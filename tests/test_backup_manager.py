"""Encrypted full-backup utilities: offline unit tests, no real Docker/Telegram."""
import hashlib
import hmac
import io
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tracemalloc
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import backup_manager as backup


KEY = "example-recovery-key-longer-than-24"


class BackupTests(unittest.TestCase):
    def test_settings_validate_and_save_without_token(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(backup, "SETTINGS", Path(temp) / "config.json"):
            backup.save_settings({
                "schedule": "hourly", "keep": 24, "telegram": True, "chat_id": "123456789"
            })
            saved = backup.load_settings()
            self.assertEqual(saved["schedule"], "hourly")
            self.assertEqual(saved["keep"], 24)
            self.assertEqual(saved["chat_id"], "123456789")
            self.assertNotIn("token", saved)
            self.assertEqual(backup.SETTINGS.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(ValueError):
                backup.save_settings({
                    "schedule": "everysecond", "keep": 24, "telegram": False, "chat_id": ""
                })
            with self.assertRaises(ValueError):
                backup.save_settings({
                    "schedule": "daily", "keep": 0, "telegram": False, "chat_id": ""
                })

    @unittest.skipUnless(shutil.which("openssl"), "OpenSSL is required")
    def test_authenticated_encrypted_archive_roundtrip(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            source = folder / "source.tar.gz"
            target = folder / "backup.rmb"
            restored = folder / "restored.tar.gz"
            source.write_bytes((b"db snapshot, secrets, env, redis " * 200))
            key = b"example-recovery-key-longer-than-24"
            backup.pack_encrypted(source, target, key)
            self.assertNotIn(b"db snapshot", target.read_bytes())
            backup.unpack_encrypted(target, restored, key)
            self.assertEqual(source.read_bytes(), restored.read_bytes())
            with self.assertRaisesRegex(ValueError, "Неверный ключ"):
                backup.unpack_encrypted(target, restored, b"another-long-recovery-key-value")
            changed = bytearray(target.read_bytes())
            changed[-3] ^= 0x01
            target.write_bytes(changed)
            with self.assertRaisesRegex(ValueError, "повреждён"):
                backup.unpack_encrypted(target, restored, key)

    def test_v1_archive_from_legacy_writer(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            data = b"backup from before streaming support"
            src = folder / "source"
            src.write_bytes(data)
            ciphertext = subprocess.run([
                "openssl", "enc", "-aes-256-cbc", "-salt", "-pbkdf2",
                "-iter", "280000", "-md", "sha256", "-pass", "stdin", "-in", str(src),
            ], input=KEY.encode() + b"\n", check=True, capture_output=True).stdout
            mac_key = hashlib.pbkdf2_hmac("sha256", KEY.encode(), b"remnaum-backup-hmac-v1", 280000)
            archive = folder / "old.rmb"
            archive.write_bytes(b"REMNAUM-BACKUP-V1\n" + hmac.digest(mac_key, ciphertext, "sha256") + ciphertext)
            backup.unpack_encrypted(archive, src, KEY.encode())
            self.assertEqual(src.read_bytes(), data)

    def test_large_roundtrip_has_bounded_memory(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            source, archive, restored = (folder / name for name in ("source", "backup.rmb", "restored"))
            with source.open("wb") as stream:
                for _ in range(24):
                    stream.write(os.urandom(1024 * 1024))
            tracemalloc.start()
            try:
                backup.pack_encrypted(source, archive, KEY.encode())
                backup.unpack_encrypted(archive, restored, KEY.encode())
                _, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
            self.assertLess(peak, 8 * 1024 * 1024)
            with source.open("rb") as original, restored.open("rb") as recovered:
                self.assertEqual(hashlib.file_digest(original, "sha256").digest(),
                                 hashlib.file_digest(recovered, "sha256").digest())

    def test_failed_archive_publication_preserves_existing_file_and_cleans_staging(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            source, archive = folder / "source", folder / "backup.rmb"
            source.write_bytes(b"source")
            archive.write_bytes(b"previous backup")
            with patch.object(backup.os, "replace", side_effect=OSError("publication failure")):
                with self.assertRaisesRegex(OSError, "publication failure"):
                    backup.pack_encrypted(source, archive, KEY.encode())
            self.assertEqual(archive.read_bytes(), b"previous backup")
            self.assertEqual(set(folder.iterdir()), {source, archive})

    def test_restore_rejects_incomplete_archive_before_touching_installation(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            source, archive = folder / "test.tar.gz", folder / "backup.rmb"
            with tarfile.open(source, "w:gz") as tar:
                for name in ("metadata.json", "install/.env", "install/compose.yaml", "redis.rdb"):
                    data = b'{"product":"remnaum","format":1}'
                    member = tarfile.TarInfo(name)
                    member.size = len(data)
                    tar.addfile(member, io.BytesIO(data))
            backup.pack_encrypted(source, archive, KEY.encode())
            target = folder / "store"
            with patch.object(backup, "DATA", folder / "data"), patch.object(backup.subprocess, "run",
                            wraps=subprocess.run) as command:
                with self.assertRaisesRegex(ValueError, "database.dump"):
                    backup.restore_backup(archive, supplied_key=KEY, target_root=target)
                self.assertFalse(any(call.args[0][0] == "docker" for call in command.call_args_list))
            self.assertFalse(target.exists())

    def test_reject_empty_configuration_and_invalid_manifest(self):
        components = {
            "metadata.json": b'{"product":"remnaum","format":1}',
            "install/.env": b"COMPOSE_FILE=compose.yaml",
            "install/compose.yaml": b"services: {}",
            "database.dump": b"PGDMP",
            "redis.rdb": b"REDIS",
        }
        cases = [
            ("install/.env", b"", "Некорректный компонент"),
            ("metadata.json", b'{"product":"remnaum","format":true}', "не принадлежит Remnaum"),
        ]
        for name, content, message in cases:
            with self.subTest(component=name):
                stream = io.BytesIO()
                with tarfile.open(fileobj=stream, mode="w:gz") as tar:
                    for path, data in (components | {name: content}).items():
                        member = tarfile.TarInfo(path)
                        member.size = len(data)
                        tar.addfile(member, io.BytesIO(data))
                stream.seek(0)
                with tarfile.open(fileobj=stream, mode="r:gz", tarinfo=backup.RestoreTarInfo) as tar:
                    paths, _ = backup._scan_backup(tar)
                    with self.assertRaisesRegex(ValueError, message):
                        backup._read_backup_manifest(tar, paths)

    def test_restore_stops_on_failed_app_stop_postgres_or_redis(self):
        components = {
            "metadata.json": b'{"product":"remnaum","format":1}',
            "install/.env": b"COMPOSE_FILE=compose.yaml",
            "install/compose.yaml": b"services: {}",
            "database.dump": b"PGDMP",
            "redis.rdb": b"REDIS",
        }
        for failure in ("stop-app", "postgres", "redis"):
            with self.subTest(stage=failure), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                target = root / "store"
                target.mkdir()
                (target / ".env").write_text("OLD_CONFIG=keep")
                source = root / "archive.tar.gz"
                with tarfile.open(source, "w:gz") as tar:
                    for name, data in components.items():
                        member = tarfile.TarInfo(name)
                        member.size = len(data)
                        tar.addfile(member, io.BytesIO(data))
                calls, staged_paths = [], []

                def run_command(command, **kwargs):
                    calls.append(command)
                    if ((failure == "stop-app" and command[-2:] == ["stop", "app"])
                            or (failure == "postgres" and "pg_restore" in command)
                            or (failure == "redis" and backup._RESTORE_REDIS in command)):
                        self.assertTrue(kwargs["check"])
                        raise subprocess.CalledProcessError(1, command)
                    return subprocess.CompletedProcess(command, 0)

                def stage_archive(_archive, destination, _key):
                    staged_paths.append(destination.parent)
                    shutil.copyfile(source, destination)

                with patch.object(backup, "ROOT", target), patch.object(backup, "DATA", target / "data"), \
                     patch.object(backup, "unpack_encrypted", side_effect=stage_archive), \
                     patch.object(backup.subprocess, "run", side_effect=run_command):
                    if failure == "stop-app":
                        with self.assertRaises(subprocess.CalledProcessError):
                            backup.restore_backup(source, supplied_key=KEY)
                        self.assertEqual((target / ".env").read_text(), "OLD_CONFIG=keep")
                        self.assertEqual(len(calls), 1)
                    else:
                        with self.assertRaisesRegex(RuntimeError, "восстановите аварийную копию"):
                            backup.restore_backup(source, supplied_key=KEY)
                        self.assertFalse(any("--build" in command for command in calls))
                    self.assertFalse(staged_paths[0].exists())

    def test_new_key_is_not_included_in_archive(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "data").mkdir()
            (root / "data" / "backup.key").write_text("supersecret")
            (root / "data" / "backup-settings.json").write_text('{"schedule":"daily"}')
            (root / "install.sh").write_text("bootstrap")
            with patch.object(backup, "ROOT", root), patch.object(backup, "DATA", root / "data"):
                buf = io.BytesIO()
                with tarfile.open(fileobj=buf, mode="w:gz") as archive:
                    backup._collect_source(archive)
                buf.seek(0)
                with tarfile.open(fileobj=buf, mode="r:gz") as archive:
                    names = archive.getnames()
                self.assertIn("install/install.sh", names)
                self.assertIn("install/data/backup-settings.json", names)
                self.assertNotIn("install/data/backup.key", names)

    def assert_rejected_before_extract(self, entries, message, limits=None):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as tar:
            for name, member_type, data in entries:
                member = tarfile.TarInfo(name)
                member.type = member_type
                member.size = len(data)
                tar.addfile(member, io.BytesIO(data))
        stream.seek(0)
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, limits or {}):
            target = Path(folder) / "target"
            with self.assertRaisesRegex(ValueError, message):
                with tarfile.open(fileobj=stream, mode="r", tarinfo=backup.RestoreTarInfo) as tar:
                    backup._safe_extract(tar, target)
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_reject_tar_traversal_absolute_paths_links_and_special_files(self):
        unsafe = [
            ("install/../outside", tarfile.REGTYPE), ("/outside", tarfile.REGTYPE),
            ("install/symlink", tarfile.SYMTYPE), ("install/hardlink", tarfile.LNKTYPE),
            ("install/character", tarfile.CHRTYPE), ("install/fifo", tarfile.FIFOTYPE),
        ]
        for name, kind in unsafe:
            with self.subTest(name=name):
                self.assert_rejected_before_extract([(name, kind, b"")], "Небезопасное содержимое")

    def test_reject_duplicate_paths_after_normalization(self):
        self.assert_rejected_before_extract([
            ("install/folder/file", tarfile.REGTYPE, b"one"),
            ("install//folder/./file", tarfile.REGTYPE, b"two"),
        ], "Дублирующийся путь")

    def test_reject_duplicate_explicit_directories(self):
        self.assert_rejected_before_extract([
            ("install/data", tarfile.DIRTYPE, b""),
            ("install/data/", tarfile.DIRTYPE, b""),
        ], "Дублирующийся путь")

    def test_reject_directory_file_conflict_in_either_order(self):
        entries = [
            ("install/folder/file", tarfile.REGTYPE, b"one"),
            ("install/folder", tarfile.REGTYPE, b"two"),
        ]
        for order in (entries, entries[::-1]):
            self.assert_rejected_before_extract(order, "Конфликт файла и каталога")

    def test_reject_archive_limits_before_unpacking(self):
        entries = [
            ("install/folder/a", tarfile.REGTYPE, b"one"),
            ("install/folder/b", tarfile.REGTYPE, b"two"),
        ]
        cases = [
            ("REMNAUM_RESTORE_MAX_FILE_BYTES", "2", "размера файла"),
            ("REMNAUM_RESTORE_MAX_TOTAL_BYTES", "5", "общего объёма"),
            ("REMNAUM_RESTORE_MAX_PATHS", "3", "файлов и каталогов"),
        ]
        for name, value, error in cases:
            with self.subTest(limit=name):
                self.assert_rejected_before_extract(entries, error, {name: value})

    def test_reject_oversized_extended_header(self):
        self.assert_rejected_before_extract([
            ("extended", tarfile.XHDTYPE, b"x" * (64 * 1024 + 1)),
            ("install/a", tarfile.REGTYPE, b"one"),
        ], "лимит расширенных заголовков")

    def test_reject_links_in_restore_destination(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            external = root / "external"
            external.write_text("keep me")
            for create_link in (os.symlink, os.link):
                linked = root / "config"
                create_link(external, linked)
                with self.assertRaisesRegex(ValueError, "Небезопасный путь"):
                    backup._check_restore_destination(root, {
                        Path("install/config"): tarfile.TarInfo("install/config")
                    })
                linked.unlink()
                self.assertEqual(external.read_text(), "keep me")

    def test_extract_nested_file_with_implicit_directories_and_pax_metadata(self):
        stream = io.BytesIO()
        path = "install/data/" + "я" * 75 + ".txt"
        content = b"hello"
        with tarfile.open(fileobj=stream, mode="w:gz", format=tarfile.PAX_FORMAT) as archive:
            member = tarfile.TarInfo(path)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
            directory = tarfile.TarInfo("install/data")
            directory.type = tarfile.DIRTYPE
            archive.addfile(directory)
        stream.seek(0)
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "target"
            with tarfile.open(fileobj=stream, mode="r:gz", tarinfo=backup.RestoreTarInfo) as archive:
                backup._safe_extract(archive, target)
            self.assertEqual((target / path).read_bytes(), content)

    def test_telegram_upload_streams_archive_in_bounded_chunks(self):
        from unittest.mock import MagicMock

        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "backup.rmb"
            with archive.open("wb") as handle:
                for _ in range(3):
                    handle.write(os.urandom(1024 * 1024))
            connection = MagicMock()
            connection.getresponse.return_value.status = 200
            connection.getresponse.return_value.read.return_value = b'{"ok":true}'

            with patch.object(backup, "_bot_token", return_value="example-token"), \
                 patch.object(backup.http.client, "HTTPSConnection", return_value=connection), \
                 patch.object(Path, "read_bytes", side_effect=AssertionError("must stream")):
                backup.send_to_telegram(archive, "123456789")

            sent = [call.args[0] for call in connection.send.call_args_list]
            self.assertEqual(len(sent), 5)  # header, three 1 MiB blocks, footer
            self.assertEqual(sum(map(len, sent)), archive.stat().st_size + len(sent[0]) + len(sent[-1]))
            self.assertLessEqual(max(map(len, sent)), 1024 * 1024)
            connection.close.assert_called_once()

    def test_telegram_upload_rejection_still_closes_connection(self):
        from unittest.mock import MagicMock

        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "backup.rmb"
            archive.write_bytes(b"encrypted")
            connection = MagicMock()
            connection.getresponse.return_value.status = 503
            with patch.object(backup, "_bot_token", return_value="example-token"), \
                 patch.object(backup.http.client, "HTTPSConnection", return_value=connection):
                with self.assertRaisesRegex(RuntimeError, "HTTP 503"):
                    backup.send_to_telegram(archive, "123456789")
            connection.close.assert_called_once()

    def test_backup_directory_rotation(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(backup, "BACKUPS", Path(temp)):
            for i in range(8):
                (Path(temp) / f"remnaum-full-20260101T00000{i}Z.rmb").write_bytes(b"test")
            backup.remove_old_archives(3)
            self.assertEqual(len(list(Path(temp).glob("*.rmb"))), 3)
            self.assertTrue((Path(temp) / "remnaum-full-20260101T000007Z.rmb").exists())


if __name__ == "__main__":
    unittest.main()
