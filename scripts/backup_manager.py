#!/usr/bin/env python3
"""Encrypted full backup/restore of a Remnaum installation.

Includes source, .env, site config, data directory, a PostgreSQL custom dump,
and a Redis RDB snapshot. Backup key is NEVER bundled with the archive.
The host has only Python stdlib, Docker Compose and OpenSSL as prerequisites.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
BACKUPS = ROOT / "backups"
DATA = ROOT / "data"
KEY = DATA / "backup.key"
SETTINGS = DATA / "backup-settings.json"
MAGIC = b"REMNAUM-BACKUP-V1\n"
EXCLUDE_TOP = {"backups", ".pytest_cache", "__pycache__", ".venv", ".mypy_cache"}
EXCLUDE_DATA = {"backup.key", ".backup.lock"}
ALLOWED_SCHEDULES = {"off", "hourly", "daily"}
MAX_TELEGRAM_BYTES = 45 * 1024 * 1024


def _secure_write(path: Path, value: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        temporary.unlink(missing_ok=True)


def load_settings() -> dict:
    defaults = {"schedule": "off", "keep": 7, "telegram": False, "chat_id": ""}
    try:
        payload = json.loads(SETTINGS.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            defaults.update({key: payload[key] for key in defaults if key in payload})
    except (FileNotFoundError, ValueError):
        pass
    return defaults


def save_settings(value: dict) -> None:
    schedule = value.get("schedule")
    keep = value.get("keep")
    chat_id = str(value.get("chat_id", ""))
    if schedule not in ALLOWED_SCHEDULES:
        raise ValueError("Период: off, hourly или daily")
    if type(keep) is not int or not 1 <= keep <= 365:
        raise ValueError("Хранить от 1 до 365 архивов")
    if type(value.get("telegram")) is not bool:
        raise ValueError("Некорректное значение Telegram")
    if value["telegram"] and not re.fullmatch(r"-?[1-9][0-9]{4,18}", chat_id):
        raise ValueError("Укажите числовой Telegram chat ID")
    normalized = {
        "schedule": schedule, "keep": keep,
        "telegram": value["telegram"], "chat_id": chat_id if value["telegram"] else "",
    }
    _secure_write(SETTINGS, (json.dumps(normalized, ensure_ascii=False, indent=2) + "\n").encode())


def key_bytes(*, supplied: str | None = None, create: bool = False) -> bytes:
    if supplied:
        if len(supplied) < 24:
            raise ValueError("Ключ восстановления должен быть не короче 24 символов")
        return supplied.encode()
    if KEY.exists():
        value = KEY.read_bytes().strip()
        if len(value) < 24:
            raise ValueError("Повреждён ключ резервных копий")
        return value
    if not create:
        raise ValueError("Нет ключа: введите секрет восстановления от прежнего сервера")
    value = secrets.token_urlsafe(48).encode()
    _secure_write(KEY, value + b"\n")
    print("\nКЛЮЧ ВОССТАНОВЛЕНИЯ (сохраните отдельно от сервера):")
    print(value.decode())
    print("Без него нельзя открыть архив, присланный в Telegram или перенесённый на другой VPS.\n")
    return value


def _crypt_key(password: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password, b"remnaum-backup-hmac-v1", 280000, dklen=32)


def pack_encrypted(plaintext: Path, destination: Path, password: bytes) -> None:
    if not shutil.which("openssl"):
        raise RuntimeError("Нужен OpenSSL: apt-get install openssl")
    with tempfile.TemporaryDirectory(prefix="remnaum-encrypt-") as tmp:
        encrypted = Path(tmp) / "payload.enc"
        subprocess.run([
            "openssl", "enc", "-aes-256-cbc", "-salt", "-pbkdf2",
            "-iter", "280000", "-md", "sha256",
            "-pass", "stdin", "-in", str(plaintext), "-out", str(encrypted),
        ], input=password + b"\n", check=True, capture_output=True)
        ciphertext = encrypted.read_bytes()
    digest = hmac.new(_crypt_key(password), ciphertext, hashlib.sha256).digest()
    _secure_write(destination, MAGIC + digest + ciphertext)


def unpack_encrypted(archive: Path, plaintext: Path, password: bytes) -> None:
    if archive.is_symlink() or not archive.is_file():
        raise ValueError("Архив не найден или является ссылкой")
    with archive.open("rb") as handle:
        prefix = handle.read(len(MAGIC))
        mac = handle.read(32)
        ciphertext = handle.read()
    if prefix != MAGIC or len(mac) != 32 or len(ciphertext) < 32:
        raise ValueError("Неизвестный или повреждённый формат резервной копии")
    digest = hmac.new(_crypt_key(password), ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(mac, digest):
        raise ValueError("Неверный ключ восстановления или архив повреждён")
    if not shutil.which("openssl"):
        raise RuntimeError("Для восстановления требуется OpenSSL")
    with tempfile.TemporaryDirectory(prefix="remnaum-decrypt-") as tmp:
        payload = Path(tmp) / "payload.enc"
        payload.write_bytes(ciphertext)
        subprocess.run([
            "openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2",
            "-iter", "280000", "-md", "sha256",
            "-pass", "stdin", "-in", str(payload), "-out", str(plaintext),
        ], input=password + b"\n", check=True, capture_output=True)


def _docker(*args: str, cwd: Path = ROOT, stdin=None, stdout=None, timeout=240) -> None:
    subprocess.run(["docker", "compose", *args], cwd=cwd, input=stdin,
                   stdout=stdout if stdout is not None else subprocess.DEVNULL,
                   stderr=subprocess.PIPE, check=True, timeout=timeout)


def _redis_snapshot(directory: Path) -> None:
    _docker("exec", "-T", "redis", "redis-cli", "SAVE")
    container = subprocess.check_output(
        ["docker", "compose", "ps", "-q", "redis"], cwd=ROOT, text=True, timeout=15
    ).strip()
    if not container:
        raise RuntimeError("Не найден Redis-контейнер")
    subprocess.run(
        ["docker", "cp", f"{container}:/data/dump.rdb", str(directory / "redis.rdb")],
        check=True, capture_output=True, timeout=60,
    )


def _collect_source(archive: tarfile.TarFile) -> None:
    """Do not follow symlinks, and exclude backup encryption keys and archives."""
    for entry in sorted(ROOT.iterdir()):
        if entry.name in EXCLUDE_TOP or entry.is_symlink():
            continue
        if entry.name == "data":
            for item in sorted(entry.rglob("*")):
                relative = item.relative_to(DATA)
                if relative.parts[0] in EXCLUDE_DATA or item.is_symlink():
                    continue
                archive.add(item, arcname=str(Path("install/data") / relative), recursive=False)
        else:
            archive.add(entry, arcname=str(Path("install") / entry.name), recursive=True,
                        filter=lambda info: None if info.issym() or info.islnk()
                        or "__pycache__" in Path(info.name).parts else info)


@contextlib.contextmanager
def _lock():
    DATA.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(DATA / ".backup.lock", os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def _manifest() -> dict:
    return {
        "format": 1,
        "product": "remnaum",
        "original_path": str(ROOT.resolve()),
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "contains": ["installation", "postgresql", "redis"],
    }


def create_backup(*, scheduled: bool = False, send: bool | None = None) -> Path:
    if not (ROOT / ".env").exists():
        raise RuntimeError("Remnaum ещё не настроен: .env не найден")
    with _lock():
        BACKUPS.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(BACKUPS, 0o700)
        settings = load_settings()
        password = key_bytes(create=True)
        label = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out = BACKUPS / f"remnaum-full-{label}.rmb"
        with tempfile.TemporaryDirectory(prefix="remnaum-backup-", dir=BACKUPS) as tmp:
            directory = Path(tmp)
            with (directory / "database.dump").open("wb") as dump:
                _docker("exec", "-T", "postgres", "pg_dump", "-U", "remnaum",
                        "-d", "remnaum", "-Fc", "--no-owner", "--no-acl",
                        stdout=dump, timeout=300)
            if not (directory / "database.dump").stat().st_size:
                raise RuntimeError("PostgreSQL вернул пустую резервную копию")
            _redis_snapshot(directory)
            if not (directory / "redis.rdb").stat().st_size:
                raise RuntimeError("Redis вернул пустую резервную копию")
            raw_archive = directory / "full.tar.gz"
            manifest = json.dumps(_manifest(), ensure_ascii=False).encode()
            with tarfile.open(raw_archive, "w:gz") as archive:
                metadata = tarfile.TarInfo("metadata.json")
                metadata.size = len(manifest)
                metadata.mode = 0o600
                archive.addfile(metadata, io.BytesIO(manifest))
                archive.add(directory / "database.dump", arcname="database.dump")
                archive.add(directory / "redis.rdb", arcname="redis.rdb")
                _collect_source(archive)
            pack_encrypted(raw_archive, out, password)
        remove_old_archives(settings["keep"])
        print(f"✓ Полная зашифрованная копия: {out}")
        deliver = settings["telegram"] if send is None else send
        if deliver:
            try:
                send_to_telegram(out, settings["chat_id"])
            except (ValueError, OSError) as exc:
                print("Не удалось отправить архив в Telegram; локальная копия сохранена.")
        return out


def remove_old_archives(keep: int) -> None:
    files = sorted(BACKUPS.glob("remnaum-full-*.rmb"), reverse=True)
    for item in files[keep:]:
        if item.is_file() and not item.is_symlink():
            item.unlink()


def _bot_token() -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("TELEGRAM_BOT_TOKEN="):
            return line.partition("=")[2].strip().strip('"').strip("'")
    raise ValueError("Telegram bot token не найден")


def send_to_telegram(archive: Path, chat_id: str) -> None:
    if archive.stat().st_size > MAX_TELEGRAM_BYTES:
        raise ValueError("Архив превышает лимит Bot API, сохранён только локально")
    if not re.fullmatch(r"-?[1-9][0-9]{4,18}", str(chat_id)):
        raise ValueError("Некорректный chat ID")
    token = _bot_token()
    boundary = secrets.token_hex(18)
    filename = archive.name.encode("ascii")
    prefix = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{chat_id}\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"document\"; filename=\"{archive.name}\"\r\n"
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode()
    suffix = f"\r\n--{boundary}--\r\n".encode()
    # Only encrypted bytes are ever uploaded. The recovery key remains on the host.
    request = Request(
        f"https://api.telegram.org/bot{token}/sendDocument",
        data=prefix + archive.read_bytes() + suffix,
        headers={"Content-Type": "multipart/form-data; boundary=" + boundary},
        method="POST",
    )
    with urlopen(request, timeout=100) as response:
        result = json.load(response)
    if not result.get("ok"):
        raise RuntimeError("Telegram отклонил зашифрованный архив")
    print("✓ Зашифрованный архив отправлен в Telegram")


def _safe_extract(archive: tarfile.TarFile, target: Path) -> None:
    for member in archive.getmembers():
        name = Path(member.name)
        if (name.is_absolute() or not name.parts or ".." in name.parts
                or not (member.isfile() or member.isdir())
                or member.size > 2 * 1024 ** 3):
            raise ValueError("Небезопасное содержимое резервного архива")
        if name.parts[0] not in {"install", "metadata.json", "database.dump", "redis.rdb"}:
            raise ValueError("Неизвестный компонент архива")
    # Never follow an archive-supplied symlink/hardlink.
    for member in archive.getmembers():
        path = target / member.name
        if member.isdir():
            path.mkdir(parents=True, exist_ok=True)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        with archive.extractfile(member) as src, path.open("wb") as dst:
            shutil.copyfileobj(src, dst)
        os.chmod(path, 0o600 if path.name == ".env" else 0o644)


def inspect_backup(archive: Path, *, supplied_key: str | None = None) -> dict:
    password = key_bytes(supplied=supplied_key)
    with tempfile.TemporaryDirectory(prefix="remnaum-inspect-") as tmp:
        plain = Path(tmp) / "archive.tar.gz"
        unpack_encrypted(archive, plain, password)
        with tarfile.open(plain, "r:gz") as tar:
            metadata = tar.extractfile("metadata.json")
            if metadata is None:
                raise ValueError("Нет метаданных архива")
            info = json.loads(metadata.read(8192))
            if info.get("product") != "remnaum" or info.get("format") != 1:
                raise ValueError("Архив не принадлежит Remnaum")
            if not tar.getmember("database.dump").size:
                raise ValueError("Архив не содержит PostgreSQL")
            return info


def restore_backup(archive: Path, *, supplied_key: str | None = None) -> None:
    """Restore in the current install location, selected by bootstrap/installer.

    PostgreSQL is restored from its consistent snapshot. External Panel/Caddy
    configurations are never overwritten by a shop restoration.
    """
    password = key_bytes(supplied=supplied_key)
    with _lock():
        with tempfile.TemporaryDirectory(prefix="remnaum-restore-") as tmp:
            folder = Path(tmp)
            plain = folder / "archive.tar.gz"
            unpack_encrypted(archive, plain, password)
            extracted = folder / "extracted"
            with tarfile.open(plain, "r:gz") as tar:
                _safe_extract(tar, extracted)
            metadata = json.loads((extracted / "metadata.json").read_text())
            if metadata.get("product") != "remnaum" or metadata.get("format") != 1:
                raise ValueError("Это не резервная копия Remnaum")
            install_dir = extracted / "install"
            if not (install_dir / ".env").is_file() or not (install_dir / "compose.yaml").exists():
                raise ValueError("В архиве нет .env или Docker Compose")
            print("Восстанавливаем данные и настройки в", ROOT)
            # On an active installation create an additional recovery point first.
            if (ROOT / ".env").exists():
                create_backup_before_restore = False  # Caller confirms before invoking.
            subprocess.run(["docker", "compose", "stop", "app"], cwd=ROOT,
                           check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for entry in sorted(install_dir.iterdir()):
                if entry.name in {"backups"}:
                    continue
                destination = ROOT / entry.name
                if entry.is_dir():
                    shutil.copytree(entry, destination, dirs_exist_ok=True)
                else:
                    shutil.copy2(entry, destination)
            os.chmod(ROOT / ".env", 0o600)
            subprocess.run(
                ["docker", "compose", "up", "-d", "postgres", "redis"],
                cwd=ROOT, check=True, capture_output=True, timeout=180,
            )
            for _ in range(30):
                health = subprocess.run(
                    ["docker", "compose", "exec", "-T", "postgres", "pg_isready", "-U", "remnaum", "-d", "remnaum"],
                    cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                if health.returncode == 0:
                    break
                import time
                time.sleep(2)
            else:
                raise RuntimeError("PostgreSQL не запустился после восстановления конфигурации")
            with (extracted / "database.dump").open("rb") as dump:
                subprocess.run([
                    "docker", "compose", "exec", "-T", "postgres",
                    "pg_restore", "-U", "remnaum", "-d", "remnaum",
                    "--no-owner", "--no-acl", "--clean", "--if-exists",
                    "--single-transaction",
                ], cwd=ROOT, stdin=dump, check=True, timeout=360)
            subprocess.run(["docker", "compose", "stop", "redis"], cwd=ROOT, check=True,
                           stdout=subprocess.DEVNULL, timeout=60)
            subprocess.run([
                "docker", "compose", "run", "--rm", "-T", "--no-deps",
                "-v", str(extracted) + ":/restore:ro",
                "--entrypoint", "sh", "redis", "-c",
                "rm -rf /data/appendonlydir && cp /restore/redis.rdb /data/dump.rdb",
            ], cwd=ROOT, check=True, capture_output=True, timeout=120)
            subprocess.run(["docker", "compose", "up", "-d", "--build"], cwd=ROOT,
                           check=True, timeout=360)
            print("✓ Данные, настройки и контейнеры Remnaum восстановлены")
            print("Исходный путь архива:", metadata.get("original_path"))
            print("Текущий путь:", ROOT)
            print("Если домены переехали на другой VPS, обновите DNS. Внешнюю панель Remnawave не перезаписывали.")


def configure_timer() -> bool:
    if not shutil.which("systemctl") or not Path("/run/systemd/system").exists():
        print("Systemd не найден: автоматическое расписание недоступно")
        return False
    settings = load_settings()
    service = Path("/etc/systemd/system/remnaum-backup.service")
    timer = Path("/etc/systemd/system/remnaum-backup.timer")
    if settings["schedule"] == "off":
        subprocess.run(["systemctl", "disable", "--now", "remnaum-backup.timer"],
                       check=False, capture_output=True)
        return True
    command = f"/usr/bin/python3 {ROOT}/scripts/backup_manager.py create --scheduled"
    service.write_text(
        "[Unit]\nDescription=Encrypted Remnaum full backup\n"
        "[Service]\nType=oneshot\nUser=root\n"
        f"WorkingDirectory={ROOT}\nExecStart={command}\n",
        encoding="utf-8",
    )
    timer.write_text(
        "[Unit]\nDescription=Remnaum automatic backups\n"
        "[Timer]\n" + (
            "OnCalendar=hourly\n" if settings["schedule"] == "hourly" else "OnCalendar=daily\n"
        ) + "Persistent=true\nRandomizedDelaySec=3m\n"
        "[Install]\nWantedBy=timers.target\n", encoding="utf-8",
    )
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "enable", "--now", "remnaum-backup.timer"], check=True)
    print("✓ Автоматическое резервное копирование:", settings["schedule"])
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Полные шифрованные бэкапы Remnaum")
    sub = parser.add_subparsers(dest="action", required=True)
    create = sub.add_parser("create")
    create.add_argument("--scheduled", action="store_true")
    restore = sub.add_parser("restore")
    restore.add_argument("archive", type=Path)
    restore.add_argument("--key", default=None)
    inspect = sub.add_parser("inspect")
    inspect.add_argument("archive", type=Path)
    inspect.add_argument("--key", default=None)
    sub.add_parser("timer")
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError("Для резервных копий нужен sudo")
    if args.action == "create":
        create_backup(scheduled=args.scheduled)
    elif args.action == "restore":
        restore_backup(args.archive.expanduser().resolve(), supplied_key=args.key)
    elif args.action == "inspect":
        print(json.dumps(inspect_backup(args.archive.expanduser().resolve(), supplied_key=args.key),
                         ensure_ascii=False, indent=2))
    else:
        configure_timer()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError, BlockingIOError) as exc:
        print("Ошибка: " + str(exc), file=sys.stderr)
        raise SystemExit(1)
