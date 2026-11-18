"""Installer unit tests: no Docker daemon or external connectivity required."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import install as installer


class InstallerTests(unittest.TestCase):
    def test_dns_hostname_validation(self):
        self.assertTrue(installer.valid_domain("webhook.myvpn.site"))
        self.assertTrue(installer.valid_domain("vpn-shop.myvpn.site"))
        for value in ("localhost", "localhost:8080", "127.0.0.1", "bad/domain",
                      "evil\nkey", "-bad.example.com", "example.com:443", "webhook.example.com", "app.example.net"):
            with self.subTest(value=value):
                self.assertFalse(installer.valid_domain(value))

    def test_mode_files_never_load_caddy_on_panel(self):
        self.assertEqual(
            installer.mode_files("standalone"),
            "compose.yaml:compose.standalone.yaml",
        )
        self.assertEqual(
            installer.mode_files("panel"),
            "compose.yaml:compose.panel.yaml",
        )
        self.assertEqual(
            installer.mode_files("panel", "remnawave-network"),
            "compose.yaml:compose.panel.yaml:compose.panel-network.yaml",
        )
        with self.assertRaises(ValueError):
            installer.mode_files("unknown")

    def test_configuration_preserves_existing_secrets(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / ".env"
            path.write_text("# keep this comment\nAPP_SECRET_KEY=original-secret\nDEPLOY_MODE=standalone\n",
                            encoding="utf-8")
            installer.write_env({"DEPLOY_MODE": "panel", "COMPOSE_FILE": installer.mode_files("panel")}, path)
            cfg = installer.read_env(path)
            self.assertEqual(cfg["APP_SECRET_KEY"], "original-secret")
            self.assertEqual(cfg["DEPLOY_MODE"], "panel")
            self.assertIn("# keep this comment", path.read_text())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_generated_snippets_do_not_touch_panel(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(installer, "ROOT", Path(temp)):
                installer.generate_proxy_snippets({
                    "PUBLIC_DOMAIN": "webhook.myvpn.site",
                    "WEBAPP_DOMAIN": "app.myvpn.site",
                    "REMNAUM_LOCAL_PORT": "18080",
                    "REMNAUM_PROXY_NETWORK": "remnawave-network",
                })
                generated = Path(temp) / "generated"
                self.assertIn("remnaum-api:8080", (generated / "remnaum.docker.caddy").read_text())
                self.assertIn("127.0.0.1:18080", (generated / "remnaum.host.caddy").read_text())
                self.assertIn("ssl_certificate", (generated / "remnaum.docker.nginx.conf").read_text())
                self.assertEqual(len(list(generated.iterdir())), 4)

    def test_panel_mode_connects_only_to_existing_proxy_network(self):
        existing = {
            "PUBLIC_DOMAIN": "hook.myvpn.site",
            "WEBAPP_DOMAIN": "app.myvpn.site",
            "TELEGRAM_BOT_USERNAME": "test_bot",
            "TELEGRAM_BOT_TOKEN": "1234567:ABC",
            "ADMIN_IDS": "123456789",
            "REMNAWAVE_BASE_URL": "https://panel.myvpn.site",
            "REMNAWAVE_API_TOKEN": "remote-secret",
            "REMNAWAVE_ACTIVE_INTERNAL_SQUADS": "test-id",
            "POSTGRES_PASSWORD": "db-secret",
            "APP_SECRET_KEY": "app-secret",
            "TELEGRAM_WEBHOOK_SECRET": "tg-secret",
            "REMNAWAVE_WEBHOOK_SECRET": "rem-secret",
            "DATABASE_URL": "postgresql+asyncpg://remnaum:db-secret@postgres:5432/remnaum",
        }
        with patch.object(installer, "network_exists", return_value=True):
            cfg = installer.config_for_install(existing, "panel", None, 18080)
        self.assertEqual(cfg["REMNAUM_PROXY_NETWORK"], "remnawave-network")
        self.assertEqual(cfg["APP_SECRET_KEY"], existing["APP_SECRET_KEY"])
        self.assertEqual(cfg["POSTGRES_PASSWORD"], existing["POSTGRES_PASSWORD"])
        self.assertNotIn("standalone", cfg["COMPOSE_FILE"])
        self.assertIn("panel-network.yaml", cfg["COMPOSE_FILE"])

    def test_refuse_panel_domain_collision(self):
        existing = {
            "PUBLIC_DOMAIN": "panel.myvpn.site",
            "WEBAPP_DOMAIN": "app.myvpn.site",
            "TELEGRAM_BOT_USERNAME": "test_bot",
            "TELEGRAM_BOT_TOKEN": "test",
            "ADMIN_IDS": "123",
            "REMNAWAVE_BASE_URL": "https://panel.myvpn.site",
            "REMNAWAVE_API_TOKEN": "secret",
            "REMNAWAVE_ACTIVE_INTERNAL_SQUADS": "uuid",
        }
        with patch.object(installer, "network_exists", return_value=False):
            with self.assertRaisesRegex(ValueError, "Panel"):
                installer.config_for_install(existing, "panel", "none", 18080)


if __name__ == "__main__":
    unittest.main()
