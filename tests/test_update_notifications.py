"""Unit tests for the VPS-only GitHub update checker. No live network."""
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "check_updates.py"
spec = importlib.util.spec_from_file_location("check_updates_test_module", SOURCE)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class UpdateNotificationTests(unittest.TestCase):
    def test_notice_has_links_only_in_buttons(self):
        with patch.object(checker, "urlopen", return_value=io.BytesIO(b'{"ok": true}')) as send:
            self.assertTrue(checker.send_notice("test-token", 1234))
        payload = parse_qs(send.call_args.args[0].data.decode())
        self.assertNotIn("https://", payload["text"][0])
        self.assertIn("sudo sh install.sh", payload["text"][0])
        self.assertEqual(json.loads(payload["reply_markup"][0])["inline_keyboard"], [[
            {"text": "📢 Что нового", "url": "https://t.me/remnaum/4"},
            {"text": "GitHub", "url": "https://github.com/u988m/remnaum"},
        ]])

    def test_main_commit_change(self):
        local = "1" * 40
        remote = "2" * 40
        with patch.object(checker, "git", side_effect=[
            "main", local, f"{remote}\trefs/heads/main"
        ]):
            self.assertEqual(checker.available_update(), (remote, local))
        with patch.object(checker, "git", side_effect=[
            "main", local, f"{local}\trefs/heads/main"
        ]):
            self.assertIsNone(checker.available_update())
        with patch.object(checker, "git", return_value="feature"):
            self.assertIsNone(checker.available_update())

    def test_private_repository_unavailable_does_not_expose_token(self):
        with patch.object(checker, "available_update", return_value=None):
            self.assertEqual(checker.check_and_notify(), 0)

    def test_notify_admin_once_per_new_remote_sha(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder) / "alerts.json"
            secret_env = Path(folder) / ".env"
            secret_env.write_text("TELEGRAM_BOT_TOKEN=1234:secret\nADMIN_IDS=111,222\n")
            with patch.object(checker, "ROOT", Path(folder)), \
                 patch.object(checker, "STATE_FILE", state), \
                 patch.object(checker, "available_update", return_value=("a"*40, "b"*40)), \
                 patch.object(checker, "send_notice", return_value=True) as sender:
                self.assertEqual(checker.check_and_notify(), 0)
                self.assertEqual(sender.call_count, 2)
                self.assertEqual(checker.check_and_notify(), 0)
                self.assertEqual(sender.call_count, 2)
                self.assertEqual(checker.read_state(), {"111": "a"*40, "222": "a"*40})

    def test_no_duplicate_marker_for_failed_message(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, ".env").write_text("TELEGRAM_BOT_TOKEN=example\nADMIN_IDS=111\n")
            with patch.object(checker, "ROOT", Path(folder)), \
                 patch.object(checker, "STATE_FILE", Path(folder) / "alerts.json"), \
                 patch.object(checker, "available_update", return_value=("a"*40, "b"*40)), \
                 patch.object(checker, "send_notice", return_value=False):
                checker.check_and_notify()
                self.assertEqual(checker.read_state(), {})


if __name__ == "__main__":
    unittest.main()
