"""Track the current menu in each private Telegram chat."""
import json
import logging
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from aiogram import BaseMiddleware, Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from redis.asyncio import Redis
from redis.exceptions import LockError

from app.config import settings
from app.services.users import BLOCKED_MESSAGE, customer_is_blocked

logger = logging.getLogger(__name__)
redis = Redis.from_url(settings.redis_url, decode_responses=True)


@dataclass
class ActiveMenu:
    bot: Bot
    chat_id: int
    key: str
    message_id: int | None = None
    callbacks: list[str] = field(default_factory=list)

    async def remember(self, message_id: int, markup: InlineKeyboardMarkup | None, previous_id: int | None = None):
        old_ids = {self.message_id, previous_id} - {None, message_id}
        self.message_id = message_id
        self.callbacks = [
            button.callback_data for row in markup.inline_keyboard for button in row if button.callback_data
        ] if markup else []
        await redis.set(self.key, json.dumps({"message_id": message_id, "callbacks": self.callbacks}))
        for old_id in old_ids:
            await self.remove(old_id)

    async def remove(self, message_id: int, *, menu: bool = True):
        try:
            await self.bot.delete_message(self.chat_id, message_id)
        except TelegramAPIError:
            if menu:
                try:
                    await self.bot.edit_message_reply_markup(chat_id=self.chat_id, message_id=message_id, reply_markup=None)
                except TelegramAPIError:
                    logger.info("Could not remove menu %s from Telegram chat %s", message_id, self.chat_id)


current_menu: ContextVar[ActiveMenu | None] = ContextVar("current_menu", default=None)


@asynccontextmanager
async def menu_session(bot: Bot, chat_id: int, *, wait: float = 0.3):
    key = f"remnaum:bot-menu:{bot.id}:{chat_id}"
    async with redis.lock(key + ":lock", timeout=90, blocking_timeout=wait):
        stored = await redis.get(key)
        state = ActiveMenu(bot, chat_id, key, **(json.loads(stored) if stored else {}))
        token = current_menu.set(state)
        try:
            yield state
        finally:
            current_menu.reset(token)


async def remember_menu(
    message: Message, markup: InlineKeyboardMarkup | None, *, previous: Message | None = None,
) -> None:
    state = current_menu.get()
    if state is not None:
        await state.remember(message.message_id, markup, previous.message_id if previous is not None else None)
    elif previous is not None:
        try:
            await previous.delete()
        except TelegramAPIError:
            logger.info("Could not remove replaced Telegram menu")


async def remove_command(message: Message) -> None:
    if state := current_menu.get():
        await state.remove(message.message_id, menu=False)


class MenuMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        is_callback = isinstance(event, CallbackQuery)
        message = event.message if is_callback else event
        if not isinstance(message, Message):
            if is_callback:
                await event.answer("Откройте меню командой /start.")
            return None
        if message.successful_payment:
            return await handler(event, data)
        actor = event.from_user if is_callback else message.from_user
        blocked = actor is not None and await customer_is_blocked(actor.id)
        if blocked and is_callback:
            await event.answer(BLOCKED_MESSAGE, show_alert=True)
            return None
        if message.chat.type != "private":
            return None if blocked else await handler(event, data)
        try:
            async with menu_session(data["bot"], message.chat.id, wait=0.3 if is_callback else 60) as state:
                if blocked:
                    sent = await message.answer(BLOCKED_MESSAGE)
                    await remember_menu(sent, None)
                    await remove_command(message)
                    return None
                if is_callback and state.message_id is not None:
                    if state.message_id != message.message_id:
                        await event.answer("Используйте кнопки текущего меню.")
                        if message.from_user and message.from_user.id == state.bot.id:
                            await state.remove(message.message_id)
                        return None
                    if event.data not in state.callbacks:
                        await event.answer("Используйте кнопки текущего меню.")
                        return None
                return await handler(event, data)
        except LockError:
            if is_callback:
                await event.answer("Предыдущее действие ещё выполняется.")
            return None
