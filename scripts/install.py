#!/usr/bin/env python3
"""Safe two-mode Remnaum installer, using only Python's standard library."""
from __future__ import annotations

import argparse
import getpass
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
DOMAIN_RE = re.compile(r"(?=.{4,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}")
NETWORK_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")


def valid_domain(value: str) -> bool:
    return (bool(DOMAIN_RE.fullmatch(value)) and not value.lower().endswith((".example.com", ".example.net", ".example.org", ".test", ".invalid", ".localhost")) and value.lower() not in {"example.com", "example.net", "example.org"})


def clean(value: str) -> str:
    if "\n" in value or "\r" in value or "\x00" in value:
        raise ValueError("Multiline or binary configuration values are not allowed")
    return value


def read_env(path: Path = ENV_PATH) -> dict[str, str]:
    if not path.exists():
        return {}
    result = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, raw = line.split("=", 1)
        if re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            value = raw.strip()
            if len(value) > 1 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            result[key] = value
    return result


def write_env(changes: dict[str, str], path: Path = ENV_PATH) -> None:
    """Atomic update; never evaluate an existing .env as a shell script."""
    original = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    emitted = set()
    output = []
    for line in original:
        key = line.split("=", 1)[0] if "=" in line else ""
        if key in changes:
            if key not in emitted:
                output.append(f"{key}={clean(changes[key])}")
                emitted.add(key)
        else:
            output.append(line)
    for key, value in changes.items():
        if key not in emitted:
            output.append(f"{key}={clean(value)}")
    temporary = path.with_name(".env.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write("\n".join(output) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if temporary.exists():
            temporary.unlink()


def prompt(label: str, *, default: str = "", hidden: bool = False, required: bool = True) -> str:
    suffix = f" [{default}]" if default else ""
    value = getpass.getpass(f"{label}{suffix}: ") if hidden else input(f"{label}{suffix}: ")
    value = clean(value.strip() or default)
    if required and not value:
        raise ValueError(f"{label} is required")
    return value


def run(command: list[str], *, check: bool = True, quiet: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(command, cwd=ROOT, check=check, text=True,
                          stdout=subprocess.DEVNULL if quiet else None,
                          stderr=subprocess.DEVNULL if quiet else None)


def docker_ready() -> bool:
    if not shutil.which("docker"):
        return False
    return run(["docker", "compose", "version"], check=False, quiet=True).returncode == 0


def install_docker() -> None:
    info = Path("/etc/os-release").read_text(encoding="utf-8")
    distro = re.search(r"^ID=(?:\"|')?([^\s\"']+)", info, re.MULTILINE)
    codename = re.search(r"^VERSION_CODENAME=(?:\"|')?([^\s\"']+)", info, re.MULTILINE)
    if not distro or distro.group(1) not in ("ubuntu", "debian") or not codename:
        raise RuntimeError("Automatic Docker installation only supports Ubuntu and Debian")
    distro_name, suite = distro.group(1), codename.group(1)
    print("Installing Docker Engine and Compose from Docker's official apt repository...")
    run(["apt-get", "update"])
    run(["apt-get", "install", "-y", "ca-certificates", "curl"])
    Path("/etc/apt/keyrings").mkdir(parents=True, exist_ok=True)
    run(["curl", "-fsSL", f"https://download.docker.com/linux/{distro_name}/gpg",
         "-o", "/etc/apt/keyrings/docker.asc"])
    os.chmod("/etc/apt/keyrings/docker.asc", 0o644)
    arch = subprocess.check_output(["dpkg", "--print-architecture"], text=True).strip()
    list_path = Path("/etc/apt/sources.list.d/docker.list")
    list_path.write_text(f"deb [arch={arch} signed-by=/etc/apt/keyrings/docker.asc] "
                         f"https://download.docker.com/linux/{distro_name} {suite} stable\n")
    run(["apt-get", "update"])
    run(["apt-get", "install", "-y", "docker-ce", "docker-ce-cli",
         "containerd.io", "docker-buildx-plugin", "docker-compose-plugin"])
    run(["systemctl", "enable", "--now", "docker"])
    if not docker_ready():
        raise RuntimeError("Docker Compose v2 did not become available")


def network_exists(name: str) -> bool:
    return run(["docker", "network", "inspect", name], check=False, quiet=True).returncode == 0


def public_ports_available() -> bool:
    """Warn before Docker tries to claim 80/443 on a standalone host."""
    for port, socktype in [(80, socket.SOCK_STREAM), (443, socket.SOCK_STREAM), (443, socket.SOCK_DGRAM)]:
        with socket.socket(socket.AF_INET, socktype) as sock:
            try:
                sock.bind(("0.0.0.0", port))
            except OSError:
                return False
    return True


def loopback_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def mode_files(mode: str, network: str = "") -> str:
    if mode == "standalone":
        return "compose.yaml:compose.standalone.yaml"
    if mode == "panel":
        return ("compose.yaml:compose.panel.yaml:compose.panel-network.yaml"
                if network else "compose.yaml:compose.panel.yaml")
    raise ValueError("Unsupported deployment mode")


def generate_proxy_snippets(cfg: dict[str, str]) -> None:
    """Write both Docker-proxy and host-proxy examples, without modifying Panel."""
    folder = ROOT / "generated"
    folder.mkdir(mode=0o700, exist_ok=True)
    domains_list = list(dict.fromkeys([cfg["PUBLIC_DOMAIN"], cfg["WEBAPP_DOMAIN"]]))
    caddy_domains = ", ".join(f"https://{name}" for name in domains_list)
    nginx_domains = " ".join(domains_list)
    for variant, upstream in (
        ("docker", "remnaum-api:8080"),
        ("host", f"127.0.0.1:{cfg.get('REMNAUM_LOCAL_PORT', '18080')}"),
    ):
        caddy = (
            "# Add this block to the EXISTING Panel Caddyfile; do not replace it.\n"
            f"{caddy_domains} {{\n"
            "    encode zstd gzip\n"
            f"    reverse_proxy http://{upstream}\n"
            "}\n"
        )
        nginx = (
            "# Add to the EXISTING Nginx configuration; supply valid TLS certs.\n"
            "server {\n"
            "    listen 443 ssl;\n"
            f"    server_name {nginx_domains};\n"
            "    ssl_certificate /PATH/TO/fullchain.pem;\n"
            "    ssl_certificate_key /PATH/TO/privkey.pem;\n"
            "    location / {\n"
            f"        proxy_pass http://{upstream};\n"
            "        proxy_http_version 1.1;\n"
            "        proxy_set_header Host $host;\n"
            "        proxy_set_header X-Real-IP $remote_addr;\n"
            "        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n"
            "        proxy_set_header X-Forwarded-Proto $scheme;\n"
            "    }\n"
            "}\n"
        )
        (folder / f"remnaum.{variant}.caddy").write_text(caddy, encoding="utf-8")
        (folder / f"remnaum.{variant}.nginx.conf").write_text(nginx, encoding="utf-8")
    print("Proxy examples generated in generated/ for both Docker and host proxies")


def choose_mode(existing: dict[str, str], requested: str | None) -> str:
    if requested:
        return requested
    if existing.get("DEPLOY_MODE") in ("panel", "standalone"):
        return existing["DEPLOY_MODE"]
    print("\nInstallation topology:")
    print("  1) panel      — alongside Remnawave; never bind public 80/443")
    print("  2) standalone — separate VPS; Caddy owns 80/443 and automatic HTTPS")
    selection = prompt("Choose 1 or 2")
    if selection not in ("1", "2"):
        raise ValueError("Choose installation mode 1 or 2")
    return "panel" if selection == "1" else "standalone"


def config_for_install(existing: dict[str, str], mode: str,
                       network_override: str | None, port: int) -> dict[str, str]:
    cfg = existing.copy()
    old_mode = cfg.get("DEPLOY_MODE", "")
    if not cfg:
        cfg = read_env(ROOT / ".env.example")
        cfg.update({
            "APP_SECRET_KEY": secrets.token_hex(32),
            "POSTGRES_PASSWORD": secrets.token_hex(24),
            "TELEGRAM_WEBHOOK_SECRET": secrets.token_hex(32),
            "REMNAWAVE_WEBHOOK_SECRET": secrets.token_hex(32),
            "ADMIN_API_TOKEN": "",
        })
        cfg["DATABASE_URL"] = (f"postgresql+asyncpg://remnaum:{cfg['POSTGRES_PASSWORD']}"
                               "@postgres:5432/remnaum")

    for key, label in (("PUBLIC_DOMAIN", "Telegram/Remnawave webhook domain (no https://)"),
                       ("WEBAPP_DOMAIN", "Mini App/admin domain (no https://)")):
        if not valid_domain(cfg.get(key, "")) or key not in existing:
            cfg[key] = prompt(label)
        if not valid_domain(cfg[key]):
            raise ValueError(f"Invalid {key}: use a real DNS hostname")

    if mode == "standalone":
        if not cfg.get("ACME_EMAIL") or cfg["ACME_EMAIL"].endswith("@example.com"):
            cfg["ACME_EMAIL"] = prompt("Email for Caddy ACME certificates")
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", cfg["ACME_EMAIL"]):
            raise ValueError("Invalid certificate email")
    else:
        cfg["REMNAUM_LOCAL_PORT"] = str(port)
        if network_override == "none":
            proxy_network = ""
        elif network_override:
            proxy_network = network_override
        else:
            old_network = cfg.get("REMNAUM_PROXY_NETWORK", "")
            proxy_network = old_network if old_network and network_exists(old_network) else (
                "remnawave-network" if network_exists("remnawave-network") else "")
        if proxy_network and (not NETWORK_RE.fullmatch(proxy_network) or not network_exists(proxy_network)):
            raise ValueError(f"Docker proxy network not found: {proxy_network}")
        cfg["REMNAUM_PROXY_NETWORK"] = proxy_network

    if not existing:
        cfg["TELEGRAM_BOT_USERNAME"] = prompt("Telegram bot username (without @)").lstrip("@")
        cfg["TELEGRAM_BOT_TOKEN"] = prompt("Telegram BotFather token", hidden=True)
        cfg["ADMIN_IDS"] = prompt("Numeric Telegram admin IDs (comma-separated)")
        cfg["REMNAWAVE_BASE_URL"] = prompt("Remnawave Panel URL (https://...)")
        cfg["REMNAWAVE_API_TOKEN"] = prompt("Remnawave API token", hidden=True)
        cfg["REMNAWAVE_ACTIVE_INTERNAL_SQUADS"] = prompt("Remnawave internal squad UUIDs")
        cfg["TELEGRAM_TERMS_URL"] = prompt("Public terms URL (optional for initial test)", required=False)
        cfg["SUPPORT_CONTACT"] = prompt("Support contact (optional)", required=False)
    if not re.fullmatch(r"[A-Za-z0-9_]{5,32}", cfg.get("TELEGRAM_BOT_USERNAME", "")):
        raise ValueError("Bot username must use 5-32 ASCII letters, digits or underscores")
    if not re.fullmatch(r"[0-9]+(?:,[0-9]+)*", cfg.get("ADMIN_IDS", "")):
        raise ValueError("ADMIN_IDS must be comma-separated Telegram numeric IDs")
    panel_url = urlsplit(cfg.get("REMNAWAVE_BASE_URL", ""))
    if panel_url.scheme != "https" or not panel_url.hostname:
        raise ValueError("Panel API URL must use HTTPS")
    if any(name == panel_url.hostname for name in (cfg["PUBLIC_DOMAIN"], cfg["WEBAPP_DOMAIN"])):
        raise ValueError("Do not reuse the Panel's domain for the Remnaum bot/Mini App")
    if cfg.get("TELEGRAM_TERMS_URL") and not cfg["TELEGRAM_TERMS_URL"].startswith("https://"):
        raise ValueError("Terms URL must use HTTPS")
    for required in ("TELEGRAM_BOT_TOKEN", "REMNAWAVE_API_TOKEN",
                     "REMNAWAVE_ACTIVE_INTERNAL_SQUADS"):
        if not cfg.get(required):
            raise ValueError(f"Missing required setting: {required}")

    cfg["DEPLOY_MODE"] = mode
    cfg["COMPOSE_FILE"] = mode_files(mode, cfg.get("REMNAUM_PROXY_NETWORK", "") if mode == "panel" else "")
    cfg["PUBLIC_BASE_URL"] = "https://" + cfg["WEBAPP_DOMAIN"]
    cfg["WEBAPP_URL"] = cfg["PUBLIC_BASE_URL"] + "/app"
    cfg["TELEGRAM_WEBHOOK_URL"] = "https://" + cfg["PUBLIC_DOMAIN"] + "/telegram/webhook"
    cfg["TELEGRAM_USE_WEBHOOK"] = "true"
    for key in ("APP_SECRET_KEY", "POSTGRES_PASSWORD",
                "TELEGRAM_WEBHOOK_SECRET", "REMNAWAVE_WEBHOOK_SECRET"):
        if not cfg.get(key):
            cfg[key] = secrets.token_hex(32)
    if not cfg.get("DATABASE_URL"):
        cfg["DATABASE_URL"] = (f"postgresql+asyncpg://remnaum:{cfg['POSTGRES_PASSWORD']}"
                               "@postgres:5432/remnaum")
    return cfg


def main() -> int:
    parser = argparse.ArgumentParser(description="Install Remnaum in a standalone or shared Panel deployment.")
    parser.add_argument("--mode", choices=("panel", "standalone"),
                        help="Optional topology selection (otherwise guided interactive prompt)")
    parser.add_argument("--proxy-network",
                        help="Existing Docker network of the reverse proxy, or 'none' for a host-only proxy")
    parser.add_argument("--local-port", type=int, default=None,
                        help="Panel mode: localhost-only port for host proxy, default 18080")
    args = parser.parse_args()

    if os.geteuid() != 0:
        raise RuntimeError("Run with sudo: sudo sh install.sh")
    os.chdir(ROOT)
    existing = read_env()
    mode = choose_mode(existing, args.mode)
    if existing.get("DEPLOY_MODE") and existing["DEPLOY_MODE"] != mode:
        answer = prompt(f"Switch installation from {existing['DEPLOY_MODE']} to {mode}? Type YES")
        if answer != "YES":
            print("No changes were made")
            return 0

    if not docker_ready():
        if mode == "panel":
            raise RuntimeError("Docker Compose is missing. On a Panel host, install it manually without replacing existing Docker.")
        if shutil.which("docker"):
            raise RuntimeError("Docker exists but Compose v2 is missing. Install the Compose plugin without replacing your Docker Engine.")
        install_docker()
    port = args.local_port or int(existing.get("REMNAUM_LOCAL_PORT") or "18080")
    if not 1024 <= port <= 65535:
        raise ValueError("Local app port must be between 1024 and 65535")
    if mode == "standalone" and existing.get("DEPLOY_MODE") != "standalone" and not public_ports_available():
        raise RuntimeError("80/443 are already occupied. Select '--mode panel' and reuse the existing proxy.")
    if mode == "panel" and existing.get("DEPLOY_MODE") != "panel" and not loopback_available(port):
        raise RuntimeError(f"127.0.0.1:{port} is busy; choose --local-port with a free port.")

    cfg = config_for_install(existing, mode, args.proxy_network, port)
    if not existing:
        base = ROOT / ".env.example"
        if not base.exists():
            raise RuntimeError(".env.example missing from source tree")
        write_env(cfg)
    else:
        write_env({k: value for k, value in cfg.items() if existing.get(k) != value})

    (ROOT / "data").mkdir(exist_ok=True)
    if mode == "panel":
        generate_proxy_snippets(cfg)
        print("\nPanel mode: Remnaum will NOT publish 80 or 443.")
        print(f"Host-loopback API: http://127.0.0.1:{port}/health")
        if cfg.get("REMNAUM_PROXY_NETWORK"):
            print(f"Proxy network: {cfg['REMNAUM_PROXY_NETWORK']} -> remnaum-api:8080")
        else:
            print("No shared Docker proxy network detected. Only a HOST-native proxy can reach localhost.")
            print("For a Dockerized proxy, rerun with --proxy-network YOUR_EXISTING_NETWORK.")
        print("Add the generated site block to your existing proxy, then validate/reload THAT proxy.")
    else:
        print("\nStandalone mode: Remnaum Caddy will own 80/443; Nginx stays private.")

    run(["docker", "compose", "config", "--quiet"])
    run(["docker", "compose", "up", "-d", "--build", "--remove-orphans"])
    print("\nRemnaum containers started. No demo users or payments were added.")
    print("Telegram webhook: " + cfg["TELEGRAM_WEBHOOK_URL"])
    print("Mini App: " + cfg["WEBAPP_URL"])
    print("Remnawave events: https://" + cfg["PUBLIC_DOMAIN"] + "/webhooks/remnawave")
    print("Configure the Panel's outgoing webhook separately using REMNAWAVE_WEBHOOK_SECRET from .env.")
    print("Check: docker compose ps; docker compose logs --tail=100 app")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, ValueError, OSError, subprocess.CalledProcessError, EOFError) as exc:
        print(f"Installation failed: {exc}", file=sys.stderr)
        sys.exit(1)
