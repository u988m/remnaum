import unittest
from unittest.mock import AsyncMock, patch

from aiogram import Bot
from aiogram.methods import AnswerCallbackQuery, DeleteMessage
from aiogram.types import Update

from app import main
from app.bot.release_notices import CLOSE_RELEASE, release_keyboard
from app.bot.handlers import main_menu


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

        async def click(message):
            update = Update.model_validate({
                "update_id": 1,
                "callback_query": {
                    "id": "release-click",
                    "from": {"id": 42, "first_name": "Пользователь", "is_bot": False},
                    "chat_instance": "chat",
                    "data": CLOSE_RELEASE,
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
