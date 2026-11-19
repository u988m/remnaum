import unittest
from unittest.mock import AsyncMock, patch

from app import main


class BotRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_webhook_requests_checkout_events_and_keeps_pending_updates(self):
        with patch.object(main, "bot", AsyncMock()) as bot:
            await main._register_telegram_webhook()
        self.assertIn("pre_checkout_query", bot.set_webhook.call_args.kwargs["allowed_updates"])
        self.assertFalse(bot.set_webhook.call_args.kwargs["drop_pending_updates"])

    async def test_polling_clears_prior_webhook_without_dropping_updates(self):
        with patch.object(main, "bot", AsyncMock()) as bot, \
             patch.object(main, "setup_bot", AsyncMock()), \
             patch.object(main.settings, "telegram_use_webhook", False), \
             patch.object(main.dispatcher, "start_polling", AsyncMock()) as polling:
            await main._run_telegram()
        bot.delete_webhook.assert_awaited_once_with(drop_pending_updates=False)
        polling.assert_awaited_once_with(
            bot, handle_signals=False, close_bot_session=False, tasks_shutdown_timeout=5,
        )
