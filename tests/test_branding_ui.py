"""UI and branding regressions for the Telegram bot, Mini App and admin."""
import pathlib
import unittest

from app.bot.handlers import main_menu
from app.services.store_profile import validate_profile, DEFAULT_PROFILE

ROOT = pathlib.Path(__file__).resolve().parents[1] / "app" / "web"


class BrandedInterfaceTests(unittest.TestCase):
    def test_start_menu_is_two_column_and_routes_exist(self):
        keyboard = main_menu()
        entries = [button for row in keyboard.inline_keyboard for button in row]
        self.assertTrue(all(len(row) == 2 for row in keyboard.inline_keyboard[:-1]))
        commands = {entry.callback_data for entry in entries if entry.callback_data}
        self.assertGreaterEqual(len(commands), 10)
        for name in ("menu:plans", "menu:profile", "menu:subscription", "menu:devices",
                     "menu:purchases", "menu:referral", "menu:promo", "menu:trial",
                     "menu:connect", "menu:support"):
            self.assertIn(name, commands)

    def test_safe_welcome_photo_id_and_brand_name(self):
        current = validate_profile({"name": "Мой VPN", "welcome_photo_file_id": "AgAC_123-X"})
        self.assertEqual(current["name"], "Мой VPN")
        self.assertEqual(current["welcome_photo_file_id"], "AgAC_123-X")
        self.assertEqual(DEFAULT_PROFILE["welcome_photo_file_id"], "")
        for identifier in ("../../etc/passwd", "https://server/secret", "bad file id", "A" * 300):
            with self.subTest(identifier=identifier):
                with self.assertRaises(ValueError):
                    validate_profile({"welcome_photo_file_id": identifier})

    def test_navigation_and_photo_controls_exist_in_templates(self):
        mini = (ROOT / "templates" / "miniapp.html").read_text(encoding="utf-8")
        admin = (ROOT / "templates" / "admin.html").read_text(encoding="utf-8")
        mini_js = (ROOT / "static" / "miniapp.js").read_text(encoding="utf-8")
        admin_js = (ROOT / "static" / "admin.js").read_text(encoding="utf-8")
        for screen in ("home", "plans", "account", "bonuses"):
            self.assertIn(f'data-screen="{screen}"', mini)
            self.assertIn(f'data-open-screen="{screen}"', mini)
        self.assertIn("function showScreen(", mini_js)
        self.assertIn("welcome-photo-input", admin)
        self.assertIn("welcome-photo-remove", admin)
        self.assertIn("/admin/api/welcome-photo", admin_js)
        self.assertIn("admin-store-name", admin)
        self.assertIn("admin-store-name", admin_js)


if __name__ == "__main__":
    unittest.main()
