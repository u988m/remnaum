#!/usr/bin/env python3
"""Check upstream main and inform Telegram administrators of a new Remnaum update.

Runs on the VPS via a systemd timer. Uses the existing Git remote and SSH/HTTPS
credentials of the repository owner, so private repos do not need GitHub tokens
inside app containers. Never displays the bot token in output.
"""
from __future__ import annotations

import json
import os
import pwd
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
COMMUNITY_UPDATES = "https://t.me/remnaum/4"
STATE_FILE = ROOT / "data" / "update-alerts.json"


def read_settings(path: Path) -> dict[str, str]:
    result = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in ("TELEGRAM_BOT_TOKEN", "ADMIN_IDS"):
            result[key] = value.strip().strip("'\\\"")
    return result


def git_user_command(command: list[str]) -> list[str]:
    username = os.environ.get("REMNAUM_GIT_USER", "").strip()
    if not username or username == "root":
        return command
    if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", username):
        raise ValueError("Invalid git user")
    pwd.getpwnam(username)  # Reject nonexistent service accounts.
    return ["runuser", "-u", username, "--", *command]


def git(command: list[str]) -> str:
    args = git_user_command(["git", "-C", str(ROOT), *command])
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    if args[0] == "git":
        args = ["git", "-c", f"safe.directory={ROOT}", "-C", str(ROOT), *command]
    return subprocess.check_output(
        args, env=env, stderr=subprocess.DEVNULL, timeout=35, text=True,
    ).strip()


def public_github_sha() -> str | None:
    """Fallback when the checkout cannot authenticate, after repo becomes public."""
    request = Request(
        "https://api.github.com/repos/u988m/remnaum/commits/main",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "Remnaum-Update-Checker"},
    )
    try:
        with urlopen(request, timeout=10) as response:
            value = json.load(response).get("sha", "")
        return value if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value) else None
    except (OSError, ValueError, KeyError):
        return None


def available_update() -> tuple[str, str] | None:
    """Remote main changed and local checkout has not yet installed it."""
    if git(["branch", "--show-current"]) != "main":
        return None
    current = git(["rev-parse", "HEAD"])
    try:
        remote_line = git(["ls-remote", "origin", "refs/heads/main"])
        fields = remote_line.split()
        upstream = fields[0] if len(fields) == 2 and fields[1] == "refs/heads/main" else None
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        upstream = public_github_sha()
    if upstream is None:
        return None
    if not re.fullmatch(r"[0-9a-f]{40}", upstream):
        return None
    return (upstream, current) if upstream != current else None


def make_message() -> str:
    return (
        "🚀 Доступно новое обновление Remnaum!\n\n"
        "Изменения и новости: https://t.me/remnaum/4\n\n"
        "Чтобы обновить проект, на сервере запустите:\n"
        "sudo sh install.sh → 2 — Обновить"
    )


def send_notice(token: str, telegram_id: int) -> bool:
    payload = urlencode({
        "chat_id": str(telegram_id),
        "text": make_message(),
        "disable_web_page_preview": "true",
        "reply_markup": json.dumps({"inline_keyboard": [[{
            "text": "📢 Что нового", "url": COMMUNITY_UPDATES,
        }]]}),
    }).encode()
    request = Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload, method="POST",
    )
    with urlopen(request, timeout=15) as response:
        parsed = json.load(response)
    return parsed.get("ok") is True


def read_state() -> dict[str, str]:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError, OSError):
        return {}


def write_state(state: dict[str, str]) -> None:
    STATE_FILE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = STATE_FILE.with_name(".update-alerts.tmp")
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(state, out)
            out.flush()
            os.fsync(out.fileno())
        os.replace(target, STATE_FILE)
        os.chmod(STATE_FILE, 0o600)
    finally:
        if target.exists():
            target.unlink()


def check_and_notify() -> int:
    update = available_update()
    if update is None:
        return 0
    sha, _ = update
    settings = read_settings(ROOT / ".env")
    token = settings.get("TELEGRAM_BOT_TOKEN", "")
    admin_ids = {
        int(part.strip()) for part in settings.get("ADMIN_IDS", "").split(",")
        if part.strip().isdigit() and int(part.strip()) > 0
    }
    if not token or not admin_ids:
        return 0

    state = read_state()
    sent = 0
    for admin_id in sorted(admin_ids):
        if state.get(str(admin_id)) == sha:
            continue
        try:
            if send_notice(token, admin_id):
                state[str(admin_id)] = sha
                write_state(state)
                sent += 1
        except Exception:
            # Admin may not have started the bot; retry next time. Do not log secrets.
            print(f"Не удалось отправить уведомление администратору {admin_id}", file=sys.stderr)
    if sent:
        print(f"Уведомление об обновлении отправлено администраторам: {sent}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(check_and_notify())
    except (OSError, ValueError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        print("Не удалось проверить обновления GitHub (доступ к репозиторию или сеть).", file=sys.stderr)
        raise SystemExit(0)  # Network outage must never break installed service.
