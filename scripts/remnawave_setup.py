"""Remnawave discovery and safe local webhook setup for the installer.

Uses only the Python standard library. The panel API token is used for a
read-only GET request; upstream webhook settings are not exposed via that API.
Never read or update an unrelated panel installation by guessing its paths.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

PANEL_ROOT = Path("/opt/remnawave")
WEBHOOK_KEYS = ("WEBHOOK_ENABLED", "WEBHOOK_URL", "WEBHOOK_SECRET_HEADER")
WEBHOOK_SECRET_RE = re.compile(r"[A-Za-z0-9]{32,256}")
SQUAD_UUID_RE = re.compile(r"[a-fA-F0-9-]{36}")


def panel_api_url(base: str) -> str:
    parsed = urlsplit(base)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Remnawave API должен использовать HTTPS без логина и параметров URL")
    if parsed.port not in (None, 443, 8443):
        raise ValueError("Укажите стандартный HTTPS-адрес панели (без произвольного порта)")
    return base.rstrip("/") + "/api/internal-squads"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Панель перенаправила API-запрос; не передаём токен другому серверу")


def _fetch_json(url: str, token: str) -> object:
    request = urllib.request.Request(
        url, headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "Remnaum-Installer",
        },
    )
    opener = urllib.request.build_opener(_NoRedirect)
    with opener.open(request, timeout=12) as response:
        if response.status != 200:
            raise ValueError("Remnawave API вернул неожиданный статус")
        if not response.headers.get("Content-Type", "").lower().startswith("application/json"):
            raise ValueError("Remnawave API вернул не JSON")
        raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError("Слишком большой ответ API")
        return json.loads(raw)


def parse_squads(payload: object) -> list[dict[str, str]]:
    """Accept documented backend response and a direct list, but no guesses."""
    if isinstance(payload, dict):
        payload = payload.get("response", payload)
        if isinstance(payload, dict):
            payload = payload.get("internalSquads")
    if not isinstance(payload, list):
        raise ValueError("Неизвестный формат списка внутренних групп Remnawave")
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in payload:
        if not isinstance(row, dict) or not row.get("uuid"):
            continue
        try:
            identifier = str(uuid.UUID(str(row["uuid"])))
        except (ValueError, TypeError, AttributeError):
            continue
        if identifier in seen:
            continue
        seen.add(identifier)
        name = str(row.get("name") or row.get("remark") or "Без названия").strip()
        result.append({"uuid": identifier, "name": name[:90]})
    return result


def discover_squads(base_url: str, token: str) -> list[dict[str, str]]:
    if not token or any(c in token for c in "\n\r"):
        raise ValueError("Некорректный токен API")
    return parse_squads(_fetch_json(panel_api_url(base_url), token))


def select_squads(
    squads: list[dict[str, str]],
    ask,
    *,
    default_all: bool = False,
) -> str:
    if not squads:
        raise ValueError("У панели пока нет внутренних групп. Создайте Internal Squad в Remnawave.")
    if len(squads) == 1:
        print(f"✅ Найдена внутренняя группа: {squads[0]['name']}")
        return squads[0]["uuid"]
    print("\nВнутренние группы Remnawave:")
    for index, squad in enumerate(squads, start=1):
        print(f"  {index}. {squad['name']}")
    # We never silently assign all squads; some may be privileged.
    label = "Номера групп для продажи (например 1,2)"
    answer = ask(label, default="1")
    if not re.fullmatch(r"[0-9]+(?:\s*,\s*[0-9]+)*", answer):
        raise ValueError("Нужно указать номера групп через запятую")
    indices = [int(part.strip()) for part in answer.split(",")]
    if any(i < 1 or i > len(squads) for i in indices):
        raise ValueError("Такого номера группы нет")
    return ",".join(dict.fromkeys(squads[i - 1]["uuid"] for i in indices))


def validate_uuid_list(value: str) -> str:
    try:
        identifiers = [str(uuid.UUID(item.strip())) for item in value.split(",")]
    except (ValueError, AttributeError) as exc:
        raise ValueError("Введите UUID внутренних групп через запятую") from exc
    if not identifiers or len(identifiers) > 100:
        raise ValueError("Некорректное количество внутренних групп")
    return ",".join(dict.fromkeys(identifiers))


def discover_local_panel(root: Path = PANEL_ROOT) -> Path | None:
    """Only the documented official Panel directory; avoid random .env files."""
    env_file = root / ".env"
    if not env_file.is_file() or not (root / "docker-compose.yml").is_file():
        return None
    return env_file


def local_panel_for_url(base_url: str, root: Path = PANEL_ROOT) -> Path | None:
    """Never modify an unrelated local Panel if API points to a remote server."""
    env = discover_local_panel(root)
    if env is None:
        return None
    hostname = urlsplit(base_url).hostname
    current = read_panel_env(env).get("FRONT_END_DOMAIN", "").strip()
    panel_hostname = urlsplit(
        current if "://" in current else "https://" + current
    ).hostname
    return env if hostname and hostname.lower() == (panel_hostname or "").lower() else None


def _unquote(value: str) -> str:
    value = value.strip()
    if value.startswith(("'", '"')) and len(value) >= 2 and value[-1] == value[0]:
        return value[1:-1]
    return value


def read_panel_env(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\s*([A-Z][A-Z0-9_]*)=(.*)$", line)
        if match:
            result[match.group(1)] = _unquote(match.group(2))
    return result


def panel_webhook_secret(path: Path) -> str | None:
    value = read_panel_env(path).get("WEBHOOK_SECRET_HEADER", "")
    return value if WEBHOOK_SECRET_RE.fullmatch(value) else None


def plan_panel_webhook(path: Path, webhook_url: str, secret: str) -> tuple[dict[str, str], bool]:
    parsed = urlsplit(webhook_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Webhook должен быть публичным HTTPS-адресом")
    if not webhook_url.endswith("/webhooks/remnawave"):
        raise ValueError("Неверный путь вебхука Remnaum")
    if not WEBHOOK_SECRET_RE.fullmatch(secret):
        raise ValueError("Webhook secret должен содержать 32+ латинских букв или цифр")
    original = read_panel_env(path)
    current_secret = original.get("WEBHOOK_SECRET_HEADER", "")
    if current_secret and current_secret != secret:
        raise ValueError("Секреты панели и Remnaum отличаются — не заменяем секрет работающих интеграций")
    existing = [part.strip() for part in original.get("WEBHOOK_URL", "").split(",") if part.strip()]
    if webhook_url not in existing:
        existing.append(webhook_url)
    changes = {
        "WEBHOOK_ENABLED": "true",
        "WEBHOOK_URL": ",".join(existing),
        "WEBHOOK_SECRET_HEADER": secret,
    }
    changed = any(original.get(key) != val for key, val in changes.items())
    return changes, changed


def apply_env_changes(path: Path, changes: dict[str, str]) -> Path:
    """Atomic env update with rollback backup and permissions, preserving other keys."""
    content = path.read_text(encoding="utf-8")
    if path.is_symlink():
        raise ValueError("Отказываемся менять ссылку на чужую конфигурацию")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    backup = path.with_name(path.name + ".remnaum-backup-" + stamp)
    if backup.exists():
        raise ValueError("Файл резервной копии уже существует")
    shutil.copy2(path, backup)
    os.chmod(backup, 0o600)
    lines = content.splitlines()
    result = []
    done: set[str] = set()
    for line in lines:
        matched = re.match(r"^(\s*)([A-Z][A-Z0-9_]*)=(.*)$", line)
        if matched and matched.group(2) in changes:
            key = matched.group(2)
            if key not in done:
                result.append(f"{key}={changes[key]}")
                done.add(key)
        else:
            result.append(line)
    for key, value in changes.items():
        if key not in done:
            result.append(f"{key}={value}")
    fd, temp = tempfile.mkstemp(prefix=".remnaum-env-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write("\n".join(result) + "\n")
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temp, path.stat().st_mode & 0o777)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    return backup


def apply_local_panel_webhook(
    env_path: Path,
    webhook_url: str,
    secret: str,
    ask,
    *,
    run_panel_command=None,
) -> bool:
    """Update official local panel with a backup; restart ONLY panel container.

    Called only after the Remnaum API/proxy is ready and after user confirmation.
    On error restore the original env and try to restore the running service.
    """
    changes, changed = plan_panel_webhook(env_path, webhook_url, secret)
    if not changed:
        print("✅ Вебхук Remnawave уже настроен")
        return True
    confirm = ask("Добавить вебхук в локальную Remnawave и перезапустить только панель? (Д/н)", default="Д")
    if confirm.lower() not in ("д", "да", "y", "yes"):
        return False

    root = env_path.parent
    compose = root / "docker-compose.yml"
    if not compose.is_file():
        raise ValueError("Файл Docker Compose панели не найден")
    if run_panel_command is None:
        def run_panel_command(command):
            return subprocess.run(
                command, cwd=root, check=True, stdout=subprocess.DEVNULL,
                timeout=120, text=True,
            )
    base = ["docker", "compose", "-f", str(compose)]
    check = base + ["config", "--quiet"]
    restart = base + ["up", "-d", "--no-deps", "--force-recreate", "remnawave"]
    # Ensure official service exists before editing another application's .env.
    available = subprocess.run(
        base + ["config", "--services"], cwd=root, text=True,
        capture_output=True, timeout=20,
    )
    if available.returncode != 0 or "remnawave" not in available.stdout.splitlines():
        raise ValueError("Найден нестандартный Docker Compose панели — не меняем его автоматически")
    backup = apply_env_changes(env_path, changes)
    try:
        run_panel_command(check)
        run_panel_command(restart)
    except Exception:
        shutil.copy2(backup, env_path)
        try:
            run_panel_command(restart)
        except Exception:
            pass
        raise
    print(f"✅ Исходящий вебхук Remnawave добавлен; копия конфигурации: {backup}")
    return True
