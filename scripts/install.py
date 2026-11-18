#!/usr/bin/env python3
"""Safe two-mode Remnaum installer, using only Python's standard library."""
from __future__ import annotations

import argparse
import getpass
import json
import os
import pwd
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit
from remnawave_setup import (
    discover_squads, select_squads, validate_uuid_list,
    local_panel_for_url, panel_webhook_secret, apply_local_panel_webhook,
    discover_local_panel, read_panel_env, public_webhook_ready,
    panel_api_url,
)

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


def offer_docker_setup() -> None:
    """Install missing components without touching a running Docker engine."""
    if docker_ready():
        return
    if shutil.which("docker"):
        if not Path("/usr/bin/apt-get").exists() and not shutil.which("apt-get"):
            raise RuntimeError("Docker найден, но Compose v2 отсутствует. Установите Compose plugin.")
        answer = prompt("Docker есть, но нет Compose v2. Установить дополнение? (Д/н)", default="Д")
        if answer.lower() not in ("д", "да", "y", "yes"):
            raise RuntimeError("Для запуска нужен Docker Compose v2")
        run(["apt-get", "update"])
        run(["apt-get", "install", "-y", "docker-compose-plugin"])
        if not docker_ready():
            raise RuntimeError("Compose v2 не появился; существующий Docker не был заменён")
        return
    answer = prompt("Docker не найден. Установить автоматически? (Д/н)", default="Д")
    if answer.lower() not in ("д", "да", "y", "yes"):
        raise RuntimeError("Для запуска нужен Docker")
    install_docker()


def first_free_loopback_port(preferred: int) -> int:
    if loopback_available(preferred):
        return preferred
    for candidate in range(max(1024, preferred + 1), min(65535, preferred + 60)):
        if loopback_available(candidate):
            return candidate
    raise RuntimeError("Не найден свободный локальный порт в ближайшем диапазоне")


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


def caddy_site_block(cfg: dict[str, str]) -> str:
    domains = list(dict.fromkeys([cfg["PUBLIC_DOMAIN"], cfg["WEBAPP_DOMAIN"]]))
    if not all(valid_domain(domain) for domain in domains):
        raise ValueError("Проверьте домены Caddy")
    return (
        "\n# Remnaum: автоматически добавленный магазин VPN\n"
        + ", ".join("https://" + domain for domain in domains) + " {\n"
        "    encode zstd gzip\n"
        "    reverse_proxy http://remnaum-api:8080\n"
        "}\n"
    )


def offer_panel_caddy_autoconfig(cfg: dict[str, str]) -> bool:
    """Only touch the documented, unmodified Caddy bind mount with owner approval.

    Saves an original backup, validates the *live* config and rolls back failed
    Caddy reconfiguration. Other proxies are left intact.
    """
    if cfg.get("REMNAUM_PROXY_NETWORK") != "remnawave-network":
        return False
    caddyfile = Path("/opt/remnawave/caddy/Caddyfile")
    if not caddyfile.is_file() or not shutil.which("docker"):
        return False
    try:
        result = subprocess.check_output(
            ["docker", "inspect", "caddy"], text=True,
            stderr=subprocess.DEVNULL, timeout=10,
        )
        container = json.loads(result)[0]
    except (ValueError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False
    mounts = container.get("Mounts", [])
    if not (container.get("State", {}).get("Running")
            and "remnawave-network" in container.get("NetworkSettings", {}).get("Networks", {})
            and any(m.get("Destination") == "/etc/caddy/Caddyfile"
                    and Path(m.get("Source", "/")).resolve() == caddyfile.resolve()
                    and m.get("RW") is True for m in mounts)):
        return False
    previous = caddyfile.read_bytes()
    domains = [cfg["PUBLIC_DOMAIN"], cfg["WEBAPP_DOMAIN"]]
    if any(domain.encode() in previous for domain in domains):
        print("В текущем Caddyfile уже найдены домены магазина; проверьте конфигурацию вручную.")
        return False
    answer = prompt("Добавить домены в существующий Caddy Remnawave с резервной копией? (Д/н)", default="Д")
    if answer.lower() not in ("д", "да", "y", "yes"):
        return False
    import datetime
    backup = caddyfile.with_name("Caddyfile.remnaum.bak." + datetime.datetime.now().strftime("%Y%m%d%H%M%S"))
    shutil.copy2(caddyfile, backup)
    # Write in place so Docker's bind mount keeps pointing to the correct inode.
    append = caddy_site_block(cfg).encode()
    try:
        with caddyfile.open("wb") as handle:
            handle.write(previous.rstrip() + b"\n" + append)
            handle.flush()
            os.fsync(handle.fileno())
        run(["docker", "exec", "caddy", "caddy", "validate", "--config", "/etc/caddy/Caddyfile"])
        run(["docker", "exec", "caddy", "caddy", "reload", "--config", "/etc/caddy/Caddyfile"])
    except (OSError, subprocess.CalledProcessError):
        with caddyfile.open("wb") as handle:
            handle.write(previous)
            handle.flush()
            os.fsync(handle.fileno())
        run(["docker", "exec", "caddy", "caddy", "reload", "--config", "/etc/caddy/Caddyfile"], check=False)
        raise RuntimeError("Caddy отклонил новые домены. Исходная конфигурация восстановлена.")
    print(f"Прокси Remnawave настроен автоматически; копия: {backup}")
    return True


LEGACY_PAYMENT_KEYS = {
    "YOOKASSA_SHOP_ID", "YOOKASSA_SECRET_KEY",
    "YOOMONEY_RECEIVER", "YOOMONEY_NOTIFICATION_SECRET",
    "CRYPTOPAY_API_TOKEN",
}


def scrub_migrated_legacy_payment_keys(path: Path = ENV_PATH) -> bool:
    """Remove plaintext legacy provider keys only after DB verification."""
    current = read_env(path)
    if not any(current.get(key) for key in LEGACY_PAYMENT_KEYS):
        return False
    verification = run(
        ["docker", "compose", "exec", "-T", "app",
         "python", "-m", "scripts.verify_legacy_credentials"],
        check=False, quiet=True,
    )
    if verification.returncode != 0:
        print("В старом .env остались реквизиты платежей. Проверьте перенос в БД через админку.")
        return False
    original = path.read_text(encoding="utf-8").splitlines()
    filtered = [line for line in original if line.split("=", 1)[0] not in LEGACY_PAYMENT_KEYS]
    temporary = path.with_name(".env.cleanup.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write("\n".join(filtered) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if temporary.exists():
            temporary.unlink()
    run(["docker", "compose", "up", "-d", "--no-deps", "--force-recreate", "app"])
    if not wait_for_app_health():
        raise RuntimeError("Платежные настройки перенесены, но API не запустился после очистки .env")
    print("Устаревшие реквизиты платежей перенесены в БД и очищены из .env.")
    return True


def choose_mode(existing: dict[str, str], requested: str | None) -> str:
    if requested:
        return requested
    if existing.get("DEPLOY_MODE") in ("panel", "standalone"):
        return existing["DEPLOY_MODE"]
    print("\nВыберите сервер:")
    print("  1 — На сервере с Remnawave (не занимает порты 80/443)")
    print("  2 — На отдельном VPS (Caddy + HTTPS на 80/443)")
    selection = prompt("Введите 1 или 2")
    if selection not in ("1", "2"):
        raise ValueError("Выберите 1 или 2")
    return "panel" if selection == "1" else "standalone"


def choose_action(existing: dict[str, str], requested: str | None) -> str:
    if requested:
        if requested == "update" and not existing:
            raise ValueError("Сначала установите Remnaum.")
        return requested
    print("\n===== Remnaum =====")
    print("  1 — Установить / настроить")
    print("  2 — Обновить")
    choice = prompt("Выберите действие (1/2)", default="1")
    if choice not in ("1", "2"):
        raise ValueError("Выберите 1 или 2")
    if choice == "2" and not existing:
        raise ValueError("Сначала установите Remnaum.")
    return "install" if choice == "1" else "update"


def wait_for_app_health(max_attempts: int = 35) -> bool:
    check = [
        "docker", "compose", "exec", "-T", "app", "python", "-c",
        "import urllib.request; assert urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3).status == 200",
    ]
    for _ in range(max_attempts):
        if run(check, check=False, quiet=True).returncode == 0:
            return True
        time.sleep(2)
    return False


def perform_update(existing: dict[str, str]) -> int:
    if not existing.get("COMPOSE_FILE"):
        raise ValueError("Сначала выберите режим установки через пункт 1.")
    if not docker_ready():
        raise RuntimeError("Docker Compose недоступен.")
    if not shutil.which("git"):
        raise RuntimeError("Для обновления необходим git.")
    print("\n[1/3] Резервная копия базы и обновление кода")
    run(["bash", "update.sh"])
    print("[2/3] Проверка контейнеров")
    run(["docker", "compose", "ps"])
    print("[3/3] Проверка приложения")
    if not wait_for_app_health():
        raise RuntimeError("API не отвечает. Проверьте docker compose logs --tail=100 app")
    scrub_migrated_legacy_payment_keys()
    install_update_timer()
    print("\nRemnaum обновлён. База и секреты сохранены.")
    return 0




def choose_install_dir(existing: dict[str, str], requested: str | None = None,
                       *, no_relocate: bool = False) -> None:
    """On first installation copy this checkout into a chosen permanent folder.

    Never move a working installation or overwrite an unrelated directory.
    Re-exec in the new copy before touching .env, ports, or Docker volumes.
    """
    if existing or no_relocate:
        return
    chosen = requested or prompt("Папка для установки Remnaum", default="/opt/remnaum")
    path = Path(chosen).expanduser()
    if not path.is_absolute():
        raise ValueError("Путь должен быть абсолютным, например /opt/remnaum")
    target = path.resolve()
    if not re.fullmatch(r"/[A-Za-z0-9_./-]+", str(target)):
        raise ValueError("В пути используйте только латинские символы, цифры, /, - и _")
    if target == ROOT.resolve():
        return
    if target == Path("/") or str(target).startswith(("/etc/", "/bin/", "/usr/", "/dev/", "/proc/", "/sys/")):
        raise ValueError("Нельзя устанавливать проект в системный каталог")
    # Refuse existing populated paths to prevent accidental data loss or source replacement.
    if target.exists() and any(target.iterdir()):
        raise ValueError(f"Каталог {target} уже содержит данные; выберите другой путь")
    if target.is_relative_to(ROOT.resolve()):
        raise ValueError("Нельзя переносить проект внутрь самого себя")

    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.rmdir()
    ignore = shutil.ignore_patterns(".env", "data", "backups", "generated", "__pycache__", ".pytest_cache")
    shutil.copytree(ROOT, target, ignore=ignore, symlinks=True)
    # Preserve GitHub credentials and ownership for the user who originally cloned the project.
    username = os.environ.get("SUDO_USER", "")
    if username and username != "root":
        user = pwd.getpwnam(username)
        for parent, dirs, files in os.walk(target):
            os.chown(parent, user.pw_uid, user.pw_gid)
            for entry in (*dirs, *files):
                try:
                    os.lchown(os.path.join(parent, entry), user.pw_uid, user.pw_gid)
                except FileNotFoundError:
                    pass
    print(f"Проект установлен в {target}. Продолжаем настройку...")
    os.execv(sys.executable, [
        sys.executable, str(target / "scripts" / "install.py"),
        "--action", "install", "--no-relocate", *sys.argv[1:],
    ])


def install_update_timer() -> bool:
    """Install a host-side scheduler that can read the private Git remote safely."""
    if os.geteuid() != 0:
        return False
    if not shutil.which("systemctl") or not Path("/run/systemd/system").exists():
        print("Systemd недоступен: автоматические уведомления GitHub можно проверять вручную.")
        return False
    source = str(ROOT.resolve())
    username = os.environ.get("SUDO_USER", "") or pwd.getpwuid(ROOT.stat().st_uid).pw_name
    if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", username):
        username = "root"
    unit = Path("/etc/systemd/system/remnaum-update-check.service")
    timer = Path("/etc/systemd/system/remnaum-update-check.timer")
    unit.write_text(
        "[Unit]\nDescription=Remnaum GitHub update checker\n"
        "[Service]\nType=oneshot\nUser=root\n"
        f"Environment=REMNAUM_GIT_USER={username}\n"
        f"WorkingDirectory={source}\n"
        f"ExecStart=/usr/bin/python3 {source}/scripts/check_updates.py\n",
        encoding="utf-8",
    )
    timer.write_text(
        "[Unit]\nDescription=Check Remnaum updates every six hours\n"
        "[Timer]\nOnBootSec=5min\nOnUnitInactiveSec=6h\nPersistent=true\nRandomizedDelaySec=10m\n"
        "[Install]\nWantedBy=timers.target\n",
        encoding="utf-8",
    )
    os.chmod(unit, 0o644)
    os.chmod(timer, 0o644)
    try:
        run(["systemctl", "daemon-reload"])
        run(["systemctl", "enable", "--now", "remnaum-update-check.timer"])
        print("Проверка новых версий GitHub включена (раз в 6 часов).")
        return True
    except subprocess.CalledProcessError:
        print("Не удалось включить таймер GitHub; приложение продолжит работать.")
        return False

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

    for key, label in (("PUBLIC_DOMAIN", "Домен вебхуков Telegram/Remnawave (без https://)"),
                       ("WEBAPP_DOMAIN", "Домен Mini App и админки (без https://)")):
        if not valid_domain(cfg.get(key, "")) or key not in existing:
            cfg[key] = prompt(label)
        if not valid_domain(cfg[key]):
            raise ValueError(f"Invalid {key}: use a real DNS hostname")

    if mode == "standalone":
        if not cfg.get("ACME_EMAIL") or cfg["ACME_EMAIL"].endswith("@example.com"):
            cfg["ACME_EMAIL"] = prompt("Email для SSL-сертификата Caddy")
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

    # Official local Panel keeps its public domain in /opt/remnawave/.env.
    # The API token itself is not in that file and must still be supplied.
    if mode == "panel" and not cfg.get("REMNAWAVE_BASE_URL"):
        local_env = discover_local_panel()
        if local_env:
            panel_domain = read_panel_env(local_env).get("FRONT_END_DOMAIN", "").strip()
            if "://" in panel_domain:
                panel_domain = urlsplit(panel_domain).hostname or ""
            if valid_domain(panel_domain):
                cfg["REMNAWAVE_BASE_URL"] = "https://" + panel_domain
                print("✅ Адрес установленной панели найден автоматически:", cfg["REMNAWAVE_BASE_URL"])

    required = (
        ("TELEGRAM_BOT_USERNAME", "Имя бота из BotFather (без @)", False),
        ("TELEGRAM_BOT_TOKEN", "Токен Telegram-бота", True),
        ("ADMIN_IDS", "Telegram ID администраторов (через запятую)", False),
        ("REMNAWAVE_BASE_URL", "Адрес панели Remnawave (с https://)", False),
        ("REMNAWAVE_API_TOKEN", "API-токен Remnawave", True),
    )
    for key, label, hidden in required:
        if not cfg.get(key):
            cfg[key] = prompt(label, hidden=hidden)
    # Never ask for UUIDs when the panel can provide its Internal Squads.
    if not cfg.get("REMNAWAVE_ACTIVE_INTERNAL_SQUADS"):
        print("\\n🔎 Получаем внутренние группы Remnawave через API...")
        try:
            squads = discover_squads(cfg["REMNAWAVE_BASE_URL"], cfg["REMNAWAVE_API_TOKEN"])
        except (OSError, ValueError):
            # Never print full HTTP errors: endpoints might include user credentials.
            print("Не удалось получить группы автоматически. Проверьте API-токен и доступность панели.")
            manual = prompt("UUID внутренней группы (резервный вариант, через запятую)")
            cfg["REMNAWAVE_ACTIVE_INTERNAL_SQUADS"] = validate_uuid_list(manual)
        else:
            # A successful API response with zero squads is not an outage.
            # Do not encourage entering an unrelated UUID.
            cfg["REMNAWAVE_ACTIVE_INTERNAL_SQUADS"] = select_squads(squads, prompt)
    # On a shared host reuse the already configured secret so other Panel
    # webhook consumers are not disconnected or made incompatible.
    panel_env = local_panel_for_url(cfg["REMNAWAVE_BASE_URL"]) if mode == "panel" else None
    if panel_env is not None:
        previous = panel_webhook_secret(panel_env)
        if previous and previous != cfg.get("REMNAWAVE_WEBHOOK_SECRET"):
            allowed = not existing.get("REMNAWAVE_WEBHOOK_SECRET") or prompt(
                "Синхронизировать секрет вебхука из локальной панели? (Д/н)",
                default="Д",
            ).lower() in ("д", "да", "y", "yes")
            if allowed:
                cfg["REMNAWAVE_WEBHOOK_SECRET"] = previous
                print("✅ Секрет вебхука синхронизирован с локальной панелью")
    # Legal terms, support, tariffs and payment providers are configured in the admin DB.
    cfg["TELEGRAM_BOT_USERNAME"] = cfg["TELEGRAM_BOT_USERNAME"].lstrip("@")
    if not re.fullmatch(r"[A-Za-z0-9_]{5,32}", cfg.get("TELEGRAM_BOT_USERNAME", "")):
        raise ValueError("Bot username must use 5-32 ASCII letters, digits or underscores")
    if not re.fullmatch(r"[0-9]+(?:,[0-9]+)*", cfg.get("ADMIN_IDS", "")):
        raise ValueError("ADMIN_IDS must be comma-separated Telegram numeric IDs")
    panel_url = urlsplit(cfg.get("REMNAWAVE_BASE_URL", ""))
    panel_api_url(cfg.get("REMNAWAVE_BASE_URL", ""))
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
    parser = argparse.ArgumentParser(description="Мастер установки и обновления Remnaum.")
    parser.add_argument("--action", choices=("install", "update"), help="Выбор без меню")
    parser.add_argument("--mode", choices=("panel", "standalone"),
                        help="Optional topology selection (otherwise guided interactive prompt)")
    parser.add_argument("--proxy-network",
                        help="Existing Docker network of the reverse proxy, or 'none' for a host-only proxy")
    parser.add_argument("--install-dir", default=None, help="Абсолютный путь для установки")
    parser.add_argument("--no-relocate", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--local-port", type=int, default=None,
                        help="Panel mode: localhost-only port for host proxy, default 18080")
    args = parser.parse_args()

    if os.geteuid() != 0:
        raise RuntimeError("Run with sudo: sudo sh install.sh")
    os.chdir(ROOT)
    existing = read_env()
    action = choose_action(existing, args.action)
    if action == "update":
        return perform_update(existing)
    choose_install_dir(existing, args.install_dir, no_relocate=args.no_relocate)
    print("\n[1/4] Способ установки")
    mode = choose_mode(existing, args.mode)
    if existing.get("DEPLOY_MODE") and existing["DEPLOY_MODE"] != mode:
        raise ValueError(
            "Нельзя автоматически менять режим действующей установки: сначала перенесите прокси "
            "и проверьте сохранность БД. Текущий режим: " + existing["DEPLOY_MODE"]
        )

    print("\n[2/4] Проверяем Docker и порты")
    offer_docker_setup()
    port = args.local_port or int(existing.get("REMNAUM_LOCAL_PORT") or "18080")
    if not 1024 <= port <= 65535:
        raise ValueError("Local app port must be between 1024 and 65535")
    if mode == "standalone" and existing.get("DEPLOY_MODE") != "standalone" and not public_ports_available():
        print("Порты 80/443 заняты существующим прокси.")
        answer = prompt("Поставить Remnaum рядом с ним без конфликта портов? (Д/н)", default="Д")
        if answer.lower() in ("д", "да", "y", "yes"):
            mode = "panel"
        else:
            raise RuntimeError("Освободите публичные 80/443 или используйте режим совместного прокси.")
    if mode == "panel" and existing.get("DEPLOY_MODE") != "panel":
        chosen_port = first_free_loopback_port(port)
        if chosen_port != port:
            print(f"Порт {port} занят. Выбран свободный 127.0.0.1:{chosen_port}.")
        port = chosen_port

    print("\n[3/4] Настройки магазина")
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

    print("\n[4/4] Сборка и запуск контейнеров")
    run(["docker", "compose", "config", "--quiet"])
    run(["docker", "compose", "up", "-d", "--build"])
    if not wait_for_app_health():
        raise RuntimeError("API не отвечает. Проверьте docker compose logs --tail=100 app")
    if mode == "panel":
        if not offer_panel_caddy_autoconfig(cfg):
            print("Если прокси не настроен автоматически, используйте примеры в generated/.")
        panel_env = local_panel_for_url(cfg["REMNAWAVE_BASE_URL"])
        if panel_env is not None:
            webhook_url = "https://" + cfg["PUBLIC_DOMAIN"] + "/webhooks/remnawave"
            if not public_webhook_ready(webhook_url):
                print("Публичный HTTPS ещё не отвечает: вебхук панели не изменяем.")
                print("Когда DNS/прокси заработают, снова запустите install.sh → 1.")
            else:
                print("\\n🔗 Подключаем исходящие события Remnawave...")
                try:
                    applied = apply_local_panel_webhook(
                        panel_env, webhook_url, cfg["REMNAWAVE_WEBHOOK_SECRET"], prompt,
                    )
                    if not applied:
                        print("Настройка панели пропущена. Данные для подключения есть в .env.")
                except (ValueError, OSError, subprocess.CalledProcessError):
                    print("Не удалось обновить вебхук панели. Её прежние настройки сохранены.")
                    print("Проверьте состояние панели и повторите настройку.")
        else:
            print("Локальная панель не совпадает с адресом API; её настройки не меняем.")
    install_update_timer()
    print("\nRemnaum запущен. Тестовые данные не создавались.")
    print("Telegram webhook: " + cfg["TELEGRAM_WEBHOOK_URL"])
    print("Mini App: " + cfg["WEBAPP_URL"])
    print("Remnawave events: https://" + cfg["PUBLIC_DOMAIN"] + "/webhooks/remnawave")
    if mode != "panel" or local_panel_for_url(cfg["REMNAWAVE_BASE_URL"]) is None:
        print("Для удалённой панели параметры вебхука находятся в .env — настройте исходящие события на сервере панели.")
    print("Check: docker compose ps; docker compose logs --tail=100 app")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, ValueError, OSError, subprocess.CalledProcessError, EOFError) as exc:
        print(f"Installation failed: {exc}", file=sys.stderr)
        sys.exit(1)
