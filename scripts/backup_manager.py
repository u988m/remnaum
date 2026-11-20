#!/usr/bin/env python3
"""Encrypted full backup/restore of a Remnaum installation.

Includes source, .env, site config, data directory, a PostgreSQL custom dump,
and a Redis RDB snapshot. Backup key is NEVER bundled with the archive.
The host has only Python stdlib, Docker Compose and OpenSSL as prerequisites.
"""
from __future__ import annotations

import argparse
import contextlib
import getpass
import fcntl
import hashlib
import hmac
import http.client
import io
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from release_version import BUILD_VERSION, git_version

ROOT = Path(__file__).resolve().parents[1]
BACKUPS = ROOT / "backups"
DATA = ROOT / "data"
KEY = BACKUPS / ".recovery-key"
SETTINGS = DATA / "backup-settings.json"
MAGIC = b"REMNAUM-BACKUP-V1\n"
EXCLUDE_TOP = {"backups", ".pytest_cache", "__pycache__", ".venv", ".mypy_cache"}
EXCLUDE_DATA = {"backup.key", ".backup.lock", ".deploy.lock"}
ALLOWED_SCHEDULES = {"off", "hourly", "daily"}
MAX_TELEGRAM_BYTES = 45 * 1024 * 1024


def _secure_write(path: Path, value: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="remnaum-write-", dir=path.parent) as tmp:
        temporary = Path(tmp) / "data"
        with temporary.open("xb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)


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
    if not sys.stdout.isatty():
        raise RuntimeError("Перед автоматическими копиями установите ключ восстановления через sudo remnaum")
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
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="remnaum-encrypt-", dir=destination.parent) as tmp:
        encrypted = Path(tmp) / "payload.enc"
        subprocess.run([
            "openssl", "enc", "-aes-256-cbc", "-salt", "-pbkdf2",
            "-iter", "280000", "-md", "sha256",
            "-pass", "stdin", "-in", str(plaintext), "-out", str(encrypted),
        ], input=password + b"\n", check=True, capture_output=True)
        digest = hmac.new(_crypt_key(password), digestmod=hashlib.sha256)
        target = Path(tmp) / "archive.rmb"
        with target.open("xb") as out, encrypted.open("rb") as src:
            os.fchmod(out.fileno(), 0o600)
            out.write(MAGIC + bytes(digest.digest_size))
            for chunk in iter(lambda: src.read(1024 * 1024), b""):
                digest.update(chunk)
                out.write(chunk)
            out.seek(len(MAGIC))
            out.write(digest.digest())
            out.flush()
            os.fsync(out.fileno())
        os.replace(target, destination)


def unpack_encrypted(archive: Path, plaintext: Path, password: bytes) -> None:
    if archive.is_symlink() or not archive.is_file():
        raise ValueError("Архив не найден или является ссылкой")
    if not shutil.which("openssl"):
        raise RuntimeError("Для восстановления требуется OpenSSL")
    with tempfile.TemporaryDirectory(prefix="remnaum-decrypt-", dir=plaintext.parent) as tmp:
        payload = Path(tmp) / "payload.enc"
        digest = hmac.new(_crypt_key(password), digestmod=hashlib.sha256)
        with archive.open("rb") as handle:
            prefix, mac = handle.read(len(MAGIC)), handle.read(32)
            if prefix != MAGIC or len(mac) != 32:
                raise ValueError("Неизвестный или повреждённый формат резервной копии")
            with payload.open("wb") as ciphertext:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
                    ciphertext.write(chunk)
        if payload.stat().st_size < 32:
            raise ValueError("Повреждённый архив Remnaum")
        if not hmac.compare_digest(mac, digest.digest()):
            raise ValueError("Неверный ключ восстановления или архив повреждён")
        decoded = Path(tmp) / "decoded"
        subprocess.run([
            "openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2",
            "-iter", "280000", "-md", "sha256",
            "-pass", "stdin", "-in", str(payload), "-out", str(decoded),
        ], input=password + b"\n", check=True, capture_output=True)
        decoded.chmod(0o600)
        os.replace(decoded, plaintext)


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
def _lock(filename: str = ".backup.lock"):
    DATA.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(DATA / filename, os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def postgres_admin(sql: str, *, cwd: Path = ROOT) -> str:
    # The official image allows local socket access. Keep credentials out of
    # command arguments, shell history and installer output.
    command = ["docker", "compose", "exec", "-T", "postgres", "psql", "-X", "-w", "-qAt",
               "-v", "ON_ERROR_STOP=1", "-h", "/var/run/postgresql", "-U", "remnaum", "-d", "remnaum"]
    result = subprocess.run(command, cwd=cwd, input=sql, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError("Не удалось выполнить команду через локальный сокет PostgreSQL")
    return result.stdout.strip()


def set_postgres_password(password: str, *, cwd: Path = ROOT) -> None:
    if not password or any(character in password for character in "\r\n\0"):
        raise ValueError("Пароль PostgreSQL должен быть непустой строкой")
    postgres_admin(f"\\password remnaum\n{password}\n{password}\n", cwd=cwd)


def wait_for_postgres(cwd: Path) -> None:
    for _ in range(30):
        health = subprocess.run(
            ["docker", "compose", "exec", "-T", "postgres", "pg_isready", "-h", "127.0.0.1",
             "-U", "remnaum", "-d", "remnaum"],
            cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
        )
        if health.returncode == 0:
            return
        time.sleep(2)
    raise RuntimeError("PostgreSQL не готов принимать подключения")


def prepare_branding_directory(cwd: Path) -> None:
    data = cwd / "data"
    branding = data / "branding"
    if data.is_symlink() or branding.is_symlink():
        raise ValueError("Каталог брендинга должен находиться внутри data")
    branding.mkdir(parents=True, exist_ok=True)
    # Match Dockerfile's unprivileged app user, preserving host-side data ownership.
    data.chmod(stat.S_IMODE(data.stat().st_mode) | stat.S_IXOTH)
    os.chown(branding, 1000, 1000)
    image = branding / "welcome.jpg"
    if image.is_file() and not image.is_symlink():
        os.chown(image, 1000, 1000, follow_symlinks=False)


def _manifest() -> dict:
    return {
        "format": 1,
        "product": "remnaum",
        "original_path": str(ROOT.resolve()),
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "contains": ["installation", "postgresql", "redis"],
    }


def create_backup(*, scheduled: bool = False, send: bool | None = None, protected: Path | None = None) -> Path:
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
        remove_old_archives(settings["keep"], protected=protected)
        print(f"✓ Полная зашифрованная копия: {out}")
        deliver = settings["telegram"] if send is None else send
        if deliver:
            try:
                send_to_telegram(out, settings["chat_id"])
            except (ValueError, OSError, RuntimeError) as exc:
                print("Не удалось отправить архив в Telegram; локальная копия сохранена.")
        return out


def remove_old_archives(keep: int, *, protected: Path | None = None) -> None:
    files = sorted(BACKUPS.glob("remnaum-full-*.rmb"), reverse=True)
    saved = {protected.resolve()} if protected else set()
    for item in files[keep:]:
        if item.resolve() not in saved and item.is_file() and not item.is_symlink():
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
    prefix = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"chat_id\"\r\n\r\n{chat_id}\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"document\"; filename=\"{archive.name}\"\r\n"
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode()
    suffix = f"\r\n--{boundary}--\r\n".encode()
    # Send one bounded chunk at a time. The host installer uses Python stdlib only.
    # Only encrypted bytes are uploaded; the recovery key never leaves the host.
    connection = http.client.HTTPSConnection("api.telegram.org", timeout=100)
    try:
        connection.putrequest("POST", f"/bot{token}/sendDocument")
        connection.putheader("Content-Type", "multipart/form-data; boundary=" + boundary)
        connection.putheader("Content-Length", str(len(prefix) + archive.stat().st_size + len(suffix)))
        connection.endheaders()
        connection.send(prefix)
        with archive.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                connection.send(chunk)
        connection.send(suffix)
        response = connection.getresponse()
        if response.status != 200:
            raise RuntimeError(f"Telegram rejected backup upload (HTTP {response.status})")
        result = json.loads(response.read(64 * 1024))
    finally:
        connection.close()
    if not result.get("ok"):
        raise RuntimeError("Telegram отклонил зашифрованный архив")
    print("✓ Зашифрованный архив отправлен в Telegram")


def _restore_limit(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    if not re.fullmatch(r"[1-9][0-9]{0,17}", value):
        raise ValueError(f"{name} должен быть положительным целым числом")
    return int(value)


class RestoreTarInfo(tarfile.TarInfo):
    """Bound metadata that tarfile reads before returning a member."""

    def _proc_member(self, archive):
        if self.type in (tarfile.GNUTYPE_SPARSE, tarfile.XGLTYPE):
            raise ValueError("Неподдерживаемый заголовок резервного архива")
        if self.type not in (tarfile.XHDTYPE, tarfile.SOLARIS_XHDTYPE,
                             tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK):
            return super()._proc_member(archive)
        depth = getattr(archive, "_restore_header_depth", 0)
        if not 0 < self.size <= 64 * 1024 or depth >= 8:
            raise ValueError("Превышен лимит расширенных заголовков архива")
        archive._restore_header_depth = depth + 1
        try:
            return super()._proc_member(archive)
        finally:
            archive._restore_header_depth = depth

    def _proc_gnusparse_10(self, *args):
        raise ValueError("Разреженные файлы не поддерживаются в резервных копиях")


def _scan_backup(archive: tarfile.TarFile) -> tuple[dict[Path, tarfile.TarInfo | None], int]:
    max_file = _restore_limit("REMNAUM_RESTORE_MAX_FILE_BYTES", 16 * 1024 ** 3)
    max_bytes = _restore_limit("REMNAUM_RESTORE_MAX_TOTAL_BYTES", 64 * 1024 ** 3)
    max_paths = _restore_limit("REMNAUM_RESTORE_MAX_PATHS", 250_000)
    paths: dict[Path, tarfile.TarInfo | None] = {}
    total = 0
    # Iterating allows us to stop at the first rejected member without loading the tar index.
    for member in archive:
        name = Path(member.name)
        if (name.is_absolute() or not name.parts or len(name.parts) > 128 or ".." in name.parts
                or "\x00" in member.name or len(os.fsencode(str(name))) >= 4096
                or member.type not in (tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE)
                or member.issparse() or member.size < 0
                or (member.isdir() and member.size)):
            raise ValueError("Небезопасное содержимое резервного архива")
        if name.parts[0] == "install":
            if len(name.parts) == 1 and not member.isdir():
                raise ValueError("Компонент install должен быть каталогом")
        elif len(name.parts) != 1 or name.name not in {"metadata.json", "database.dump", "redis.rdb"} or not member.isfile():
            raise ValueError("Неизвестный компонент архива")

        if paths.get(name) is not None:
            raise ValueError("Дублирующийся путь в резервном архиве")
        if name in paths and not member.isdir():
            raise ValueError("Конфликт файла и каталога в резервном архиве")
        paths[name] = member
        for parent in name.parents:
            if parent == Path("."):
                break
            existing = paths.setdefault(parent, None)
            if existing is not None and not existing.isdir():
                raise ValueError("Конфликт файла и каталога в резервном архиве")
            if len(paths) > max_paths:
                raise ValueError("Превышен лимит файлов и каталогов резервной копии")
        if len(paths) > max_paths:
            raise ValueError("Превышен лимит файлов и каталогов резервной копии")
        if member.size > max_file:
            raise ValueError("Превышен лимит размера файла резервной копии")
        total += member.size
        if total > max_bytes:
            raise ValueError("Превышен лимит общего объёма резервной копии")
        # TarInfo already contains the resolved values. Release the per-entry metadata.
        member.pax_headers = {}
    return paths, total


def _safe_extract(archive: tarfile.TarFile, target: Path, *, layout=None) -> None:
    paths, total = _scan_backup(archive) if layout is None else layout
    if total > shutil.disk_usage(target.parent).free:
        raise ValueError("Недостаточно места для распаковки резервной копии")
    target.mkdir(mode=0o700)
    for name, member in paths.items():
        if member is None:
            continue
        path = target / name
        if member.isdir():
            path.mkdir(parents=True, exist_ok=True)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        with archive.extractfile(member) as src, path.open("xb") as dst:
            shutil.copyfileobj(src, dst)
        os.chmod(path, 0o600 if path.name == ".env" else 0o644)


def _read_backup_manifest(archive: tarfile.TarFile, paths: dict) -> dict:
    for name in ("database.dump", "redis.rdb", "install/.env", "install/compose.yaml"):
        member = paths.get(Path(name))
        if member is None:
            raise ValueError(f"В архиве отсутствует {name}")
        if not member.isfile() or not member.size:
            raise ValueError(f"Некорректный компонент архива: {name}")
    member = paths.get(Path("metadata.json"))
    if member is None:
        raise ValueError("Нет метаданных архива")
    if not member.isfile() or member.size > 8192:
        raise ValueError("Некорректные метаданные архива")
    with archive.extractfile(member) as metadata:
        info = json.load(metadata)
    if (not isinstance(info, dict) or info.get("product") != "remnaum"
            or type(info.get("format")) is not int or info["format"] != 1):
        raise ValueError("Архив не принадлежит Remnaum")
    return info


def inspect_backup(archive: Path, *, supplied_key: str | None = None) -> dict:
    password = key_bytes(supplied=supplied_key)
    with tempfile.TemporaryDirectory(prefix="remnaum-inspect-") as tmp:
        plain = Path(tmp) / "archive.tar.gz"
        unpack_encrypted(archive, plain, password)
        with tarfile.open(plain, "r:gz", tarinfo=RestoreTarInfo) as tar:
            paths, _ = _scan_backup(tar)
            return _read_backup_manifest(tar, paths)


def _check_restore_destination(target: Path, paths: dict) -> None:
    for name in sorted(paths, key=lambda path: len(path.parts)):
        member = paths[name]
        if name.parts[0] != "install" or len(name.parts) < 2 or name.parts[1] == "backups":
            continue
        try:
            status = (target / Path(*name.parts[1:])).lstat()
        except FileNotFoundError:
            continue
        is_dir = member is None or member.isdir()
        if is_dir and stat.S_ISDIR(status.st_mode):
            continue
        if not is_dir and stat.S_ISREG(status.st_mode) and status.st_nlink == 1:
            continue
        raise ValueError(f"Небезопасный путь в каталоге назначения: {name}")


# Redis 7 loads the AOF manifest when appendonly is enabled. Seed its base
# file from the validated RDB, and keep dump.rdb for RDB-only configurations.
_RESTORE_REDIS = """
set -eu
stage=$(mktemp -d /data/.remnaum-restore.XXXXXX)
trap 'rm -rf "$stage"' EXIT
cp /restore/redis.rdb "$stage/dump.rdb"
redis-check-rdb "$stage/dump.rdb"
mkdir "$stage/appendonlydir"
ln "$stage/dump.rdb" "$stage/appendonlydir/appendonly.aof.1.base.rdb"
printf '%s\n' 'file appendonly.aof.1.base.rdb seq 1 type b' > "$stage/appendonlydir/appendonly.aof.manifest"
if [ -e /data/appendonlydir ]; then
    mv /data/appendonlydir "$stage/previous"
fi
if mv "$stage/appendonlydir" /data/appendonlydir; then
    if mv -f "$stage/dump.rdb" /data/dump.rdb; then
        exit 0
    fi
    mv /data/appendonlydir "$stage/appendonlydir" || { trap - EXIT; exit 1; }
fi
if [ -d "$stage/previous" ]; then
    mv "$stage/previous" /data/appendonlydir || { trap - EXIT; exit 1; }
fi
exit 1
"""


def restore_backup(archive: Path, *, supplied_key: str | None = None, target_root: Path | None = None) -> None:
    """Restore in the current install location, selected by bootstrap/installer.

    PostgreSQL is restored from its consistent snapshot. External Panel/Caddy
    configurations are never overwritten by a shop restoration.
    """
    password = key_bytes(supplied=supplied_key)
    target = (target_root or ROOT).resolve()
    if not re.fullmatch(r"/[A-Za-z0-9_./-]+", str(target)):
        raise ValueError("Небезопасный путь восстановления")
    if target == Path("/") or target == Path("/etc") or str(target).startswith(("/etc/", "/usr/", "/dev/", "/proc/", "/sys/")):
        raise ValueError("Нельзя восстанавливать в системный каталог")
    if target != ROOT.resolve() and target.exists() and any(target.iterdir()):
        raise ValueError("Внешняя папка восстановления уже содержит данные")
    with _lock(".deploy.lock"), _lock():
        with tempfile.TemporaryDirectory(prefix="remnaum-restore-") as tmp:
            folder = Path(tmp)
            plain = folder / "archive.tar.gz"
            unpack_encrypted(archive, plain, password)
            extracted = folder / "extracted"
            with tarfile.open(plain, "r:gz", tarinfo=RestoreTarInfo) as tar:
                layout = _scan_backup(tar)
                metadata = _read_backup_manifest(tar, layout[0])
                _check_restore_destination(target, layout[0])
                _safe_extract(tar, extracted, layout=layout)
            install_dir = extracted / "install"

            for name, magic in (("database.dump", b"PGDMP"), ("redis.rdb", b"REDIS")):
                with (extracted / name).open("rb") as snapshot:
                    if snapshot.read(len(magic)) != magic:
                        raise ValueError(f"Некорректный формат снимка: {name}")

            target.mkdir(parents=True, exist_ok=True)
            print("Восстанавливаем данные и настройки в", target)
            if (target / ".env").is_file():
                _docker("stop", "app", cwd=target, timeout=60)
            try:
                for entry in sorted(install_dir.iterdir()):
                    if entry.name in {"backups"}:
                        continue
                    destination = target / entry.name
                    if entry.is_dir():
                        shutil.copytree(entry, destination, dirs_exist_ok=True)
                    else:
                        shutil.copy2(entry, destination)
                os.chmod(target / ".env", 0o600)
                subprocess.run(
                    ["docker", "compose", "up", "-d", "postgres", "redis"],
                    cwd=target, check=True, capture_output=True, timeout=180,
                )
                wait_for_postgres(target)
                # pg_dump restores database objects but does not include role passwords.
                result = subprocess.run(
                    ["docker", "compose", "exec", "-T", "postgres", "printenv", "POSTGRES_PASSWORD"],
                    cwd=target, check=True, capture_output=True, text=True, timeout=30,
                )
                set_postgres_password(result.stdout.removesuffix("\n"), cwd=target)
                with (extracted / "database.dump").open("rb") as dump:
                    subprocess.run([
                        "docker", "compose", "exec", "-T", "postgres",
                        "pg_restore", "-U", "remnaum", "-d", "remnaum",
                        "--no-owner", "--no-acl", "--clean", "--if-exists",
                        "--single-transaction",
                    ], cwd=target, stdin=dump, check=True, timeout=360)
                subprocess.run(["docker", "compose", "stop", "redis"], cwd=target, check=True,
                               stdout=subprocess.DEVNULL, timeout=60)
                subprocess.run([
                    "docker", "compose", "run", "--rm", "-T", "--no-deps",
                    "-v", str(extracted) + ":/restore:ro",
                    "--entrypoint", "sh", "redis", "-c",
                    _RESTORE_REDIS,
                ], cwd=target, check=True, capture_output=True, timeout=120)
                prepare_branding_directory(target)
                subprocess.run(["docker", "compose", "up", "-d", "--build"], cwd=target,
                               env=os.environ | {BUILD_VERSION: git_version(target)}, check=True, timeout=360)
                for _ in range(40):
                    health = subprocess.run([
                        "docker", "compose", "exec", "-T", "app", "python", "-c",
                        "import urllib.request; assert urllib.request.urlopen('http://127.0.0.1:8080/health',timeout=4).status==200",
                    ], cwd=target, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    if health.returncode == 0:
                        break
                    time.sleep(2)
                else:
                    raise RuntimeError("Данные восстановлены, но приложение не отвечает. Проверьте логи: docker compose logs app")
            except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                raise RuntimeError(
                    f"Восстановление прервано: {exc}. Файлы, PostgreSQL и Redis могли быть обновлены частично. "
                    "Оставьте приложение остановленным до проверки данных. "
                    "Устраните причину сбоя и повторите восстановление или восстановите аварийную копию."
                ) from exc
            print("✓ Данные, настройки и контейнеры Remnaum восстановлены")
            print("Исходный путь архива:", metadata.get("original_path"))
            print("Текущий путь:", target)
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
    inspect = sub.add_parser("inspect")
    inspect.add_argument("archive", type=Path)
    sub.add_parser("timer")
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError("Для резервных копий нужен sudo")
    if args.action == "create":
        create_backup(scheduled=args.scheduled)
    elif args.action == "restore":
        restore_backup(args.archive.expanduser().resolve(), supplied_key=(None if KEY.exists() else getpass.getpass("Ключ восстановления: ")))
    elif args.action == "inspect":
        print(json.dumps(inspect_backup(args.archive.expanduser().resolve(), supplied_key=(None if KEY.exists() else getpass.getpass("Ключ восстановления: "))),
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
