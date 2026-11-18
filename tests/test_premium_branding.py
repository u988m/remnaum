"""Image validation, bot menu privacy and front-end navigation regressions."""
import base64
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image
from fastapi import HTTPException

from app.services import store_media
from app.bot.handlers import main_menu, _admin_webapp_url
from app.services.bot_identity import bot_username, remember_bot
from app.web import routes


class StoreWelcomeImageTests(unittest.TestCase):
    def make_picture(self, size=(900, 500), fmt="PNG") -> str:
        img = Image.new("RGBA", size, (60, 115, 210, 190))
        buffer = io.BytesIO()
        img.save(buffer, format=fmt)
        return base64.b64encode(buffer.getvalue()).decode()

    def test_upload_jpg_png_webp_and_remove(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root) / "branding"
            with patch.object(store_media, "DATA_DIR", folder), \
                 patch.object(store_media, "WELCOME_IMAGE", folder / "welcome.jpg"):
                url = store_media.save_welcome_image(self.make_picture())
                self.assertTrue(url.startswith("/api/v1/store-profile/welcome-image?v="))
                self.assertTrue(store_media.WELCOME_IMAGE.is_file())
                with Image.open(store_media.WELCOME_IMAGE) as result:
                    self.assertEqual(result.format, "JPEG")
                    self.assertEqual(result.size, (900, 500))
                self.assertEqual(store_media.WELCOME_IMAGE.stat().st_mode & 0o777, 0o600)
                store_media.delete_welcome_image()
                self.assertIsNone(store_media.welcome_image_url())

    def test_rejects_invalid_format_and_large_pixel_count(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.object(store_media, "DATA_DIR", Path(root)), \
                 patch.object(store_media, "WELCOME_IMAGE", Path(root) / "welcome.jpg"):
                for sample in ("not base64!!!", base64.b64encode(b"bad-file").decode()):
                    with self.subTest(sample=sample), self.assertRaises(ValueError):
                        store_media.save_welcome_image(sample)
                with self.assertRaises(ValueError):
                    store_media.save_welcome_image(self.make_picture((180, 120)))
                with self.assertRaises(ValueError):
                    store_media.save_welcome_image(self.make_picture((5000, 4000)))
                self.assertFalse(store_media.WELCOME_IMAGE.exists())


class PremiumMenuTests(unittest.TestCase):
    def test_balanced_user_menu_and_owner_only_admin(self):
        with patch("app.bot.handlers.settings.webapp_url", "https://miniapp.example.com/app"):
            ordinary = main_menu(is_admin=False)
            owner = main_menu(is_admin=True)
        self.assertEqual([len(row) for row in ordinary.inline_keyboard], [2, 2, 2, 2, 1])
        self.assertEqual([len(row) for row in owner.inline_keyboard], [2, 2, 2, 2, 1, 1])
        self.assertFalse(any("управлен" in b.text.lower() for r in ordinary.inline_keyboard for b in r))
        self.assertEqual(owner.inline_keyboard[-1][0].web_app.url, "https://miniapp.example.com/admin")
        callbacks = [b.callback_data for r in ordinary.inline_keyboard for b in r if b.callback_data]
        self.assertIn("menu:bonuses", callbacks)
        self.assertEqual(len(callbacks), len(set(callbacks)))

    def test_bot_identity_prefers_confirmed_getme(self):
        with patch("app.services.bot_identity.settings.telegram_bot_username", "old_bot"):
            remember_bot("new_bot", "VPN Service")
            self.assertEqual(bot_username(), "new_bot")


class BotWelcomeTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_sends_owner_photo_above_the_button_menu(self):
        from app.bot.handlers import start_handler
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=12345, username="customer"),
            text="/start",
            answer_photo=AsyncMock(),
            answer=AsyncMock(),
        )
        profile = {"name": "Личный VPN", "tagline": "Подключение без сложностей",
                   "description": "Всё под рукой"}
        with patch("app.bot.handlers.get_or_create_user", new_callable=AsyncMock) as register, \
             patch("app.bot.handlers.get_store_profile", new_callable=AsyncMock) as branding, \
             patch("app.bot.handlers.welcome_image_path", return_value=Path("/tmp/welcome.jpg")):
            register.return_value = False
            branding.return_value = profile
            await start_handler(message)
        message.answer_photo.assert_awaited_once()
        message.answer.assert_awaited_once()
        photo_caption = message.answer_photo.call_args.kwargs["caption"]
        self.assertIn("Личный VPN", photo_caption)
        markup = message.answer.call_args.kwargs["reply_markup"]
        self.assertEqual(len(markup.inline_keyboard[0]), 2)

class WelcomeImageApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_upload_requires_admin_auth_before_saving(self):
        with self.assertRaises(HTTPException) as error:
            await routes.admin_upload_welcome_image(
                routes.WelcomeImagePayload(data_base64="A" * 36),
                x_telegram_init_data=None,
            )
        self.assertEqual(error.exception.status_code, 401)

    async def test_delete_requires_admin_auth(self):
        with self.assertRaises(HTTPException) as error:
            await routes.admin_delete_welcome_image(x_telegram_init_data=None)
        self.assertEqual(error.exception.status_code, 401)

    async def test_public_profile_discloses_only_image_url(self):
        with patch.object(routes, "get_store_profile", new_callable=AsyncMock) as profile, \
             patch.object(routes, "welcome_image_url", return_value=None):
            profile.return_value = {"name": "Example VPN"}
            value = await routes.public_store_profile()
        self.assertEqual(value, {"name": "Example VPN", "welcome_image_url": None})


if __name__ == "__main__":
    unittest.main()
