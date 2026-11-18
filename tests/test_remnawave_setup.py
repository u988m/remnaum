"""Safe Remnawave autodiscovery tests; no actual panel, network or Docker."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import remnawave_setup as setup
import install as installer

A = "11111111-1111-4111-8111-111111111111"
B = "22222222-2222-4222-8222-222222222222"
SECRET = "A" * 64
WEBHOOK = "https://hook.myvpn.site/webhooks/remnawave"


class RemnawaveSetupTests(unittest.TestCase):
    def test_real_backend_nested_response(self):
        payload = {"response": {"internalSquads": [
            {"uuid": A, "name": "Standard"}, {"uuid": B, "name": "Premium"},
            {"uuid": A, "name": "Duplicate"}, {"uuid": "bad", "name": "Invalid"},
        ]}}
        self.assertEqual(setup.parse_squads(payload), [
            {"uuid": A, "name": "Standard"}, {"uuid": B, "name": "Premium"},
        ])
        self.assertEqual(setup.select_squads(setup.parse_squads(payload), lambda _, default: "2"), B)
        self.assertEqual(setup.select_squads(setup.parse_squads(payload), lambda _, default: "1,2"), A + "," + B)
        self.assertEqual(setup.select_squads([{"uuid": A, "name": "One"}], lambda *a, **kw: ""), A)

    def test_api_discovery_uses_bearer_token(self):
        with patch.object(setup, "_fetch_json", return_value={
            "response": {"internalSquads": [{"uuid": A, "name": "Default"}]}
        }) as fetch:
            result = setup.discover_squads("https://panel.myvpn.site", "secret")
        self.assertEqual(result[0]["uuid"], A)
        fetch.assert_called_once_with("https://panel.myvpn.site/api/internal-squads", "secret")

    def test_unsafe_url_and_invalid_squads_rejected(self):
        for url in ("http://panel.myvpn.site", "https://user:pass@panel.myvpn.site",
                    "https://panel.myvpn.site?access=secret", "https://panel.myvpn.site:444"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                setup.panel_api_url(url)
        for value in ("foo", "hello,", "1111", " "):
            with self.subTest(value=value), self.assertRaises(ValueError):
                setup.validate_uuid_list(value)
        self.assertEqual(setup.validate_uuid_list(A + "," + A), A)

    def test_fallback_to_manual_only_when_api_fails(self):
        existing = {
            "PUBLIC_DOMAIN": "hook.myvpn.site",
            "WEBAPP_DOMAIN": "miniapp.myvpn.site",
            "TELEGRAM_BOT_USERNAME": "test_bot",
            "TELEGRAM_BOT_TOKEN": "123:ABC",
            "ADMIN_IDS": "123456789",
            "REMNAWAVE_BASE_URL": "https://panel.myvpn.site",
            "REMNAWAVE_API_TOKEN": "secret",
            "APP_SECRET_KEY": "old-app-secret",
            "POSTGRES_PASSWORD": "old-db-password",
        }
        with patch.object(installer, "network_exists", return_value=False), \
             patch.object(installer, "local_panel_for_url", return_value=None), \
             patch.object(installer, "discover_squads", return_value=[{"uuid": A, "name": "Default"}]), \
             patch.object(installer, "prompt") as prompt:
            cfg = installer.config_for_install(existing, "panel", "none", 18080)
        self.assertEqual(cfg["REMNAWAVE_ACTIVE_INTERNAL_SQUADS"], A)
        prompt.assert_not_called()

        with patch.object(installer, "network_exists", return_value=False), \
             patch.object(installer, "local_panel_for_url", return_value=None), \
             patch.object(installer, "discover_squads", side_effect=OSError("offline")), \
             patch.object(installer, "prompt", return_value=B):
            cfg = installer.config_for_install(existing, "panel", "none", 18080)
        self.assertEqual(cfg["REMNAWAVE_ACTIVE_INTERNAL_SQUADS"], B)

    def _panel(self, temp):
        root = Path(temp) / "remnawave"
        root.mkdir()
        (root / "docker-compose.yml").write_text("services:\n  remnawave: {}\n")
        env = root / ".env"
        env.write_text(
            "FRONT_END_DOMAIN=panel.myvpn.site\n"
            "WEBHOOK_ENABLED=false\n"
            "WEBHOOK_URL=https://existing.myvpn.site/notify\n"
            "WEBHOOK_SECRET_HEADER=" + SECRET + "\n"
            "OTHER=keep-value\n",
            encoding="utf-8",
        )
        return root, env

    def test_local_panel_detected_only_for_matching_domain(self):
        with tempfile.TemporaryDirectory() as temp:
            root, env = self._panel(temp)
            self.assertEqual(setup.local_panel_for_url("https://panel.myvpn.site", root), env)
            self.assertIsNone(setup.local_panel_for_url("https://other.myvpn.site", root))
            self.assertEqual(setup.panel_webhook_secret(env), SECRET)

    def test_local_webhook_keeps_existing_destinations_and_secret(self):
        with tempfile.TemporaryDirectory() as temp:
            root, env = self._panel(temp)
            changes, modified = setup.plan_panel_webhook(env, WEBHOOK, SECRET)
            self.assertTrue(modified)
            self.assertEqual(changes["WEBHOOK_ENABLED"], "true")
            self.assertEqual(
                changes["WEBHOOK_URL"],
                "https://existing.myvpn.site/notify," + WEBHOOK
            )
            backup = setup.apply_env_changes(env, changes)
            self.assertTrue(backup.exists())
            self.assertIn("OTHER=keep-value", env.read_text())
            self.assertIn("WEBHOOK_URL=https://existing.myvpn.site/notify," + WEBHOOK, env.read_text())
            self.assertEqual(setup.plan_panel_webhook(env, WEBHOOK, SECRET)[1], False)
            self.assertEqual(backup.stat().st_mode & 0o777, 0o600)

    def test_different_secret_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            _, env = self._panel(temp)
            initial = env.read_text()
            with self.assertRaisesRegex(ValueError, "Секреты"):
                setup.plan_panel_webhook(env, WEBHOOK, "B" * 64)
            self.assertEqual(initial, env.read_text())

    def test_webhook_reconfiguration_requires_explicit_consent(self):
        with tempfile.TemporaryDirectory() as temp:
            root, env = self._panel(temp)
            orig = env.read_text()
            with patch.object(setup.subprocess, "run") as runner:
                done = setup.apply_local_panel_webhook(
                    env, WEBHOOK, SECRET, lambda _, default: "н"
                )
            self.assertFalse(done)
            self.assertEqual(orig, env.read_text())
            runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
