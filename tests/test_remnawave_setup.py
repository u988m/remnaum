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
        with patch.object(installer, "prompt") as ask:
            self.assertEqual(setup.select_squads(setup.parse_squads(payload), ask), A + "," + B)
            ask.assert_not_called()
        self.assertEqual(setup.select_squads([{"uuid": A, "name": "One"}]), A)
        # An optional explicit manual API remains available for maintenance.
        self.assertEqual(setup.select_squads(setup.parse_squads(payload),
                         lambda _, default: "2", default_all=False), B)

    def test_all_existing_squads_selected_without_keyboard_input(self):
        squads = [{"uuid": A, "name": "Basic"}, {"uuid": B, "name": "Premium"}]
        with patch.object(installer, "prompt") as ask:
            selection = setup.select_squads(squads, ask)
        self.assertEqual(selection, A + "," + B)
        ask.assert_not_called()

    def test_api_discovery_uses_bearer_token(self):
        with patch.object(setup, "_fetch_json", return_value={
            "response": {"internalSquads": [{"uuid": A, "name": "Default"}]}
        }) as fetch:
            result = setup.discover_squads("https://panel.myvpn.site", "secret")
        self.assertEqual(result[0]["uuid"], A)
        fetch.assert_called_once_with("https://panel.myvpn.site/api/internal-squads", "secret")

    def test_empty_squads_is_a_configuration_error(self):
        with self.assertRaisesRegex(ValueError, "нет внутренних групп"):
            setup.select_squads([], lambda *args, **kwargs: "1")

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

        # An existing shop keeps its valid squads during temporary API outages.
        with patch.object(installer, "network_exists", return_value=False), \
             patch.object(installer, "local_panel_for_url", return_value=None), \
             patch.object(installer, "discover_squads", side_effect=OSError("offline")), \
             patch.object(installer, "prompt") as ask:
            cfg = installer.config_for_install(existing | {
                "REMNAWAVE_ACTIVE_INTERNAL_SQUADS": A
            }, "panel", "none", 18080)
        self.assertEqual(cfg["REMNAWAVE_ACTIVE_INTERNAL_SQUADS"], A)
        ask.assert_not_called()
        # A genuinely new installation without saved IDs may still enter a UUID.
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

    def test_installer_reuses_local_panel_webhook_secret(self):
        existing = {
            "PUBLIC_DOMAIN": "hook.myvpn.site",
            "WEBAPP_DOMAIN": "miniapp.myvpn.site",
            "TELEGRAM_BOT_USERNAME": "test_bot",
            "TELEGRAM_BOT_TOKEN": "123:ABC",
            "ADMIN_IDS": "123456789",
            "REMNAWAVE_BASE_URL": "https://panel.myvpn.site",
            "REMNAWAVE_API_TOKEN": "remote-secret",
            "REMNAWAVE_ACTIVE_INTERNAL_SQUADS": A,
        }
        with tempfile.TemporaryDirectory() as temp:
            root, env = self._panel(temp)
            with patch.object(installer, "network_exists", return_value=False), \
                 patch.object(installer, "local_panel_for_url", return_value=env), \
                 patch.object(installer, "prompt") as prompt:
                config = installer.config_for_install(existing, "panel", "none", 18080)
            self.assertEqual(config["REMNAWAVE_WEBHOOK_SECRET"], SECRET)
            prompt.assert_not_called()

    def test_installer_detects_official_panel_domain(self):
        existing = {
            "PUBLIC_DOMAIN": "hook.myvpn.site",
            "WEBAPP_DOMAIN": "miniapp.myvpn.site",
            "TELEGRAM_BOT_USERNAME": "test_bot",
            "TELEGRAM_BOT_TOKEN": "123:ABC",
            "ADMIN_IDS": "123456789",
            "REMNAWAVE_BASE_URL": "",
            "REMNAWAVE_API_TOKEN": "remote-secret",
            "REMNAWAVE_ACTIVE_INTERNAL_SQUADS": A,
        }
        with tempfile.TemporaryDirectory() as temp:
            _, env = self._panel(temp)
            with patch.object(installer, "network_exists", return_value=False), \
                 patch.object(installer, "discover_local_panel", return_value=env), \
                 patch.object(installer, "local_panel_for_url", return_value=env), \
                 patch.object(installer, "prompt") as prompt:
                config = installer.config_for_install(existing, "panel", "none", 18080)
            self.assertEqual(config["REMNAWAVE_BASE_URL"], "https://panel.myvpn.site")
            self.assertEqual(config["REMNAWAVE_WEBHOOK_SECRET"], SECRET)
            prompt.assert_not_called()

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

    def test_public_webhook_requires_correct_health_and_tls(self):
        from unittest.mock import MagicMock
        response = MagicMock()
        response.status = 200
        response.read.return_value = b'{"status":"ok","service":"remnaum"}'
        response.__enter__.return_value = response
        with patch.object(setup.urllib.request, "urlopen", return_value=response):
            self.assertTrue(setup.public_webhook_ready(WEBHOOK))
        response.read.return_value = b'{"status":"unhealthy"}'
        with patch.object(setup.urllib.request, "urlopen", return_value=response):
            self.assertFalse(setup.public_webhook_ready(WEBHOOK))
        self.assertFalse(setup.public_webhook_ready("http://hook.myvpn.site/webhooks/remnawave"))

    def test_failed_panel_apply_rolls_back_old_env(self):
        from subprocess import CalledProcessError, CompletedProcess
        with tempfile.TemporaryDirectory() as temp:
            root, env = self._panel(temp)
            old = env.read_text()
            def fake_run(command):
                if command[-1] == "--quiet":
                    raise CalledProcessError(1, command)
                return CompletedProcess(command, 0)
            with patch.object(setup.subprocess, "run", return_value=CompletedProcess(
                ["docker", "compose"], 0, stdout="remnawave\n",
            )):
                with self.assertRaises(CalledProcessError):
                    setup.apply_local_panel_webhook(
                        env, WEBHOOK, SECRET, lambda _, default: "Д",
                        run_panel_command=fake_run,
                    )
            self.assertEqual(env.read_text(), old)
            self.assertTrue(list(root.glob(".env.remnaum-backup-*")))

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
