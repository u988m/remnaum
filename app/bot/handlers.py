from html import escape

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, Message, PreCheckoutQuery, WebAppInfo

from app.config import settings
from app.services.tariffs import TariffQuote, get_tariff_draft, update_tariff_draft
from app.services.payment_service import (
    accept_current_terms,
    create_stars_payment_for_user,
    has_accepted_current_terms,
    retry_paid_payments_for_user,
    validate_stars_pre_checkout,
)
from app.services.payment_providers import VerifiedPayment
from app.services.payment_service import settle_payment
from app.services.tariffs import calculate_stars_price
from app.services.subscriptions import get_user_subscription, provision_paid_payment
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
    stars = calculate_stars_price(quote.duration_days, quote.traffic_gb, quote.device_limit)
    return (
        "📦 <b>Конструктор тарифа</b>\n\n"
        f"Срок: <b>{quote.duration_days} дней</b>\n"
        f"Трафик: <b>{quote.traffic_gb} ГБ</b>\n"
        f"Устройства: <b>{quote.device_limit}</b>\n\n"
        f"Стоимость: <b>{stars} ⭐</b>\n\n"
        "Настройте параметры кнопками ниже. Значения ограничены доступным диапазоном."
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
    if not settings.telegram_terms_url:
        if callback.message:
            await callback.message.edit_text(
                "Продажи пока не включены: администратору нужно опубликовать условия и указать TELEGRAM_TERMS_URL.",
                reply_markup=back_menu(),
            )
        return
    if await has_accepted_current_terms(callback.from_user.id):
        await show_stars_checkout(callback)
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📄 Прочитать условия", url=settings.telegram_terms_url)],
        [InlineKeyboardButton(text="Принимаю условия", callback_data="payment:terms:accept")],
        [InlineKeyboardButton(text="⬅️ Назад к тарифу", callback_data="menu:plans")],
    ])
    if callback.message:
        await callback.message.edit_text(
            "Перед оплатой прочитайте и примите условия оказания услуги.",
            reply_markup=keyboard,
        )


async def show_stars_checkout(callback: CallbackQuery) -> None:
    quote = await get_tariff_draft(callback.from_user.id)
    stars = calculate_stars_price(quote.duration_days, quote.traffic_gb, quote.device_limit)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"⭐ Оплатить {stars} Stars", callback_data="payment:create:stars")],
        [InlineKeyboardButton(text="⬅️ Назад к тарифу", callback_data="menu:plans")],
    ])
    if callback.message:
        await callback.message.edit_text(
            f"💳 <b>Оплата Telegram Stars</b>\n\n"
            f"Тариф: {quote.duration_days} дней, {quote.traffic_gb} ГБ, {quote.device_limit} устройств.\n"
            f"К оплате: <b>{stars} ⭐</b>",
            reply_markup=keyboard,
        )


@router.callback_query(F.data == "payment:terms:accept")
async def payment_terms_accept_handler(callback: CallbackQuery) -> None:
    try:
        await accept_current_terms(callback.from_user.id)
    except (ValueError, RuntimeError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer("Условия приняты")
    await show_stars_checkout(callback)


@router.callback_query(F.data == "payment:create:stars")
async def stars_payment_create_handler(callback: CallbackQuery) -> None:
    await callback.answer()
    try:
        payment, stars = await create_stars_payment_for_user(callback.from_user.id)
    except (ValueError, RuntimeError) as exc:
        if callback.message:
            await callback.message.answer(escape(str(exc)))
        return
    if callback.message:
        await callback.message.answer_invoice(
            title="Remnaum VPN",
            description="VPN-подписка с выбранными вами параметрами.",
            payload=f"remnaum:{payment.id}",
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice(label="Remnaum VPN subscription", amount=stars)],
        )


@router.pre_checkout_query()
async def stars_pre_checkout_handler(query: PreCheckoutQuery) -> None:
    try:
        payment_id = int(query.invoice_payload.removeprefix("remnaum:"))
        valid = await validate_stars_pre_checkout(
            telegram_id=query.from_user.id,
            payment_id=payment_id,
            amount=query.total_amount,
            currency=query.currency,
            payload=query.invoice_payload,
        )
    except (ValueError, TypeError):
        valid = False
    await query.answer(
        ok=valid,
        error_message=None if valid else "Счёт недействителен или уже оплачен. Создайте новый.",
    )


@router.message(F.successful_payment)
async def stars_successful_payment_handler(message: Message) -> None:
    successful = message.successful_payment
    if successful is None:
        return
    try:
        payment_id = int(successful.invoice_payload.removeprefix("remnaum:"))
        verified = VerifiedPayment(
            local_payment_id=payment_id,
            provider_payment_id=successful.telegram_payment_charge_id,
            amount_minor=successful.total_amount,
            currency=successful.currency,
        )
        await settle_payment(verified, "telegram_stars")
        await provision_paid_payment(payment_id)
    except Exception:
        await message.answer(
            "⭐ Платёж подтверждён Telegram. Выдача VPN-доступа задерживается; "
            "откройте «Моя подписка» через несколько секунд или обратитесь в поддержку."
        )
        return
    await message.answer(
        "✅ Оплата подтверждена! VPN-подписка активирована. "
        "Откройте раздел «Моя подписка», чтобы получить ссылку подключения.",
        reply_markup=main_menu(),
    )


@router.message(Command("terms"))
async def terms_handler(message: Message) -> None:
    if settings.telegram_terms_url:
        await message.answer(
            f"Условия Remnaum: <a href=\"{escape(settings.telegram_terms_url, quote=True)}\">прочитать</a>"
        )
    else:
        await message.answer("Условия ещё не опубликованы.")


@router.message(Command("paysupport"))
async def payment_support_handler(message: Message) -> None:
    support = escape(settings.support_contact) if settings.support_contact else "Контакт поддержки ещё не настроен."
    await message.answer(f"Поддержка по оплате: {support}")


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
    try:
        await retry_paid_payments_for_user(callback.from_user.id)
    except Exception:
        pass
    subscription = await get_user_subscription(callback.from_user.id)
    if subscription is None:
        await callback.message.edit_text(
            "🔐 <b>Моя подписка</b>\n\nАктивная VPN-подписка пока не найдена.",
            reply_markup=back_menu(),
        )
        return

    expires_at = subscription["expires_at"]
    expires_text = expires_at.strftime("%d.%m.%Y %H:%M UTC") if expires_at else "не указано"
    details = (
        "🔐 <b>Моя подписка</b>\n\n"
        f"Статус: <b>{escape(subscription['status'])}</b>\n"
        f"Действует до: <b>{expires_text}</b>\n"
        f"Трафик: <b>{subscription['traffic_gb'] or '—'} ГБ</b>\n"
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
