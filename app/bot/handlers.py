from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo

from app.config import settings
from app.services.users import get_or_create_user

router = Router(name="user")


def main_menu() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="📦 Тарифы", callback_data="menu:plans"),
         InlineKeyboardButton(text="👤 Мой профиль", callback_data="menu:profile")],
        [InlineKeyboardButton(text="🔐 Моя подписка", callback_data="menu:subscription"),
         InlineKeyboardButton(text="🆘 Поддержка", callback_data="menu:support")],
    ]
    if settings.webapp_url:
        rows.append([InlineKeyboardButton(text="🌐 Личный кабинет", web_app=WebAppInfo(url=settings.webapp_url))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="⬅️ Главное меню", callback_data="menu:home")
    ]])


@router.message(CommandStart())
async def start_handler(message: Message) -> None:
    if message.from_user is None:
        return
    await get_or_create_user(message.from_user)
    await message.answer(
        "Добро пожаловать в <b>Remnaum</b>! 🚀\n\n"
        "Здесь вы сможете управлять VPN-подпиской, следить за сроком действия "
        "и получать помощь. Выберите нужный раздел:",
        reply_markup=main_menu(),
    )


@router.message(Command("help"))
async def help_handler(message: Message) -> None:
    await message.answer(
        "Используйте кнопки меню для управления аккаунтом. "
        "Если нужна помощь, откройте раздел «Поддержка».",
        reply_markup=main_menu(),
    )


@router.callback_query(F.data == "menu:home")
async def home_handler(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message:
        await callback.message.edit_text("Главное меню Remnaum. Выберите раздел:", reply_markup=main_menu())


@router.callback_query(F.data == "menu:plans")
async def plans_handler(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            "📦 <b>Тарифы</b>\n\nКаталог тарифов пока настраивается. "
            "Когда подключим тарифную систему, здесь появятся цены, сроки и условия.",
            reply_markup=back_menu(),
        )


@router.callback_query(F.data == "menu:profile")
async def profile_handler(callback: CallbackQuery) -> None:
    await callback.answer()
    user = callback.from_user
    username = f"@{user.username}" if user.username else "не указан"
    if callback.message:
        await callback.message.edit_text(
            "👤 <b>Мой профиль</b>\n\n"
            f"Telegram ID: <code>{user.id}</code>\n"
            f"Username: {username}\nСтатус аккаунта: активен",
            reply_markup=back_menu(),
        )


@router.callback_query(F.data == "menu:subscription")
async def subscription_handler(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            "🔐 <b>Моя подписка</b>\n\n"
            "У вас пока нет активной VPN-подписки. "
            "Управление подписками подключим на следующем этапе.",
            reply_markup=back_menu(),
        )


@router.callback_query(F.data == "menu:support")
async def support_handler(callback: CallbackQuery) -> None:
    await callback.answer()
    support = escape(settings.support_contact) if settings.support_contact else "Контакт поддержки ещё не настроен."
    if callback.message:
        await callback.message.edit_text(f"🆘 <b>Поддержка</b>\n\n{support}", reply_markup=back_menu())
