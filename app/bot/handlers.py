from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from app.config import settings

router = Router(name="user")

@router.message(CommandStart())
async def start_handler(message: Message) -> None:
    keyboard = None
    if settings.webapp_url:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Открыть личный кабинет", web_app=WebAppInfo(url=settings.webapp_url))
        ]])
    await message.answer(
        "Добро пожаловать в <b>Remnaum</b>!\n\n"
        "Бот находится в разработке. Здесь появятся подписки, оплата и управление VPN.",
        reply_markup=keyboard,
    )
