"""Inline actions for release messages in an administrator's private chat."""
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.version import RELEASE_REPOSITORY

router = Router(name="release_notices")
CLOSE_RELEASE = "release:close"


def release_keyboard(tag: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Открыть релиз", url=f"https://github.com/{RELEASE_REPOSITORY}/releases/tag/{tag}"),
            InlineKeyboardButton(text="Сообщество", url="https://t.me/remnaum"),
        ],
        [InlineKeyboardButton(text="Закрыть", callback_data=CLOSE_RELEASE)],
    ])


@router.callback_query(F.data == CLOSE_RELEASE)
async def close_release_notice(callback: CallbackQuery, bot: Bot) -> None:
    message = callback.message
    if (
        not isinstance(message, Message)
        or message.chat.type != "private"
        or message.chat.id != callback.from_user.id
        or message.from_user is None
        or message.from_user.id != bot.id
        or not message.reply_markup
        or not any(
            button.callback_data == CLOSE_RELEASE
            for row in message.reply_markup.inline_keyboard for button in row
        )
    ):
        await callback.answer()
        return
    try:
        await message.delete()
    except TelegramAPIError:
        await callback.answer("Не удалось удалить уведомление.", show_alert=True)
    else:
        await callback.answer()
