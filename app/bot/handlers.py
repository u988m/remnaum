from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo

from app.config import settings
from app.services.tariffs import TariffQuote, format_price, get_tariff_draft, update_tariff_draft
from app.services.payment_service import create_payment_for_user
from app.services.subscriptions import get_user_subscription
from app.services.users import get_or_create_user

router = Router(name="user")


def main_menu() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="📦 Собрать тариф", callback_data="menu:plans"),
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


def tariff_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="− 7 дней", callback_data="tariff:days:-7"),
            InlineKeyboardButton(text="+ 7 дней", callback_data="tariff:days:7"),
        ],
        [
            InlineKeyboardButton(text="− 10 ГБ", callback_data="tariff:traffic:-10"),
            InlineKeyboardButton(text="+ 10 ГБ", callback_data="tariff:traffic:10"),
        ],
        [
            InlineKeyboardButton(text="− 1 устройство", callback_data="tariff:devices:-1"),
            InlineKeyboardButton(text="+ 1 устройство", callback_data="tariff:devices:1"),
        ],
        [InlineKeyboardButton(text="💳 Перейти к оплате", callback_data="payment:choose")],
        [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="menu:home")],
    ])


def tariff_text(quote: TariffQuote) -> str:
    return (
        "📦 <b>Конструктор тарифа</b>\n\n"
        f"Срок: <b>{quote.duration_days} дней</b>\n"
        f"Трафик: <b>{quote.traffic_gb} ГБ</b>\n"
        f"Устройства: <b>{quote.device_limit}</b>\n\n"
        f"Итоговая стоимость: <b>{escape(format_price(quote.amount_minor, quote.currency))}</b>\n\n"
        "Настройте параметры кнопками ниже. Значения ограничены доступным диапазоном. "
        "После выбора тарифа можно перейти к оплате."
    )


async def show_tariff(callback: CallbackQuery, quote: TariffQuote | None = None) -> None:
    if callback.message:
        selected = quote or await get_tariff_draft(callback.from_user.id)
        await callback.message.edit_text(tariff_text(selected), reply_markup=tariff_keyboard())


@router.message(CommandStart())
async def start_handler(message: Message) -> None:
    if message.from_user is None:
        return
    await get_or_create_user(message.from_user)
    await message.answer(
        "Добро пожаловать в <b>Remnaum</b>! 🚀\n\n"
        "Соберите VPN-тариф под себя: выберите срок, трафик и количество устройств.",
        reply_markup=main_menu(),
    )


@router.message(Command("help"))
async def help_handler(message: Message) -> None:
    await message.answer(
        "Используйте кнопки меню для настройки тарифа и управления аккаунтом. "
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
    await show_tariff(callback)


@router.callback_query(F.data.startswith("tariff:"))
async def tariff_update_handler(callback: CallbackQuery) -> None:
    try:
        _, field, raw_delta = callback.data.split(":", 2)
        quote = await update_tariff_draft(
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
            field=field,
            delta=int(raw_delta),
        )
    except (ValueError, TypeError):
        await callback.answer("Не удалось изменить параметр.", show_alert=True)
        return
    await callback.answer("Параметр обновлён")
    await show_tariff(callback, quote)


@router.callback_query(F.data == "payment:choose")
async def payment_choose_handler(callback: CallbackQuery) -> None:
    await callback.answer()
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="ЮKassa", callback_data="payment:create:yookassa")],
        [InlineKeyboardButton(text="ЮMoney", callback_data="payment:create:yoomoney")],
        [InlineKeyboardButton(text="Crypto Pay", callback_data="payment:create:cryptopay")],
        [InlineKeyboardButton(text="⬅️ Назад к тарифу", callback_data="menu:plans")],
    ])
    if callback.message:
        quote = await get_tariff_draft(callback.from_user.id)
        await callback.message.edit_text(
            f"💳 <b>Выберите способ оплаты</b>\\n\\n"
            f"К оплате: <b>{escape(format_price(quote.amount_minor, quote.currency))}</b>",
            reply_markup=keyboard,
        )


@router.callback_query(F.data.startswith("payment:create:"))
async def payment_create_handler(callback: CallbackQuery) -> None:
    provider_name = callback.data.rsplit(":", 1)[-1]
    await callback.answer("Создаю счёт…")
    try:
        _, link = await create_payment_for_user(callback.from_user.id, provider_name)
    except (ValueError, RuntimeError) as exc:
        if callback.message:
            await callback.message.answer(f"Не удалось создать счёт: {escape(str(exc))}")
        return
    except Exception:
        if callback.message:
            await callback.message.answer("Платёжный сервис временно недоступен. Попробуйте позже.")
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Перейти к оплате", url=link.url)],
        [InlineKeyboardButton(text="⬅️ К способам оплаты", callback_data="payment:choose")],
    ])
    if callback.message:
        await callback.message.answer(
            f"Счёт создан. После оплаты дождитесь подтверждения платёжной системы.",
            reply_markup=keyboard,
        )


@router.callback_query(F.data == "menu:profile")
async def profile_handler(callback: CallbackQuery) -> None:
    await callback.answer()
    user = callback.from_user
    username = f"@{escape(user.username)}" if user.username else "не указан"
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
    if not callback.message:
        return
    subscription = await get_user_subscription(callback.from_user.id)
    if subscription is None:
        await callback.message.edit_text(
            "🔐 <b>Моя подписка</b>\\n\\nАктивная VPN-подписка пока не найдена.",
            reply_markup=back_menu(),
        )
        return

    expires_at = subscription["expires_at"]
    expires_text = expires_at.strftime("%d.%m.%Y %H:%M UTC") if expires_at else "не указано"
    details = (
        "🔐 <b>Моя подписка</b>\\n\\n"
        f"Статус: <b>{escape(subscription['status'])}</b>\\n"
        f"Действует до: <b>{expires_text}</b>\\n"
        f"Трафик: <b>{subscription['traffic_gb'] or '—'} ГБ</b>\\n"
        f"Устройства: <b>{subscription['device_limit'] or '—'}</b>"
    )
    url = subscription.get("subscription_url")
    keyboard = back_menu()
    if isinstance(url, str) and url.startswith("https://"):
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔗 Получить ссылку подключения", url=url)],
            [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="menu:home")],
        ])
    await callback.message.edit_text(details, reply_markup=keyboard)


@router.callback_query(F.data == "menu:support")
async def support_handler(callback: CallbackQuery) -> None:
    await callback.answer()
    support = escape(settings.support_contact) if settings.support_contact else "Контакт поддержки ещё не настроен."
    if callback.message:
        await callback.message.edit_text(f"🆘 <b>Поддержка</b>\n\n{support}", reply_markup=back_menu())
