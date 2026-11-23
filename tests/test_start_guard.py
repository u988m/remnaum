"""Regression tests for Telegram /start message preservation and recovery."""
import json
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram import Bot
from aiogram.types import Message

from app.bot import handlers, menu


class StartCommandProtectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_remember_start_never_deletes_commands_and_keeps_newest_id(self):
        bot = SimpleNamespace(delete_message=AsyncMock())
        state = menu.ActiveMenu(bot=bot, chat_id=42, key="test")
        store = SimpleNamespace(set=AsyncMock())

        with patch.object(menu, "redis", store):
            await state.remember_start(10)
            await state.remember_start(12)
            await state.remember_start(11)

        self.assertEqual(state.start_message_id, 12)
        self.assertEqual(json.loads(store.set.await_args.args[1])["start_message_id"], 12)
        self.assertEqual(store.set.await_count, 3)
        bot.delete_message.assert_not_awaited()

    async def test_command_cleanup_and_fallback_never_delete_start(self):
        remove = AsyncMock()
        state = SimpleNamespace(remove=remove)
        token = menu.current_menu.set(state)
        try:
            message = SimpleNamespace(text="/start ref_42", message_id=5, delete=AsyncMock())
            await menu.remove_command(message)
            await handlers.delete_unhandled_message(message)
            message.text = "/start@remnaum_bot"
            await menu.remove_command(message)
            await handlers.delete_unhandled_message(message)
            remove.assert_not_awaited()
            message.delete.assert_not_awaited()

            message.text = "/help"
            await menu.remove_command(message)
            await handlers.delete_unhandled_message(message)
            remove.assert_awaited_once_with(5, menu=False)
            message.delete.assert_awaited_once()
        finally:
            menu.current_menu.reset(token)

    async def test_menu_cleanup_never_deletes_recorded_start(self):
        bot = SimpleNamespace(delete_message=AsyncMock())
        state = menu.ActiveMenu(bot=bot, chat_id=42, key="test", start_message_id=15)
        await state.remove(15)
        bot.delete_message.assert_not_awaited()

    async def test_replacement_without_state_preserves_start(self):
        previous = SimpleNamespace(text="/start referral", delete=AsyncMock())
        message = SimpleNamespace(message_id=17)
        await menu.remember_menu(message, None, previous=previous)
        previous.delete.assert_not_awaited()

    async def test_replayed_start_rebuilds_missing_menu(self):
        bot = Bot("123:test")
        self.addAsyncCleanup(bot.session.close)
        key = f"remnaum:bot-menu:{bot.id}:42"
        store = {
            key: json.dumps({"message_id": 80, "callbacks": [], "start_message_id": 50}),
        }

        @asynccontextmanager
        async def lock(*args, **kwargs):
            yield

        redis = SimpleNamespace(
            lock=lock,
            get=AsyncMock(side_effect=store.get),
            set=AsyncMock(side_effect=store.__setitem__),
        )
        message = Message.model_validate({
            "message_id": 50,
            "date": 1_700_000_000,
            "chat": {"id": 42, "type": "private"},
            "from": {"id": 42, "first_name": "Покупатель", "is_bot": False},
            "text": "/start",
        }, context={"bot": bot})
        handler = AsyncMock()
        middleware = menu.MenuMiddleware()
        with (
            patch.object(menu, "redis", redis),
            patch.object(menu, "customer_is_blocked", return_value=False),
            patch.object(menu, "get_operations", return_value={"required_channel": ""}),
        ):
            # Duplicate update with an active menu is ignored.
            await middleware(handler, message, {"bot": bot})
            handler.assert_not_awaited()

            # When the menu is closed/cleared, the same /start recovers it.
            store[key] = json.dumps({"message_id": None, "callbacks": [], "start_message_id": 50})
            await middleware(handler, message, {"bot": bot})
            handler.assert_awaited_once()
