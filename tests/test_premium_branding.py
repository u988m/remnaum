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

    def test_square_logo_has_separate_storage_and_url(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(store_media, "DATA_DIR", Path(folder)), \
                 patch.object(store_media, "WELCOME_IMAGE", Path(folder) / "welcome.jpg"):
                store_media.save_welcome_image(self.make_picture())
                self.assertIn("/logo-image?v=", store_media.save_logo_image(self.make_picture((800, 800))))
                with Image.open(store_media.image_path("logo")) as logo:
                    self.assertEqual(logo.size, (512, 512))
                store_media.delete_logo_image()
                self.assertIsNone(store_media.logo_image_url())
                self.assertTrue(store_media.welcome_image_path().is_file())

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
    async def test_start_sends_one_combined_photo_caption_and_keyboard(self):
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
             patch("app.bot.handlers.get_user_subscription", AsyncMock(return_value={"status": "active"})), \
             patch("app.bot.handlers.welcome_image_path", return_value=Path("/tmp/welcome.jpg")):
            register.return_value = False
            branding.return_value = profile
            await start_handler(message)
        message.answer_photo.assert_awaited_once()
        message.answer.assert_not_awaited()
        photo_caption = message.answer_photo.call_args.kwargs["caption"]
        self.assertIn("Личный VPN", photo_caption)
        self.assertIn("Активна", photo_caption)
        markup = message.answer_photo.call_args.kwargs["reply_markup"]
        self.assertEqual(len(markup.inline_keyboard[0]), 2)

    async def test_photo_menu_callback_opens_section_without_edit_text(self):
        from app.bot.handlers import edit_menu_message
        message = SimpleNamespace(photo=[object()], answer=AsyncMock(), delete=AsyncMock(), edit_text=AsyncMock())
        markup = main_menu(is_admin=False)
        await edit_menu_message(message, "Доступные тарифы", reply_markup=markup)
        message.answer.assert_awaited_once_with("Доступные тарифы", reply_markup=markup)
        message.delete.assert_awaited_once()
        message.edit_text.assert_not_awaited()

    async def test_text_menu_callback_edits_existing_message(self):
        from app.bot.handlers import edit_menu_message
        message = SimpleNamespace(edit_text=AsyncMock())
        await edit_menu_message(message, "Тарифы", reply_markup=main_menu())
        message.edit_text.assert_awaited_once()

    async def test_back_home_reuses_photo_caption(self):
        from app.bot.handlers import home_handler
        message = SimpleNamespace(photo=[object()], edit_caption=AsyncMock(), edit_text=AsyncMock())
        callback = SimpleNamespace(message=message, from_user=SimpleNamespace(id=123), answer=AsyncMock())
        profile = {"name": "VPN", "tagline": "Ваш VPN", "description": "Описание"}
        with patch("app.bot.handlers.get_store_profile", AsyncMock(return_value=profile)), \
             patch("app.bot.handlers.get_user_subscription", AsyncMock(return_value=None)), \
             patch("app.bot.handlers.welcome_image_path", return_value=Path("/tmp/test.jpg")):
            await home_handler(callback)
        message.edit_caption.assert_awaited_once()
        message.edit_text.assert_not_awaited()

    async def test_ready_plan_review_binds_price_and_device_count_to_confirmation(self):
        from app.bot.handlers import fixed_plan_handler
        from app.db.models import TariffPlan
        from app.services.plans import plan_revision

        plan = TariffPlan(
            id=4, title="Безлимит", duration_days=30, traffic_gb=0, device_limit=2,
            price_rub_minor=39000, extra_device_price_rub_minor=9000,
            price_stars=190, extra_device_price_stars=None, is_active=True,
        )
        message = SimpleNamespace(edit_text=AsyncMock())
        callback = SimpleNamespace(message=message, answer=AsyncMock(), data="plan:select:4:2")
        with patch("app.bot.handlers.get_plan", AsyncMock(return_value=plan)):
            await fixed_plan_handler(callback)
        review = message.edit_text.call_args.args[0]
        self.assertIn("Безлимит", review)
        self.assertIn("Устройств добавлено: 2", review)
        self.assertIn("570.00 ₽", review)
        keyboard = message.edit_text.call_args.kwargs["reply_markup"]
        self.assertIn("plan:confirm:4:2:" + plan_revision(plan), [
            button.callback_data for row in keyboard.inline_keyboard for button in row
        ])

    async def test_start_keeps_menu_when_photo_missing_or_telegram_rejects_upload(self):
        from app.bot.handlers import start_handler
        profile = {"name": "Личный VPN", "tagline": "Подключение", "description": "Описание"}
        for image in (None, Path("/tmp/missing-welcome.jpg")):
            with self.subTest(image=image):
                message = SimpleNamespace(
                    from_user=SimpleNamespace(id=12345), text="/start",
                    answer_photo=AsyncMock(side_effect=RuntimeError("Telegram rejected photo")),
                    answer=AsyncMock(),
                )
                with patch("app.bot.handlers.get_or_create_user", AsyncMock(return_value=False)), \
                     patch("app.bot.handlers.get_store_profile", AsyncMock(return_value=profile)), \
                     patch("app.bot.handlers.get_user_subscription", AsyncMock(return_value=None)), \
                     patch("app.bot.handlers.welcome_image_path", return_value=image):
                    if image:
                        with self.assertLogs("app.bot.handlers", level="WARNING"):
                            await start_handler(message)
                        message.answer_photo.assert_awaited_once()
                    else:
                        await start_handler(message)
                        message.answer_photo.assert_not_awaited()
                message.answer.assert_awaited_once()
                self.assertIn("Описание", message.answer.call_args.args[0])
                self.assertIsNotNone(message.answer.call_args.kwargs["reply_markup"])


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
