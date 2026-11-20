"""Dismiss individual Telegram notices without routing their buttons through the menu."""
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from redis.exceptions import LockError

from app.bot.buttons import button
from app.bot.release_notices import CLOSE_RELEASE

router = Router(name="notifications")
CLOSE_NOTICE = "notice:close"
CLOSE_MENU_NOTICE = "notice:close-menu"
CLOSE_ACTIONS = {CLOSE_NOTICE, CLOSE_MENU_NOTICE, CLOSE_RELEASE}


def with_close(markup: InlineKeyboardMarkup | None = None, *, tracked: bool = False) -> InlineKeyboardMarkup:
    rows = markup.inline_keyboard if markup else []
    action = CLOSE_MENU_NOTICE if tracked else CLOSE_NOTICE
    return InlineKeyboardMarkup(inline_keyboard=[*rows, [button("dismiss", callback_data=action)]])


@router.callback_query(F.data.in_(CLOSE_ACTIONS))
async def dismiss_notice(callback: CallbackQuery, bot: Bot) -> None:
    message = callback.message
    if (
        not isinstance(message, Message)
        or message.chat.type != "private"
        or message.chat.id != callback.from_user.id
        or message.from_user is None
        or message.from_user.id != bot.id
        or not message.reply_markup
        or not any(
            item.callback_data == callback.data
            for row in message.reply_markup.inline_keyboard for item in row
        )
    ):
        await callback.answer()
        return
    try:
        if callback.data == CLOSE_MENU_NOTICE:
            from app.bot.menu import menu_session

            async with menu_session(bot, message.chat.id, wait=3) as menu:
                await message.delete()
                if menu.message_id == message.message_id:
                    menu.message_id, menu.callbacks = None, []
                    await menu.save()
        else:
            await message.delete()
    except (TelegramAPIError, LockError):
        await callback.answer("Не удалось удалить уведомление.", show_alert=True)
    else:
        await callback.answer()
