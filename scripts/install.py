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
from urllib.error import HTTPError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen
import terminal_ui as ui
import backup_manager as backups
from release_version import BUILD_VERSION, git_version
from remnawave_setup import (
    local_panel_for_url, panel_webhook_secret, apply_local_panel_webhook,
    discover_local_panel, read_panel_env, public_webhook_ready,
    panel_api_url,
)

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
DOMAIN_RE = re.compile(r"(?=.{4,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}")
NETWORK_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
BOT_TOKEN_RE = re.compile(r"[0-9]{5,20}:[A-Za-z0-9_-]{20,}")


def identify_telegram_bot(token: str) -> tuple[str, str] | None:
    """Read BotFather's actual @username and display name using getMe.

    Token stays in memory; a failed request is never printed or logged.
    """
    if not BOT_TOKEN_RE.fullmatch(token):
        return None
    try:
        request = Request(
            "https://api.telegram.org/bot" + token + "/getMe",
            headers={"Accept": "application/json", "User-Agent": "Remnaum-Installer"},
        )
        with urlopen(request, timeout=8) as response:
            result = json.load(response)
        bot = result.get("result", {}) if result.get("ok") else {}
        username = bot.get("username")
        name = bot.get("first_name")
        if not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9_]{5,32}", username):
            return None
        return username, str(name or username)[:80]
    except HTTPError as exc:
        if exc.code in (401, 404):
            raise ValueError("Telegram отклонил токен. Получите действующий токен в BotFather.") from None
        return None
    except (OSError, ValueError, KeyError, TypeError):
        return None


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
    backups._secure_write(path, ("\n".join(output) + "\n").encode())


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


def public_domains(cfg: dict[str, str]) -> list[str]:
    return list(dict.fromkeys(cfg[key] for key in ("PUBLIC_DOMAIN", "WEBAPP_DOMAIN", "BROWSER_DOMAIN") if cfg.get(key)))


def generate_proxy_snippets(cfg: dict[str, str]) -> None:
    """Write both Docker-proxy and host-proxy examples, without modifying Panel."""
    folder = ROOT / "generated"
    folder.mkdir(mode=0o700, exist_ok=True)
    domains_list = public_domains(cfg)
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


def caddy_site_block(cfg: dict[str, str], *, domains: list[str] | None = None) -> str:
    domains = public_domains(cfg) if domains is None else domains
    if not domains or not all(valid_domain(domain) for domain in domains):
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
    domains = public_domains(cfg)
    configured = previous.decode("utf-8", errors="replace")
    existing_domains = {domain for domain in domains if re.search(
        rf"(?<![\w.-]){re.escape(domain)}(?![\w.-])", configured, re.IGNORECASE,
    )}
    domains = [domain for domain in domains if domain not in existing_domains]
    if not domains:
        return False
    answer = prompt("Добавить домены в существующий Caddy Remnawave с резервной копией? (Д/н)", default="Д")
    if answer.lower() not in ("д", "да", "y", "yes"):
        return False
    import datetime
    backup = caddyfile.with_name("Caddyfile.remnaum.bak." + datetime.datetime.now().strftime("%Y%m%d%H%M%S"))
    shutil.copy2(caddyfile, backup)
    # Write in place so Docker's bind mount keeps pointing to the correct inode.
    append = caddy_site_block(cfg, domains=domains).encode()
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


def install_global_command(destination: Path = Path("/usr/local/bin/remnaum")) -> None:
    """Keep one safe launcher pointing to the actual installation directory."""
    if os.geteuid() != 0:
        return
    location = str(ROOT.resolve())
    if not re.fullmatch(r"/[A-Za-z0-9_./-]+", location):
        ui.warning("Путь содержит необычные символы; глобальная команда не настроена")
        return
    path = destination
    wrapper = (
        "#!/bin/sh\n"
        "set -eu\n"
        "if [ \"$(id -u)\" -ne 0 ]; then exec sudo /usr/local/bin/remnaum \"$@\"; fi\n"
        f"cd {location}\n"
        "exec sh ./install.sh \"$@\"\n"
    )
    temporary = path.with_name(".remnaum-launcher.tmp")
    temporary.write_text(wrapper, encoding="utf-8")
    os.chmod(temporary, 0o755)
    os.replace(temporary, path)
    ui.ok("Команда remnaum работает из любой папки")


def offer_backup_key() -> None:
    """Owners can set a recovery key or accept a strong generated one."""
    if backups.KEY.exists():
        return
    ui.section(5, 5, "Защита резервных копий")
    print("  Резервные копии будут зашифрованы. Ключ понадобится при восстановлении на другом сервере.")
    answer = prompt("Установить свой ключ восстановления? (д/Н)", default="Н")
    if answer.lower() in ("д", "да", "y", "yes"):
        secret = prompt("Ключ восстановления (минимум 24 символа)", hidden=True)
        if len(secret) < 24:
            raise ValueError("Ключ восстановления должен содержать минимум 24 символа")
        confirmation = prompt("Повторите ключ", hidden=True)
        if secret != confirmation:
            raise ValueError("Ключи не совпадают")
        backups._secure_write(backups.KEY, secret.encode() + b"\n")
        ui.ok("Ваш собственный ключ восстановления сохранён с правами 600")
    else:
        backups.key_bytes(create=True)


def show_backup_menu(existing: dict[str, str]) -> int:
    global ROOT
    ui.header("Полные копии · Восстановление · Расписание")
    if not existing:
        print("  Магазин ещё не настроен. Можно восстановить существующий архив.")
        choice = "2"
    else:
        ui.menu("Резервное копирование", [
            ("1", "Создать полную копию сейчас"),
            ("2", "Восстановить архив"),
            ("3", "Настроить ежедневные / ежечасные копии"),
            ("4", "Показать сохранённые архивы"),
            ("0", "Вернуться"),
        ])
        choice = prompt("Выберите действие", default="1")
    if choice == "0":
        return 0
    if choice == "1":
        ui.section(1, 2, "Снимаем согласованный снимок PostgreSQL и Redis")
        result = backups.create_backup()
        ui.section(2, 2, "Шифрование завершено")
        ui.ok("Сохранено в " + str(result))
    elif choice == "2":
        if existing:
            archives = sorted(backups.BACKUPS.glob("remnaum-full-*.rmb"), reverse=True)
            for number, item in enumerate(archives[:10], 1):
                print(f"  {number}. {item.name}")
        archive_name = prompt("Полный путь к файлу .rmb")
        archive = Path(archive_name).expanduser().resolve()
        if archive.suffix != ".rmb":
            raise ValueError("Выберите зашифрованный архив Remnaum (.rmb)")
        secret = None
        if not backups.KEY.exists():
            secret = prompt("Ключ восстановления с прежнего сервера", hidden=True)
        try:
            info = backups.inspect_backup(archive, supplied_key=secret)
        except ValueError:
            # On a fresh installation, the default generated key may belong to
            # a different server. Ask instead of making recovery impossible.
            if not backups.KEY.exists():
                raise
            secret = prompt("Этот архив создан с другим ключом. Введите прежний ключ", hidden=True)
            info = backups.inspect_backup(archive, supplied_key=secret)
        print(f"  Архив создан: {info['created_utc']}")
        print(f"  Исходная папка: {info['original_path']}")
        print(f"  Текущая папка: {ROOT}")
        target = ROOT.resolve()
        original = Path(str(info["original_path"])).resolve()
        if original != target and not existing:
            answer = prompt("Восстановить в исходную папку " + str(original) + "? (Д/н)", default="Д")
            if answer.lower() in ("д", "да", "y", "yes"):
                if original.exists() and any(original.iterdir()):
                    raise ValueError("Исходная папка уже содержит данные. Выберите её при новой установке или освободите вручную.")
                target = original
        elif original != target:
            ui.warning("Действующая установка будет восстановлена в текущую папку, не в путь архива.")
        print("  Восстановление заменит базу и настройки текущего магазина!")
        if prompt("Для подтверждения введите ВОССТАНОВИТЬ", required=True) != "ВОССТАНОВИТЬ":
            ui.warning("Восстановление отменено")
            return 0
        if existing:
            print("  Сохраняем аварийную копию текущего состояния...")
            backups.create_backup(protected=archive)
        ui.section(1, 2, "Проверяем архив и восстанавливаем данные")
        backups.restore_backup(archive, supplied_key=secret, target_root=target)
        if target != ROOT.resolve():
            # This process is exiting; rebase only the installers/schedulers
            # so the restored instance becomes the global one.
            ROOT = target
            backups.ROOT = target
            backups.DATA = target / "data"
            backups.SETTINGS = target / "data" / "backup-settings.json"
            backups.KEY = target / "backups" / ".recovery-key"
            backups.BACKUPS = target / "backups"
        if secret and not backups.KEY.exists():
            backups._secure_write(backups.KEY, secret.encode() + b"\n")
        ui.section(2, 2, "Восстановление завершено")
        install_global_command()
        backups.configure_timer()
        disable_update_timer()
        ui.ok("Remnaum восстановлен. Проверьте DNS и подключение к панели.")
    elif choice == "3":
        ui.menu("Как часто создавать резервные копии?", [
            ("1", "Каждый час"),
            ("2", "Каждый день"),
            ("3", "Отключить автоматические копии"),
        ])
        period = prompt("Период", default="2")
        schedules = {"1": "hourly", "2": "daily", "3": "off"}
        if period not in schedules:
            raise ValueError("Выберите 1, 2 или 3")
        keep = int(prompt("Сколько последних копий хранить", default="7"))
        send = prompt("Отправлять зашифрованные архивы в Telegram? (д/Н)", default="Н").lower() in (
            "д", "да", "y", "yes",
        )
        chat_id = ""
        if send:
            admins = existing.get("ADMIN_IDS", "").split(",")
            default_chat = admins[0].strip() if admins else ""
            chat_id = prompt("Telegram chat ID получателя", default=default_chat)
            print("  Сначала напишите своему боту /start. Ключ восстановления в Telegram НЕ отправляется.")
        if not backups.KEY.exists():
            offer_backup_key()
        backups.save_settings({
            "schedule": schedules[period], "keep": keep, "telegram": send, "chat_id": chat_id,
        })
        backups.configure_timer()
        ui.ok("Параметры резервного копирования сохранены")
    elif choice == "4":
        archives = sorted(backups.BACKUPS.glob("remnaum-full-*.rmb"), reverse=True)
        if not archives:
            ui.warning("Пока нет резервных копий")
        for archive in archives[:30]:
            mb = archive.stat().st_size / 1048576
            print(f"  • {archive.name} — {mb:.1f} МиБ")
    else:
        raise ValueError("Неизвестный пункт меню")
    return 0


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
        if requested in ("update", "password") and not existing:
            raise ValueError("Сначала установите Remnaum.")
        return requested
    ui.header()
    options = [
        ("1", "Установить / настроить"),
        ("2", "Обновить до новой версии"),
        ("3", "Резервные копии и восстановление"),
    ]
    actions = {"1": "install", "2": "update", "3": "backup"}
    if existing:
        options.append(("4", "Сменить пароль PostgreSQL"))
        actions["4"] = "password"
    ui.menu("Выберите действие", options)
    choice = prompt("Ваш выбор", default="1")
    if choice not in actions:
        raise ValueError("Выберите действие из меню")
    if choice == "2" and not existing:
        raise ValueError("Сначала установите Remnaum.")
    return actions[choice]


def wait_for_app_health(max_attempts: int = 35) -> bool:
    check = [
        "docker", "compose", "exec", "-T", "app", "python", "-c",
        "import urllib.request; assert urllib.request.urlopen('http://127.0.0.1:8080/ready', timeout=4).status == 200",
    ]
    for _ in range(max_attempts):
        if run(check, check=False, quiet=True).returncode == 0:
            return True
        time.sleep(2)
    return False


def validate_postgres_password(password: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9._~-]{12,128}", password):
        raise ValueError("Пароль PostgreSQL: 12–128 символов, латиница, цифры и . _ ~ -")


def local_postgres_url(current: str, password: str) -> str:
    url = urlsplit(current)
    if (url.scheme != "postgresql+asyncpg" or url.username != "remnaum"
            or url.hostname != "postgres" or url.port not in (None, 5432)
            or url.path != "/remnaum" or url.query or url.fragment):
        raise ValueError("Смена пароля поддерживает штатную базу Remnaum в Docker Compose")
    address = url.netloc.rsplit("@", 1)[-1]
    return urlunsplit(url._replace(netloc=f"remnaum:{password}@{address}"))


def rotate_postgres_password(password: str, *, path: Path = ENV_PATH) -> None:
    validate_postgres_password(password)
    # Serialize with backup and restore so an archive cannot capture the
    # configuration midway through a password change.
    with backups._lock(".deploy.lock"), backups._lock():
        existing = read_env(path)
        if not existing.get("COMPOSE_FILE"):
            raise ValueError("Сначала настройте Remnaum через пункт 1")
        database_url = local_postgres_url(existing.get("DATABASE_URL", ""), password)
        original_env = path.read_bytes()
        backups.prepare_branding_directory(ROOT)
        run(["docker", "compose", "start", "postgres"])
        backups.wait_for_postgres(ROOT)
        # Save the actual verifier, even if the previous .env password was wrong.
        verifier = backups.postgres_admin("SELECT COALESCE(rolpassword, '') FROM pg_authid WHERE rolname = 'remnaum';\n")
        old_password = "'" + verifier.replace("'", "''") + "'" if verifier else "NULL"
        recreate_app = ["docker", "compose", "up", "-d", "--no-deps", "--force-recreate", "app"]
        replacing_app = False
        try:
            write_env({"POSTGRES_PASSWORD": password, "DATABASE_URL": database_url}, path)
            backups.set_postgres_password(password)
            run(["docker", "compose", "run", "--rm", "--no-deps", "-T", "--entrypoint", "python",
                 "app", "-m", "app.db.check_connection"])
            replacing_app = True
            run(recreate_app)
            if not wait_for_app_health():
                raise RuntimeError("Приложение не прошло проверку готовности после смены пароля")
        except (Exception, KeyboardInterrupt):
            try:
                backups.postgres_admin(f"SET standard_conforming_strings = on;\nALTER ROLE remnaum PASSWORD {old_password};\n")
                backups._secure_write(path, original_env)
                if replacing_app:
                    run(recreate_app)
            except (Exception, KeyboardInterrupt):
                raise RuntimeError("Откат пароля не завершён. Повторите смену пароля для восстановления доступа.") from None
            raise


def check_installer_postgres_connection() -> tuple[bool, bool]:
    """Probe the actual PostgreSQL credentials, not pg_isready alone.

    Never print subprocess output: external error messages may contain secrets.
    The helper returns (connected, bad_password), nothing sensitive.
    """
    result = subprocess.run(
        ["docker", "compose", "run", "--rm", "--no-deps", "-T", "--entrypoint",
         "python", "app", "-m", "app.db.check_connection"],
        cwd=ROOT, capture_output=True, text=True, timeout=60,
    )
    return result.returncode == 0, (
        "PostgreSQL отклонил пароль из DATABASE_URL" in result.stderr
    )


def ensure_install_postgres_connection(cfg: dict[str, str]) -> None:
    """Protect a surviving PostgreSQL volume during a fresh source reinstall.

    The database volume can survive deletion of /opt/remnaum. Never remove it,
    silently change role credentials, or start an app with an unverified login.
    """
    # Existing volumes are preserved by compose up; start only the DB before app.
    run(["docker", "compose", "build", "app"])
    run(["docker", "compose", "up", "-d", "--no-deps", "postgres"])
    backups.wait_for_postgres(ROOT)
    connected, bad_password = check_installer_postgres_connection()
    if connected:
        return
    if not bad_password:
        raise RuntimeError(
            "PostgreSQL не принял подключение приложения. Проверьте DATABASE_URL, "
            "логи postgres и сеть Docker. Контейнер приложения не заменён."
        )
    # A re-created .env does not imply an empty database. Encryption secrets
    # from the old installation may also be needed for existing store settings.
    print(
        "\n⚠️ Найдена база PostgreSQL со старым паролем. "
        "Удаление папки Remnaum не удаляет Docker volumes.\n"
        "Данные сохранятся, но зашифрованные настройки прежнего магазина могут "
        "потребовать старый APP_SECRET_KEY или резервную копию."
    )
    answer = prompt(
        "Установить пароль из новой .env для существующей роли PostgreSQL? (д/Н)",
        default="Н",
    ).lower()
    if answer not in ("д", "да", "y", "yes"):
        raise RuntimeError(
            "Установка остановлена без изменения пароля или данных PostgreSQL. "
            "Восстановите прежнюю .env/бэкап либо запустите remnaum и выберите "
            "пункт 4 для смены пароля."
        )
    password = cfg["POSTGRES_PASSWORD"]
    validate_postgres_password(password)
    local_postgres_url(cfg["DATABASE_URL"], password)
    with backups._lock(".deploy.lock"), backups._lock():
        # Take the old verifier before touching the password, to roll back
        # even if the original plaintext password is already lost.
        verifier = backups.postgres_admin(
            "SELECT COALESCE(rolpassword, '') FROM pg_authid WHERE rolname = 'remnaum';\n"
        )
        if not verifier:
            raise RuntimeError("Роль remnaum не найдена; старую базу не изменяли.")
        try:
            backups.set_postgres_password(password)
            connected, _ = check_installer_postgres_connection()
            if not connected:
                raise RuntimeError(
                    "Проверка PostgreSQL после смены пароля не прошла; "
                    "прежний пароль будет восстановлен."
                )
        except (Exception, KeyboardInterrupt):
            old_verifier = "'" + verifier.replace("'", "''") + "'"
            try:
                backups.postgres_admin(
                    "SET standard_conforming_strings = on;\n"
                    f"ALTER ROLE remnaum PASSWORD {old_verifier};\n"
                )
            except Exception:
                raise RuntimeError(
                    "Не удалось откатить пароль PostgreSQL. Сохраните volume и "
                    "восстановите пароль вручную; данные не удалены."
                ) from None
            raise
    print("✅ Пароль роли PostgreSQL синхронизирован с .env без удаления данных.")


def change_postgres_password() -> None:
    password = prompt("Новый пароль PostgreSQL", hidden=True)
    validate_postgres_password(password)
    if password != prompt("Повторите новый пароль PostgreSQL", hidden=True):
        raise ValueError("Пароли не совпадают")
    rotate_postgres_password(password)
    ui.ok("Пароль PostgreSQL обновлён. Приложение подключилось к существующей базе.")


def refresh_all_remnawave_squads(existing: dict[str, str]) -> bool:
    """Compatibility hook for update.sh running from an older checkout."""
    return False


def perform_update(existing: dict[str, str]) -> int:
    if not existing.get("COMPOSE_FILE"):
        raise ValueError("Сначала выберите режим установки через пункт 1.")
    if not docker_ready():
        raise RuntimeError("Docker Compose недоступен.")
    if not shutil.which("git"):
        raise RuntimeError("Для обновления необходим git.")
    ui.section(1, 3, "Создание резервной копии и загрузка обновлений")
    run(["bash", "update.sh"])
    ui.section(2, 3, "Проверка контейнеров")
    run(["docker", "compose", "ps"])
    ui.section(3, 3, "Проверка приложения")
    if not wait_for_app_health():
        raise RuntimeError("API не отвечает. Проверьте docker compose logs --tail=100 app")
    scrub_migrated_legacy_payment_keys()
    install_global_command()
    backups.configure_timer()
    disable_update_timer()
    ui.ok("Remnaum обновлён. База, секреты и резервные копии сохранены.")
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


def disable_update_timer() -> None:
    """Remove the legacy six-hour schedule after the app takes over."""
    timer = Path("/etc/systemd/system/remnaum-update-check.timer")
    if os.geteuid() == 0 and timer.exists() and Path("/run/systemd/system").exists() and shutil.which("systemctl"):
        run(["systemctl", "disable", "--now", timer.name], check=False, quiet=True)


def prompt_existing_setting(label: str, current: str, *, hidden: bool = False) -> str:
    """Empty input keeps an installed value; never reveal secrets in terminal history."""
    if hidden:
        print(f"  {label}: {'настроено' if current else 'не настроено'}")
        value = prompt(f"{label} (Enter — оставить прежнее)", hidden=True, required=False)
    else:
        value = prompt(f"{label} (Enter — оставить прежнее)", default=current, required=False)
    return value or current


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
        print("  База данных: remnaum. Надёжный пароль будет сгенерирован автоматически.")
        user_password = prompt("Свой пароль PostgreSQL? Введите пароль или нажмите Enter для автоматического", required=False, hidden=True)
        if user_password:
            validate_postgres_password(user_password)
            cfg["POSTGRES_PASSWORD"] = user_password
        cfg["DATABASE_URL"] = (f"postgresql+asyncpg://remnaum:{cfg['POSTGRES_PASSWORD']}"
                               "@postgres:5432/remnaum")

    public = existing.get("PUBLIC_DOMAIN", "").strip().lower()
    if existing:
        public = prompt_existing_setting("Домен вебхуков", public).strip().lower()
    if not valid_domain(public):
        public = prompt("Домен вебхуков, например webhook.ваш-домен (без https://)").lower()
    if not valid_domain(public):
        raise ValueError("Укажите действительный DNS-адрес для вебхуков.")

    # Derive defaults from a conventional webhook name without guessing a public suffix.
    label, _, zone = public.partition(".")
    if label not in {"webhook", "hook", "hooks"} or not valid_domain(zone):
        zone = public
    cfg["PUBLIC_DOMAIN"] = public
    used = {public}
    for key, label, prefix in (
        ("WEBAPP_DOMAIN", "Домен Telegram Mini App (без https://)", "miniapp"),
        ("BROWSER_DOMAIN", "Поддомен браузерной версии (без https://)", "web"),
    ):
        address = existing.get(key, "").strip().lower()
        if existing:
            address = prompt_existing_setting(label, address).strip().lower()
        if not valid_domain(address) or address in used:
            default = f"{prefix}.{zone}"
            if default in used:
                default = f"browser.{zone}"
            address = prompt(label, default=default if valid_domain(default) else "").lower()
        if not valid_domain(address) or address in used:
            raise ValueError("Укажите отдельный действительный DNS-адрес для " + label.split(" (")[0].lower() + ".")
        cfg[key] = address
        used.add(address)

    if mode == "standalone":
        if existing:
            cfg["ACME_EMAIL"] = prompt_existing_setting("Email SSL-сертификата", cfg.get("ACME_EMAIL", ""))
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

    if existing:
        previous_token = cfg.get("TELEGRAM_BOT_TOKEN", "")
        cfg["TELEGRAM_BOT_TOKEN"] = prompt_existing_setting(
            "Токен Telegram-бота", previous_token, hidden=True,
        )
        if not BOT_TOKEN_RE.fullmatch(cfg["TELEGRAM_BOT_TOKEN"]):
            raise ValueError("Неверный формат токена Telegram.")
        if cfg["TELEGRAM_BOT_TOKEN"] != previous_token:
            # The old username belongs to the old bot and must not be retained.
            cfg["TELEGRAM_BOT_USERNAME"] = ""
    if not cfg.get("TELEGRAM_BOT_TOKEN"):
        cfg["TELEGRAM_BOT_TOKEN"] = prompt("Токен Telegram-бота из BotFather", hidden=True)
        if not BOT_TOKEN_RE.fullmatch(cfg["TELEGRAM_BOT_TOKEN"]):
            raise ValueError("Неверный формат токена Telegram. Проверьте токен из BotFather.")
    if existing and cfg.get("TELEGRAM_BOT_USERNAME"):
        cfg["TELEGRAM_BOT_USERNAME"] = prompt_existing_setting(
            "Username бота (без @)", cfg["TELEGRAM_BOT_USERNAME"],
        ).lstrip("@")
    if not cfg.get("TELEGRAM_BOT_USERNAME"):
        info = identify_telegram_bot(cfg["TELEGRAM_BOT_TOKEN"])
        if info is not None:
            cfg["TELEGRAM_BOT_USERNAME"] = info[0]
            print(f"✅ BotFather: {info[1]} (@{info[0]})")
        else:
            cfg["TELEGRAM_BOT_USERNAME"] = prompt("Username бота без @ (Telegram API недоступен)")
    required = (
        ("ADMIN_IDS", "Telegram ID администраторов (через запятую)", False),
        ("REMNAWAVE_BASE_URL", "Адрес панели Remnawave (с https://)", False),
        ("REMNAWAVE_API_TOKEN", "API-токен Remnawave", True),
    )
    for key, label, hidden in required:
        if existing:
            cfg[key] = prompt_existing_setting(label, cfg.get(key, ""), hidden=hidden)
        if not cfg.get(key):
            cfg[key] = prompt(label, hidden=hidden)
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
    if panel_url.hostname in public_domains(cfg):
        raise ValueError("Do not reuse the Panel's domain for the Remnaum bot/Mini App")
    if cfg.get("TELEGRAM_TERMS_URL") and not cfg["TELEGRAM_TERMS_URL"].startswith("https://"):
        raise ValueError("Terms URL must use HTTPS")
    for required in ("TELEGRAM_BOT_TOKEN", "REMNAWAVE_API_TOKEN"):
        if not cfg.get(required):
            raise ValueError(f"Missing required setting: {required}")

    cfg["DEPLOY_MODE"] = mode
    cfg["COMPOSE_FILE"] = mode_files(mode, cfg.get("REMNAUM_PROXY_NETWORK", "") if mode == "panel" else "")
    cfg["WEBAPP_URL"] = "https://" + cfg["WEBAPP_DOMAIN"]
    cfg["BROWSER_WEBAPP_URL"] = "https://" + cfg["BROWSER_DOMAIN"]
    cfg["PUBLIC_BASE_URL"] = cfg["BROWSER_WEBAPP_URL"]
    cfg["TELEGRAM_WEBHOOK_URL"] = "https://" + cfg["PUBLIC_DOMAIN"] + "/telegram/webhook"
    cfg["TELEGRAM_USE_WEBHOOK"] = "true"
    for key in ("APP_SECRET_KEY", "POSTGRES_PASSWORD",
                "TELEGRAM_WEBHOOK_SECRET", "REMNAWAVE_WEBHOOK_SECRET", "GITHUB_RELEASE_WEBHOOK_SECRET"):
        if not cfg.get(key):
            cfg[key] = secrets.token_hex(32)
    if not cfg.get("DATABASE_URL"):
        cfg["DATABASE_URL"] = (f"postgresql+asyncpg://remnaum:{cfg['POSTGRES_PASSWORD']}"
                               "@postgres:5432/remnaum")
    return cfg


def main() -> int:
    parser = argparse.ArgumentParser(description="Мастер установки и обновления Remnaum.")
    parser.add_argument("--action", choices=("install", "update", "backup", "password"), help="Выбор без меню")
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
    if action == "password":
        change_postgres_password()
        return 0
    if action == "backup":
        return show_backup_menu(existing)
    choose_install_dir(existing, args.install_dir, no_relocate=args.no_relocate)
    ui.section(1, 5, "Режим установки")
    mode = choose_mode(existing, args.mode)
    if existing.get("DEPLOY_MODE") and existing["DEPLOY_MODE"] != mode:
        raise ValueError(
            "Нельзя автоматически менять режим действующей установки: сначала перенесите прокси "
            "и проверьте сохранность БД. Текущий режим: " + existing["DEPLOY_MODE"]
        )

    ui.section(2, 5, "Docker и свободные порты")
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

    ui.section(3, 5, "Настройки Remnaum")
    if existing:
        print("  Remnaum уже установлен. Enter — оставить прежнее значение; новый ввод — заменить.")
        print("  Секретные значения не показываются. База данных и подписки сохраняются.")
    cfg = config_for_install(existing, mode, args.proxy_network, port)
    offer_backup_key()
    if not existing:
        base = ROOT / ".env.example"
        if not base.exists():
            raise RuntimeError(".env.example missing from source tree")
        write_env(cfg)
    else:
        write_env({k: value for k, value in cfg.items() if existing.get(k) != value})

    backups.prepare_branding_directory(ROOT)
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

    os.environ[BUILD_VERSION] = git_version(ROOT)
    ui.section(4, 5, "Сборка и запуск Docker")
    run(["docker", "compose", "config", "--quiet"])
    ensure_install_postgres_connection(cfg)
    if sys.stdout.isatty():
        code = ui.animated_call(
            ["docker", "compose", "up", "-d", "--build"],
            str(ROOT), log_path=str(ROOT / "data" / "installer.log"),
        )
        if code:
            raise RuntimeError("Ошибка сборки Docker. Подробности: data/installer.log")
    else:
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
    install_global_command()
    backups.configure_timer()
    disable_update_timer()
    ui.ok("Remnaum запущен. База создана без тестовых пользователей.")

    print("Telegram webhook: " + cfg["TELEGRAM_WEBHOOK_URL"])
    print("Telegram Mini App: " + cfg["WEBAPP_URL"])
    print("Веб-приложение в браузере: " + cfg["BROWSER_WEBAPP_URL"])
    print("Для браузерного входа через Telegram укажите " + cfg["BROWSER_DOMAIN"] + " в BotFather → /setdomain.")
    print("DNS: направьте " + ", ".join(public_domains(cfg)) + " на сервер Remnaum.")
    print("Remnawave events: https://" + cfg["PUBLIC_DOMAIN"] + "/webhooks/remnawave")
    if mode != "panel" or local_panel_for_url(cfg["REMNAWAVE_BASE_URL"]) is None:
        print("Для удалённой панели параметры вебхука находятся в .env — настройте исходящие события на сервере панели.")
    print("GitHub Releases: https://" + cfg["PUBLIC_DOMAIN"] + "/webhooks/github/releases (секрет в .env)")
    print("Check: docker compose ps; docker compose logs --tail=100 app")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("Операция прервана.", file=sys.stderr)
        sys.exit(130)
    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError, EOFError) as exc:
        print(f"Ошибка Remnaum: {exc}", file=sys.stderr)
        sys.exit(1)
