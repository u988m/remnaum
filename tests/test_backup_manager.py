"""Encrypted full-backup utilities: offline unit tests, no real Docker/Telegram."""
import io
import os
import shutil
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import backup_manager as backup


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

    def test_reject_tar_traversal_and_symlinks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name, member_type in [("../outside", tarfile.REGTYPE), ("install/linked", tarfile.SYMTYPE)]:
                stream = io.BytesIO()
                with tarfile.open(fileobj=stream, mode="w") as archive:
                    item = tarfile.TarInfo(name)
                    item.type = member_type
                    item.linkname = "/etc/passwd" if member_type == tarfile.SYMTYPE else ""
                    item.size = 0
                    archive.addfile(item, io.BytesIO())
                stream.seek(0)
                with tarfile.open(fileobj=stream, mode="r") as archive:
                    with self.assertRaises(ValueError):
                        backup._safe_extract(archive, root / "target")
                self.assertFalse((root / "outside").exists())

    def test_backup_directory_rotation(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(backup, "BACKUPS", Path(temp)):
            for i in range(8):
                (Path(temp) / f"remnaum-full-20260101T00000{i}Z.rmb").write_bytes(b"test")
            backup.remove_old_archives(3)
            self.assertEqual(len(list(Path(temp).glob("*.rmb"))), 3)
            self.assertTrue((Path(temp) / "remnaum-full-20260101T000007Z.rmb").exists())


if __name__ == "__main__":
    unittest.main()
