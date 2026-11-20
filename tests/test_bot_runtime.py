import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram import Bot
from aiogram.methods import AnswerCallbackQuery, DeleteMessage
from aiogram.types import Update

from app import main
from app.bot.release_notices import CLOSE_RELEASE, release_keyboard
from app.bot.handlers import main_menu, support_menu
from app.bot.notifications import CLOSE_MENU_NOTICE, with_close


class BotRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_webhook_requests_checkout_events_and_keeps_pending_updates(self):
        with patch.object(main, "bot", AsyncMock()) as bot:
            await main._register_telegram_webhook()
        self.assertIn("pre_checkout_query", bot.set_webhook.call_args.kwargs["allowed_updates"])
        self.assertIn("chat_member", bot.set_webhook.call_args.kwargs["allowed_updates"])
        self.assertIn("my_chat_member", bot.set_webhook.call_args.kwargs["allowed_updates"])
        self.assertFalse(bot.set_webhook.call_args.kwargs["drop_pending_updates"])

    async def test_polling_clears_prior_webhook_without_dropping_updates(self):
        with patch.object(main, "bot", AsyncMock()) as bot, \
             patch.object(main, "setup_bot", AsyncMock()), \
             patch.object(main.settings, "telegram_use_webhook", False), \
             patch.object(main.dispatcher, "start_polling", AsyncMock()) as polling:
            await main._run_telegram()
        bot.delete_webhook.assert_awaited_once_with(drop_pending_updates=False)
        polling.assert_awaited_once_with(
            bot, allowed_updates=main.dispatcher.resolve_used_update_types(), handle_signals=False, close_bot_session=False, tasks_shutdown_timeout=5,
        )


    async def test_close_release_deletes_only_the_notice(self):
        bot = Bot("123:test")
        self.addAsyncCleanup(bot.session.close)
        notice = {
            "message_id": 70,
            "date": 1_700_000_000,
            "chat": {"id": 42, "type": "private"},
            "from": {"id": 123, "first_name": "Remnaum", "is_bot": True},
            "text": "Вышел релиз Remnaum v0.0.9",
            "reply_markup": release_keyboard("v0.0.9"),
        }

        async def click(message, action=CLOSE_RELEASE):
            update = Update.model_validate({
                "update_id": 1,
                "callback_query": {
                    "id": "release-click",
                    "from": {"id": 42, "first_name": "Пользователь", "is_bot": False},
                    "chat_instance": "chat",
                    "data": action,
                    "message": message,
                },
            }, context={"bot": bot})
            await main.dispatcher.feed_update(bot, update)

        with patch.object(bot.session, "make_request", return_value=True) as send:
            await click(notice)
            methods = [call.args[1] for call in send.await_args_list]
            self.assertEqual([type(method) for method in methods], [DeleteMessage, AnswerCallbackQuery])
            self.assertEqual((methods[0].chat_id, methods[0].message_id), (42, 70))

            send.reset_mock()
            await click(notice | {"message_id": 71, "reply_markup": main_menu()})
            send.assert_awaited_once()
            self.assertIsInstance(send.await_args.args[1], AnswerCallbackQuery)


            state = SimpleNamespace(message_id=70, callbacks=[CLOSE_MENU_NOTICE], start_message_id=67, save=AsyncMock())

            @asynccontextmanager
            async def menu_session(*args, **kwargs):
                yield state

            with patch("app.bot.menu.menu_session", menu_session):
                send.reset_mock()
                await click(notice | {"reply_markup": with_close(tracked=True)}, CLOSE_MENU_NOTICE)
                self.assertEqual([type(call.args[1]) for call in send.await_args_list], [DeleteMessage, AnswerCallbackQuery])
                self.assertEqual((state.message_id, state.callbacks, state.start_message_id), (None, [], 67))
                state.save.assert_awaited_once()

    async def test_support_offers_tickets_without_a_contact_placeholder(self):
        with patch("app.bot.handlers.get_store_profile", return_value={"support_contact": " "}), \
             patch.object(main.settings, "webapp_url", "https://miniapp.example.test"):
            text, markup = await support_menu()
        self.assertEqual(text, "💬 <b>Поддержка</b>")
        self.assertEqual(markup.inline_keyboard[0][0].web_app.url,
                         "https://miniapp.example.test?section=tickets")


    async def test_fallback_removes_unknown_commands_and_media_only_in_private_chats(self):
        bot = Bot("123:test")
        self.addAsyncCleanup(bot.session.close)
        user = {"id": 42, "first_name": "Пользователь", "is_bot": False}

        async def receive(message_id, content, *, chat_type="private", sender=user):
            update = Update.model_validate({
                "update_id": message_id,
                "message": {
                    "message_id": message_id, "date": 1_700_000_000,
                    "chat": {"id": 42, "type": chat_type}, "from": sender, **content,
                },
            }, context={"bot": bot})
            await main.dispatcher.feed_update(bot, update)

        photo = {"photo": [{"file_id": "photo", "file_unique_id": "unique", "width": 320, "height": 240}]}
        with patch.object(bot.session, "make_request", return_value=True) as send:
            await receive(1, {"text": "/unknown"})
            await receive(2, photo)
            await receive(3, photo, chat_type="group")
            await receive(4, photo, sender=user | {"is_bot": True})
            await receive(5, {"web_app_data": {"data": "{}", "button_text": "Mini App"}})
            methods = [call.args[1] for call in send.await_args_list]
            self.assertTrue(all(isinstance(method, DeleteMessage) for method in methods))
            self.assertEqual([(method.chat_id, method.message_id) for method in methods], [(42, 1), (42, 2)])
