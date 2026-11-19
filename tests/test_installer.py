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

    def test_existing_installation_prompts_only_for_missing_bot_username(self):
        old = {
            "PUBLIC_DOMAIN": "hook.myvpn.site",
            "WEBAPP_DOMAIN": "app.myvpn.site",
            "TELEGRAM_BOT_USERNAME": "",
            "TELEGRAM_BOT_TOKEN": "1234567:ABC",
            "ADMIN_IDS": "123456789",
            "REMNAWAVE_BASE_URL": "https://panel.myvpn.site",
            "REMNAWAVE_API_TOKEN": "remote-secret",
            "REMNAWAVE_ACTIVE_INTERNAL_SQUADS": "test-id",
            "APP_SECRET_KEY": "keep-this-key",
            "POSTGRES_PASSWORD": "keep-this-db-password",
            "DATABASE_URL": "postgresql+asyncpg://remnaum:keep-this-db-password@postgres:5432/remnaum",
        }
        with patch.object(installer, "network_exists", return_value=False), \
             patch.object(installer, "identify_telegram_bot", return_value=None):
            with patch.object(installer, "prompt", return_value="test_bot") as ask:
                cfg = installer.config_for_install(old, "panel", "none", 18080)
        ask.assert_called_once()
        self.assertEqual(cfg["TELEGRAM_BOT_USERNAME"], "test_bot")
        self.assertEqual(cfg["APP_SECRET_KEY"], "keep-this-key")
        self.assertEqual(cfg["POSTGRES_PASSWORD"], "keep-this-db-password")

    def test_bot_username_is_discovered_without_manual_entry(self):
        previous = {
            "PUBLIC_DOMAIN": "hook.myvpn.site",
            "WEBAPP_DOMAIN": "miniapp.myvpn.site",
            "TELEGRAM_BOT_USERNAME": "",
            "TELEGRAM_BOT_TOKEN": "123456789:MOCK_TOKEN",
            "ADMIN_IDS": "123456789",
            "REMNAWAVE_BASE_URL": "https://panel.myvpn.site",
            "REMNAWAVE_API_TOKEN": "mock",
            "REMNAWAVE_ACTIVE_INTERNAL_SQUADS": "known-id",
            "APP_SECRET_KEY": "already-set",
            "POSTGRES_PASSWORD": "already-set",
        }
        with patch.object(installer, "network_exists", return_value=False), \
             patch.object(installer, "identify_telegram_bot", return_value=("real_bot", "Remnaum Shop")), \
             patch.object(installer, "prompt") as ask:
            cfg = installer.config_for_install(previous, "panel", "none", 18080)
        self.assertEqual(cfg["TELEGRAM_BOT_USERNAME"], "real_bot")
        ask.assert_not_called()

    def test_existing_mode_is_preserved_when_reconfiguring(self):
        self.assertEqual(
            installer.choose_mode({"DEPLOY_MODE": "panel"}, None), "panel"
        )

    def test_global_remnaum_launcher_works_from_any_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "remnaum"
            root.mkdir()
            launcher = Path(temp) / "bin" / "remnaum"
            launcher.parent.mkdir()
            with patch.object(installer, "ROOT", root), \
                 patch.object(installer.os, "geteuid", return_value=0):
                installer.install_global_command(destination=launcher)
            content = launcher.read_text()
            self.assertIn("cd " + str(root), content)
            self.assertIn("exec sh ./install.sh", content)
            self.assertIn("sudo /usr/local/bin/remnaum", content)
            self.assertEqual(launcher.stat().st_mode & 0o777, 0o755)

    def test_backup_menu_is_available_even_before_installation(self):
        with patch.object(installer, "prompt", return_value="3"):
            self.assertEqual(installer.choose_action({}, None), "backup")
        with patch.object(installer, "prompt", return_value="3"):
            self.assertEqual(installer.choose_action({"DEPLOY_MODE": "panel"}, None), "backup")

    def test_action_menu_install_and_update(self):
        with patch.object(installer, "prompt", return_value="1"):
            self.assertEqual(installer.choose_action({}, None), "install")
        with patch.object(installer, "prompt", return_value="2"):
            self.assertEqual(installer.choose_action({"DEPLOY_MODE": "panel"}, None), "update")
        with patch.object(installer, "prompt", return_value="2"):
            with self.assertRaises(ValueError):
                installer.choose_action({}, None)
        self.assertEqual(installer.choose_action({}, "install"), "install")
        with self.assertRaises(ValueError):
            installer.choose_action({}, "update")

    def test_update_requires_existing_config_before_touching_git(self):
        with self.assertRaises(ValueError):
            installer.perform_update({"DEPLOY_MODE": "panel"})

    def test_upgrade_keeps_legacy_squad_configuration(self):
        script = (Path(__file__).resolve().parents[1] / "update.sh").read_text()
        self.assertNotIn("refresh_all_remnawave_squads(read_env())", script)

    def test_update_runs_backup_and_pull_then_health_check(self):
        with patch.object(installer, "docker_ready", return_value=True), \
             patch.object(installer.shutil, "which", return_value="/usr/bin/git"), \
             patch.object(installer, "run") as runner, \
             patch.object(installer, "wait_for_app_health", return_value=True), \
             patch.object(installer, "offer_backup_key"), \
             patch.object(installer, "install_global_command"), \
             patch.object(installer.backups, "configure_timer", return_value=True), \
             patch.object(installer, "install_update_timer", return_value=True):
            self.assertEqual(installer.perform_update({
                "DEPLOY_MODE": "panel",
                "COMPOSE_FILE": "compose.yaml:compose.panel.yaml",
            }), 0)
        self.assertEqual(runner.call_args_list[0].args[0], ["bash", "update.sh"])
        self.assertEqual(runner.call_args_list[1].args[0], ["docker", "compose", "ps"])

    def test_installer_health_retries_then_succeeds(self):
        from subprocess import CompletedProcess
        with patch.object(installer, "run", side_effect=[
            CompletedProcess([], 1), CompletedProcess([], 0)
        ]) as runner, patch.object(installer.time, "sleep"):
            self.assertTrue(installer.wait_for_app_health(2))
        self.assertEqual(runner.call_count, 2)

    def test_auto_placement_copies_checkout_without_existing_secrets(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source"
            source.mkdir()
            (source / "scripts").mkdir()
            (source / "scripts" / "install.py").write_text("# installer")
            (source / ".env").write_text("TELEGRAM_BOT_TOKEN=should-not-copy")
            (source / "install.sh").write_text("#!/bin/sh")
            (source / "data").mkdir()
            target = Path(temp) / "opt" / "remnaum"
            with patch.object(installer, "ROOT", source), \
                 patch.dict(installer.os.environ, {"SUDO_USER": "root"}), \
                 patch.object(installer.os, "execv") as exec_call:
                installer.choose_install_dir({}, str(target))
            self.assertTrue((target / "install.sh").is_file())
            self.assertFalse((target / ".env").exists())
            self.assertFalse((target / "data").exists())
            self.assertTrue(exec_call.called)

    def test_deny_overwriting_an_existing_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            dest = Path(temp) / "existing"
            dest.mkdir()
            (dest / "keep.txt").write_text("real business data")
            with patch.object(installer, "ROOT", Path(temp) / "checkout"):
                with self.assertRaisesRegex(ValueError, "содержит данные"):
                    installer.choose_install_dir({}, str(dest))
            self.assertEqual((dest / "keep.txt").read_text(), "real business data")

    def test_caddy_block_for_separate_webhook_and_app_domains(self):
        block = installer.caddy_site_block({
            "PUBLIC_DOMAIN": "webhook.myvpn.site",
            "WEBAPP_DOMAIN": "miniapp.myvpn.site",
        })
        self.assertIn("https://webhook.myvpn.site", block)
        self.assertIn("https://miniapp.myvpn.site", block)
        self.assertIn("remnaum-api:8080", block)
        with self.assertRaises(ValueError):
            installer.caddy_site_block({
                "PUBLIC_DOMAIN": "localhost",
                "WEBAPP_DOMAIN": "miniapp.myvpn.site",
            })

    def test_port_conflict_chooses_next_safe_loopback_port(self):
        with patch.object(installer, "loopback_available", side_effect=lambda p: p == 18082):
            self.assertEqual(installer.first_free_loopback_port(18080), 18082)

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
