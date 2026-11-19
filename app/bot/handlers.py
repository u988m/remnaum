import logging
from html import escape, unescape
import re
from urllib.parse import urlsplit

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, Message, PreCheckoutQuery, WebAppInfo, FSInputFile

from app.bot.menu import MenuMiddleware, menu_session, remember_menu, remove_command
from app.config import settings
from app.services.tariffs import format_traffic
from app.services.payment_service import (
    accept_current_terms,
    PaymentProviderUnavailable,
    create_stars_payment_for_user,
    create_payment_for_user,
    has_accepted_current_terms,
    retry_paid_payments_for_user,
    validate_stars_pre_checkout,
)
from app.services.payment_providers import VerifiedPayment
from app.services.payment_service import settle_payment
from app.services.subscriptions import get_user_subscription, provision_paid_payment
from app.services.users import get_or_create_user
from app.services.store_settings import PROVIDER_LABELS, is_payment_method_enabled, get_store_payment_settings, public_payment_settings
from app.services.store_profile import get_terms_url
from app.services.store_profile import get_store_profile
from app.services.store_media import welcome_image_path
from app.services.bot_identity import bot_username
from app.services.referrals import parse_referral_start, register_referral, referral_count, referral_code, reward_balance, get_referral_settings
from app.services.plans import PlanQuote, get_plan, list_plans, max_extra_devices, plan_revision, quote_plan
from app.services.trials import get_trial_settings, trial_eligible, claim_trial
from app.services.promos import redeem_promo, promo_balance
from app.db.models import Payment, User
from app.db.session import SessionLocal
from sqlalchemy import select

logger = logging.getLogger(__name__)
router = Router(name="user")
router.message.middleware(MenuMiddleware())
router.callback_query.middleware(MenuMiddleware())


def _admin_webapp_url() -> str:
    url = urlsplit(settings.webapp_url)
    if url.scheme == "https" and url.netloc:
        return f"https://{url.netloc}/admin"
    return f"{settings.public_base_url.rstrip('/')}/admin"


def main_menu(*, is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = []
    has_webapp = settings.webapp_url.startswith("https://")
    if has_webapp:
        rows.append([InlineKeyboardButton(text="✨ Mini App", web_app=WebAppInfo(url=settings.webapp_url))])
    rows.extend([
        [InlineKeyboardButton(text="👤 Профиль", callback_data="menu:profile"),
         InlineKeyboardButton(text="🔐 Подписка", callback_data="menu:subscription")],
        [InlineKeyboardButton(text="🛒 Тарифы", callback_data="menu:plans"),
         InlineKeyboardButton(text="🎁 Бонусы", callback_data="menu:bonuses")],
        [InlineKeyboardButton(text="🧾 Покупки", callback_data="menu:purchases"),
         InlineKeyboardButton(text="💬 Поддержка", callback_data="menu:support")],
    ])
    if is_admin and has_webapp:
        rows.append([InlineKeyboardButton(
            text="⚙️ Админка", web_app=WebAppInfo(url=_admin_webapp_url()),
        )])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def back_menu(destination: str = "menu:home") -> InlineKeyboardMarkup:
    label = "← Меню" if destination == "menu:home" else "← Назад"
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=label, callback_data=destination)]])


SUBSCRIPTION_STATUS = {
    "active": "Активна", "disabled": "Отключена", "expired": "Истекла", "deleted": "Удалена",
}


async def home_text(telegram_id: int, profile: dict) -> str:
    lines = [f"<b>{escape(profile['name'])}</b>", escape(profile["tagline"] or "Ваш VPN — ваши правила")]
    if description := profile.get("description"):
        lines.extend(("", escape(description)))
    if subscription := await get_user_subscription(telegram_id):
        status = SUBSCRIPTION_STATUS.get(subscription["status"], subscription["status"])
        lines.extend(("", f"Подписка · <b>{escape(status)}</b>"))
    return "\n".join(lines)


async def send_menu(
    message: Message, text: str, markup: InlineKeyboardMarkup | None, *, previous: Message | None = None,
) -> None:
    image = welcome_image_path()
    if image is not None:
        try:
            sent = await message.answer_photo(FSInputFile(image), caption=_menu_caption(text), reply_markup=markup)
        except Exception as exc:
            logger.warning("Could not send welcome photo: %s", type(exc).__name__)
        else:
            await remember_menu(sent, markup, previous=previous)
            return
    sent = await message.answer(text, reply_markup=markup)
    await remember_menu(sent, markup, previous=previous)


async def send_home_menu(message: Message, telegram_id: int, profile: dict, *, is_admin: bool) -> None:
    await send_menu(message, await home_text(telegram_id, profile), main_menu(is_admin=is_admin))


async def show_command_section(message: Message, text: str, markup: InlineKeyboardMarkup) -> None:
    await send_menu(message, text, markup)
    await remove_command(message)


def _menu_caption(text: str) -> str:
    plain = unescape(re.sub(r"<[^>]+>", "", text))
    encoded = plain.encode("utf-16-le")
    if len(encoded) <= 2 * 1024:
        return text
    suffix = "\n\n… Остальное смотрите в Mini App."
    shortened = encoded[:2 * (1024 - len(suffix))].decode("utf-16-le", errors="ignore")
    return escape(shortened.rstrip(), quote=False) + suffix


async def edit_menu_message(message: Message, text: str, *, reply_markup: InlineKeyboardMarkup | None = None) -> None:
    """Edit the active menu or replace it if Telegram changes the message type."""
    if getattr(message, "invoice", None) or (not getattr(message, "photo", None) and welcome_image_path() is not None):
        await send_menu(message, text, reply_markup, previous=message)
        return
    try:
        if getattr(message, "photo", None):
            await message.edit_caption(caption=_menu_caption(text), reply_markup=reply_markup)
        else:
            await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            await send_menu(message, text, reply_markup, previous=message)
            return
    await remember_menu(message, reply_markup)


@router.message(CommandStart())
async def start_handler(message: Message) -> None:
    if message.from_user is None:
        return
    inviter = parse_referral_start(message.text)
    is_new_user = await get_or_create_user(message.from_user)
    if is_new_user and inviter is not None:
        await register_referral(message.from_user.id, inviter)
    profile = await get_store_profile()
    await send_home_menu(
        message, message.from_user.id, profile,
        is_admin=message.from_user.id in settings.admin_id_set,
    )
    await remove_command(message)


@router.message(Command("admin"))
async def admin_command_handler(message: Message) -> None:
    if message.from_user is None or message.from_user.id not in settings.admin_id_set:
        await show_command_section(message, "Доступ к панели управления запрещён.", back_menu())
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚙️ Открыть админку", web_app=WebAppInfo(url=_admin_webapp_url()))],
        back_menu().inline_keyboard[0],
    ])
    await show_command_section(message, "<b>Панель управления</b>", keyboard)


@router.message(Command("help"))
async def help_handler(message: Message) -> None:
    text, keyboard = await support_menu()
    await show_command_section(message, text, keyboard)


@router.callback_query(F.data == "menu:home")
async def home_handler(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message:
        profile = await get_store_profile()
        text = await home_text(callback.from_user.id, profile)
        keyboard = main_menu(is_admin=callback.from_user.id in settings.admin_id_set)
        await edit_menu_message(callback.message, text, reply_markup=keyboard)


@router.callback_query(F.data == "menu:bonuses")
async def bonuses_handler(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message:
        await edit_menu_message(callback.message, 
            "🎁 <b>Бонусы и подарки</b>\n\n"
            "Приглашайте друзей, активируйте промокоды и получите пробный доступ.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🤝 Пригласить друга", callback_data="menu:referral")],
                [InlineKeyboardButton(text="🎟 Активировать промокод", callback_data="menu:promo")],
                [InlineKeyboardButton(text="✨ Пробный доступ", callback_data="menu:trial")],
                [InlineKeyboardButton(text="← Меню", callback_data="menu:home")],
            ]),
        )


@router.callback_query(F.data == "menu:plans")
async def plans_handler(callback: CallbackQuery) -> None:
    await callback.answer()
    plans = await list_plans()
    rows = []
    for plan in plans[:20]:
        title = " ".join(plan["title"].split())
        if len(title) > 20:
            title = title[:19].rstrip() + "…"
        price = f"{plan['price_rub']:g} ₽" if plan["price_rub_minor"] else f"{plan['price_stars']} ⭐"
        rows.append([InlineKeyboardButton(
            text=f"{title} · {price}",
            callback_data=f"plan:select:{plan['id']}",
        )])
    rows.append([InlineKeyboardButton(text="← Меню", callback_data="menu:home")])
    if callback.message:
        await edit_menu_message(callback.message, 
            "📦 <b>Выберите тариф</b>\n\n"
            "Срок, трафик, устройства и цену устанавливает владелец магазина.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )


@router.callback_query(F.data == "menu:builder")
async def builder_handler(callback: CallbackQuery) -> None:
    await callback.answer("Тарифы задаёт администратор. Выберите готовый вариант.", show_alert=True)


@router.callback_query(F.data.regexp(r"^plan:select:[0-9]+(?::[0-9]+)?$"))
async def fixed_plan_handler(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    plan_id = int(parts[2])
    extra_devices = int(parts[3]) if len(parts) == 4 else 0
    plan = await get_plan(plan_id)
    try:
        quote = quote_plan(plan, extra_devices)
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer()
    controls = []
    if extra_devices:
        controls.append(InlineKeyboardButton(text="➖ 1", callback_data=f"plan:select:{plan_id}:{extra_devices - 1}"))
    if extra_devices < max_extra_devices(plan):
        controls.append(InlineKeyboardButton(text="➕ 1", callback_data=f"plan:select:{plan_id}:{extra_devices + 1}"))
    price = f"{quote.amount_minor / 100:.2f} ₽" if quote.amount_minor else ""
    if quote.stars is not None:
        price = " / ".join(filter(None, (price, f"{quote.stars} ⭐")))
    key = f"{plan_id}:{extra_devices}:{plan_revision(plan)}"
    rows = [controls] if controls else []
    rows.extend([
        [InlineKeyboardButton(text="💳 Выбрать оплату", callback_data=f"plan:confirm:{key}")],
        [InlineKeyboardButton(text="← К тарифам", callback_data="menu:plans")],
    ])
    if callback.message:
        details = [_plan_summary(quote)]
        if max_extra_devices(plan):
            details.append(f"Устройств добавлено: {extra_devices}")
            addon_prices = []
            if quote.unit_minor is not None:
                addon_prices.append(f"{quote.unit_minor / 100:.2f} ₽")
            if plan.extra_device_price_stars is not None:
                addon_prices.append(f"{plan.extra_device_price_stars} ⭐")
            details.append(f"За доп. устройство: {' / '.join(addon_prices)}")
        details.append(f"\nИтого: <b>{price}</b>")
        await edit_menu_message(callback.message, "\n".join(details), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


def _plan_summary(quote: PlanQuote) -> str:
    return (f"📦 <b>{escape(quote.name)}</b>\n{quote.duration_days} дн. · {format_traffic(quote.traffic_gb)}"
            f"\nУстройств: {quote.device_limit}")


@router.callback_query(F.data.regexp(
    rf"^plan:(?:confirm|accept|pay:(?:stars|{'|'.join(PROVIDER_LABELS)})):[0-9]+:[0-9]+:[0-9a-f]{{20}}$"
))
async def fixed_plan_checkout_handler(callback: CallbackQuery) -> None:
    action, plan_id, extras, revision = callback.data[5:].rsplit(":", 3)
    order = {"plan_id": int(plan_id), "extra_devices": int(extras), "revision": revision}
    try:
        quote = quote_plan(await get_plan(order["plan_id"]), order["extra_devices"], revision)
        terms = await get_terms_url()
        if terms and action == "accept":
            await accept_current_terms(callback.from_user.id)
        accepted = not terms or await has_accepted_current_terms(callback.from_user.id)
    except (ValueError, RuntimeError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer()
    if not callback.message:
        return
    key = f"{plan_id}:{extras}:{revision}"
    rows = []
    if not accepted:
        rows.extend([
            [InlineKeyboardButton(text="📄 Условия продажи", url=terms)],
            [InlineKeyboardButton(text="✅ Принимаю", callback_data=f"plan:accept:{key}")],
        ])
        text = "Перед оплатой примите условия продажи."
    elif action.startswith("pay:"):
        provider = action.split(":", 1)[1]
        if provider == "stars":
            await send_stars_invoice(callback.message, callback.from_user.id, **order)
        else:
            await send_external_invoice(callback.message, callback.from_user.id, provider, **order)
        return
    else:
        methods, credentials = await get_store_payment_settings()
        configured = public_payment_settings(methods, credentials)["configured"]
        if quote.stars is not None and methods.get("telegram_stars") and configured.get("telegram_stars"):
            rows.append([InlineKeyboardButton(text=f"⭐ Stars · {quote.stars}", callback_data=f"plan:pay:stars:{key}")])
        if quote.amount_minor > 0:
            for provider, title in PROVIDER_LABELS.items():
                if methods.get(provider) and configured.get(provider):
                    rows.append([InlineKeyboardButton(
                        text=f"💳 {title} · {quote.amount_minor / 100:.2f} ₽", callback_data=f"plan:pay:{provider}:{key}",
                    )])
        text = "Выберите способ оплаты." if rows else "Для этого заказа нет доступных способов оплаты."
    rows.append([InlineKeyboardButton(text="← К заказу", callback_data=f"plan:select:{plan_id}:{extras}")])
    await edit_menu_message(callback.message, f"{_plan_summary(quote)}\n\n{text}",
                            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("tariff:"))
async def tariff_update_handler(callback: CallbackQuery) -> None:
    await callback.answer("Конструктор отключён. Тарифы задаёт администратор.", show_alert=True)


@router.callback_query(F.data == "payment:choose")
@router.callback_query(F.data == "payment:terms:accept")
@router.callback_query(F.data.startswith("payment:create:"))
async def legacy_checkout_disabled(callback: CallbackQuery) -> None:
    """Reject old flexible-checkout keyboards; only admin plans can be ordered."""
    await callback.answer("Выберите тариф из списка магазина.", show_alert=True)


def payment_back_button(order: dict) -> InlineKeyboardButton:
    if order.get("revision") is None:
        return back_menu().inline_keyboard[0][0]
    return InlineKeyboardButton(
        text="← Назад", callback_data=f"plan:confirm:{order['plan_id']}:{order['extra_devices']}:{order['revision']}",
    )


async def send_external_invoice(message: Message, telegram_id: int, name: str, **order) -> None:
    back = payment_back_button(order)
    reply_markup = InlineKeyboardMarkup(inline_keyboard=[[back]])
    try:
        payment, link = await create_payment_for_user(telegram_id, name, **order)
    except (ValueError, PaymentProviderUnavailable) as exc:
        await edit_menu_message(message, escape(str(exc)), reply_markup=reply_markup)
        return
    except Exception:
        logger.warning("Could not initiate %s payment for Telegram account %s", name, telegram_id)
        await edit_menu_message(message, "Не удалось создать счёт. Попробуйте ещё раз.", reply_markup=reply_markup)
        return
    await edit_menu_message(
        message, f"<b>Заказ №{payment.id}</b>\n{PROVIDER_LABELS[name]} · {payment.amount / 100:.2f} ₽",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Оплатить", url=link.url)], [back],
        ]),
    )


async def send_stars_invoice(message: Message, telegram_id: int, **order) -> None:
    back = payment_back_button(order)
    reply_markup = InlineKeyboardMarkup(inline_keyboard=[[back]])
    try:
        if not await is_payment_method_enabled("telegram_stars"):
            raise ValueError("Оплата в Telegram Stars отключена")
        payment, stars = await create_stars_payment_for_user(telegram_id, **order)
    except (ValueError, RuntimeError) as exc:
        await edit_menu_message(message, escape(str(exc)), reply_markup=reply_markup)
        return
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Оплатить ⭐", pay=True)], [back],
    ])
    try:
        profile = await get_store_profile()
        sent = await message.answer_invoice(
            title=profile["name"][:28] + " VPN",
            description="VPN-подписка по тарифу магазина.",
            payload=f"remnaum:{payment.id}",
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice(label="VPN-подписка", amount=stars)],
            reply_markup=markup,
        )
    except Exception:
        logger.exception("Telegram Stars invoice delivery failed for payment %s", payment.id)
        await edit_menu_message(
            message, "Не удалось открыть счёт Telegram Stars. Попробуйте ещё раз.", reply_markup=reply_markup,
        )
    else:
        await remember_menu(sent, markup, previous=message)


@router.pre_checkout_query()
async def stars_pre_checkout_handler(query: PreCheckoutQuery) -> None:
    try:
        payment_id = int(query.invoice_payload.removeprefix("remnaum:"))
        if not await is_payment_method_enabled("telegram_stars"):
            raise ValueError("Stars checkout disabled")
        valid = await validate_stars_pre_checkout(
            telegram_id=query.from_user.id,
            payment_id=payment_id,
            amount=query.total_amount,
            currency=query.currency,
            payload=query.invoice_payload,
        )
    except Exception:
        logger.exception("Telegram Stars pre-checkout validation failed")
        valid = False
    await query.answer(
        ok=valid,
        error_message=None if valid else "Счёт недействителен или уже оплачен. Создайте новый.",
    )


async def show_payment_result(bot, chat_id: int, text: str) -> None:
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔐 Подписка", callback_data="menu:subscription")],
        back_menu().inline_keyboard[0],
    ])
    async with menu_session(bot, chat_id, wait=3) as menu:
        sent = await bot.send_message(chat_id, text, reply_markup=markup)
        await menu.remember(sent.message_id, markup)


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
        text = "Платёж принят. Подключение VPN задерживается. Проверьте раздел «Подписка»."
    else:
        text = f"<b>Заказ №{payment_id} оплачен</b>\nПодписка готова к подключению."
    try:
        await show_payment_result(message.bot, message.chat.id, text)
    except Exception:
        logger.info("Could not show Stars payment status in chat %s", message.chat.id)


@router.message(Command("terms"))
async def terms_handler(message: Message) -> None:
    terms_url = await get_terms_url()
    text = (f'<a href="{escape(terms_url, quote=True)}">Условия продажи</a>' if terms_url
            else "Условия продажи ещё не опубликованы.")
    await show_command_section(message, text, back_menu())


@router.message(Command("paysupport"))
async def payment_support_handler(message: Message) -> None:
    profile = await get_store_profile()
    support = escape(profile["support_contact"]) if profile["support_contact"] else "Контакт поддержки ещё не настроен."
    await show_command_section(message, f"<b>Поддержка по оплате</b>\n{support}", back_menu())


@router.message(Command("promo"))
async def promo_command_handler(message: Message) -> None:
    if message.from_user is None:
        return
    chunks = (message.text or "").split(maxsplit=1)
    if len(chunks) != 2:
        await show_command_section(message, "Введите промокод: <code>/promo ВАШ_КОД</code>", back_menu())
        return
    try:
        result = await redeem_promo(message.from_user.id, chunks[1])
    except ValueError as exc:
        await show_command_section(message, escape(str(exc)), back_menu())
        return
    await show_command_section(
        message,
        f"Промокод <b>{escape(result['code'])}</b> активирован. "
        f"+{result['bonus_days']} дней при следующей оплате.",
        back_menu(),
    )


@router.callback_query(F.data == "menu:promo")
async def promo_menu_handler(callback: CallbackQuery) -> None:
    await callback.answer()
    balance = await promo_balance(callback.from_user.id)
    if callback.message:
        await edit_menu_message(callback.message, 
            "🎟 <b>Промокоды</b>\n\n"
            "Введите в этом чате: <code>/promo ВАШ_КОД</code>\n\n"
            f"Бонусных дней: <b>{balance['days']}</b>. "
            "Они добавятся к следующей оплаченной подписке.",
            reply_markup=back_menu("menu:bonuses"),
        )


@router.callback_query(F.data == "menu:trial")
async def trial_menu_handler(callback: CallbackQuery) -> None:
    await callback.answer()
    if not callback.message:
        return
    config = await get_trial_settings()
    if not config["enabled"]:
        await edit_menu_message(callback.message, "🎁 Пробный доступ пока отключён.", reply_markup=back_menu("menu:bonuses"))
        return
    if not await trial_eligible(callback.from_user.id):
        await edit_menu_message(callback.message, 
            "🎁 Пробный доступ доступен только один раз до первой подписки.",
            reply_markup=back_menu("menu:bonuses"),
        )
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎁 Получить пробный доступ", callback_data="trial:claim")],
        back_menu("menu:bonuses").inline_keyboard[0],
    ])
    await edit_menu_message(callback.message, 
        "🎁 <b>Пробный доступ</b>\n\n"
        f"{config['days']} дней · {config['traffic_gb']} ГБ · "
        f"{config['device_limit']} устройств.\n"
        "Выдаётся один раз.",
        reply_markup=keyboard,
    )


@router.callback_query(F.data == "trial:claim")
async def trial_claim_handler(callback: CallbackQuery) -> None:
    await callback.answer()
    if not callback.message:
        return
    try:
        result = await claim_trial(callback.from_user.id)
    except (ValueError, RuntimeError) as exc:
        await edit_menu_message(callback.message, "🎁 " + escape(str(exc)), reply_markup=back_menu("menu:bonuses"))
        return
    except Exception:
        await edit_menu_message(callback.message, 
            "Пробный доступ временно недоступен. Попробуйте позже.", reply_markup=back_menu("menu:bonuses"),
        )
        return
    link = result.get("subscription_url")
    keyboard = back_menu("menu:bonuses")
    if isinstance(link, str) and link.startswith("https://"):
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔗 Подключить VPN", url=link)],
            back_menu("menu:bonuses").inline_keyboard[0],
        ])
    await edit_menu_message(callback.message, 
        f"✅ Доступ выдан: {result['days']} дней, {result['traffic_gb']} ГБ, "
        f"{result['device_limit']} устройств.",
        reply_markup=keyboard,
    )


@router.callback_query(F.data == "menu:profile")
async def profile_handler(callback: CallbackQuery) -> None:
    await callback.answer()
    user = callback.from_user
    username = f"@{escape(user.username)}" if user.username else "не указан"
    if callback.message:
        await edit_menu_message(callback.message, 
            "👤 <b>Мой профиль</b>\n\n"
            f"Telegram ID: <code>{user.id}</code>\n"
            f"Имя пользователя: {username}",
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
    rows = [
        [InlineKeyboardButton(text="📱 Устройства", callback_data="menu:devices")],
        [InlineKeyboardButton(text="📖 Инструкция", callback_data="menu:connect")],
        back_menu().inline_keyboard[0],
    ]
    if subscription is None:
        await edit_menu_message(callback.message, 
            "🔐 <b>Моя подписка</b>\n\nАктивная VPN-подписка пока не найдена.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )
        return

    expires_at = subscription["expires_at"]
    expires_text = expires_at.strftime("%d.%m.%Y %H:%M UTC") if expires_at else "не указано"
    details = (
        "🔐 <b>Моя подписка</b>\n\n"
        f"Статус: <b>{escape(SUBSCRIPTION_STATUS.get(subscription['status'], subscription['status']))}</b>\n"
        f"Действует до: <b>{expires_text}</b>\n"
        f"Трафик: <b>{format_traffic(subscription['traffic_gb'])}</b>\n"
        f"Устройства: <b>{subscription['device_limit'] or '—'}</b>"
    )
    url = subscription.get("subscription_url")
    if subscription.get("plan_id") is not None:
        plan = await get_plan(subscription["plan_id"])
        if plan and plan.is_active:
            extras = max(0, (subscription.get("device_limit") or plan.device_limit) - plan.device_limit)
            extras = min(extras, max_extra_devices(plan))
            rows.insert(0, [InlineKeyboardButton(
                text="🔄 Продлить", callback_data=f"plan:select:{plan.id}:{extras}",
            )])
    if isinstance(url, str) and url.startswith("https://"):
        rows.insert(0, [InlineKeyboardButton(text="🔗 Подключить VPN", url=url)])
    await edit_menu_message(callback.message, details, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data == "menu:connect")
async def connection_help_handler(callback: CallbackQuery) -> None:
    await callback.answer()
    if not callback.message:
        return
    await edit_menu_message(callback.message, 
        "📖 <b>Как подключить VPN</b>\n\n"
        "1. Установите приложение-клиент, совместимое с подпиской вашей панели Remnawave.\n"
        "2. Откройте ссылку подключения из раздела «Подписка».\n"
        "3. Импортируйте ссылку в клиент и обновите список серверов.\n"
        "4. Выберите сервер и включите соединение.\n\n"
        "Если профиль не открывается, напишите в поддержку магазина.",
        reply_markup=back_menu("menu:subscription"),
    )


async def support_menu() -> tuple[str, InlineKeyboardMarkup]:
    profile = await get_store_profile()
    contact = str(profile.get("support_contact") or "").strip()
    support = escape(contact) if contact else "Связь с поддержкой пока не настроена."
    button_url = None
    if contact.startswith("@") and len(contact) > 1 and contact[1:].replace("_", "").isalnum():
        button_url = "https://t.me/" + contact[1:]
    elif contact.startswith("https://"):
        try:
            parsed = urlsplit(contact)
            if parsed.hostname and not parsed.username and not parsed.password:
                button_url = contact
        except ValueError:
            pass
    rows = []
    if button_url:
        rows.append([InlineKeyboardButton(text="💬 Написать в поддержку", url=button_url)])
    rows.append([InlineKeyboardButton(text="← Меню", callback_data="menu:home")])
    text = "💬 <b>Поддержка</b>"
    if not button_url:
        text += f"\n\n{support}"
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "menu:support")
async def support_handler(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message:
        text, keyboard = await support_menu()
        await edit_menu_message(callback.message, text, reply_markup=keyboard)


@router.callback_query(F.data == "menu:devices")
async def devices_handler(callback: CallbackQuery) -> None:
    from app.services.remnawave import RemnawaveClient
    await callback.answer()
    subscription = await get_user_subscription(callback.from_user.id)
    if not subscription:
        message = "📱 <b>Мои устройства</b>\\n\\nСначала оформите подписку."
    else:
        limit = subscription.get("device_limit")
        lines = ["📱 <b>Мои устройства</b>", "", f"Ваш лимит: <b>{limit if limit is not None else '—'}</b>"]
        panel_id = subscription.get("panel_user_id")
        if panel_id and settings.remnawave_base_url and settings.remnawave_api_token:
            try:
                result = await RemnawaveClient().get_hwid_devices(panel_id)
                data = result.get("response", result) if isinstance(result, dict) else result
                if isinstance(data, dict):
                    devices = data.get("devices", data.get("items", []))
                elif isinstance(data, list):
                    devices = data
                else:
                    devices = []
                lines.append(f"Подключено устройств: <b>{len(devices)}</b>")
                for index, device in enumerate(devices[:10], 1):
                    if isinstance(device, dict):
                        label = device.get("deviceModel") or device.get("platform") or device.get("userAgent") or "Устройство"
                    else:
                        label = "Устройство"
                    lines.append(f"{index}. {escape(str(label)[:70])}")
            except Exception:
                lines.append("Список устройств временно недоступен.")
        else:
            lines.append("Подключите VPN, чтобы увидеть зарегистрированные устройства.")
        message = chr(10).join(lines)
    if callback.message:
        await edit_menu_message(callback.message, message.replace(chr(92) + "n", chr(10)), reply_markup=back_menu("menu:subscription"))


@router.callback_query(F.data == "menu:purchases")
async def purchases_handler(callback: CallbackQuery) -> None:
    await callback.answer()
    async with SessionLocal() as session:
        rows = (await session.scalars(
            select(Payment).join(User, Payment.user_id == User.id)
            .where(User.telegram_id == callback.from_user.id)
            .order_by(Payment.created_at.desc(), Payment.id.desc()).limit(10)
        )).all()
    lines = ["🧾 <b>Мои покупки</b>", ""]
    if not rows:
        lines.append("Покупок пока нет.")
    for p in rows:
        amount = f"{p.amount} ⭐" if p.currency == "XTR" else f"{p.amount / 100:.2f} {escape(p.currency)}"
        provider = "Telegram Stars" if p.provider == "telegram_stars" else PROVIDER_LABELS.get(p.provider, p.provider)
        status = {"pending": "Ждёт оплаты", "paid": "Оплачен", "succeeded": "Подписка выдана",
                  "failed": "Ошибка", "cancelled": "Отменён"}.get(p.status, p.status)
        lines.append(f"№{p.id} · {escape(provider)} · {amount} · {escape(status)}")
    if callback.message:
        await edit_menu_message(callback.message, chr(10).join(lines), reply_markup=back_menu())


@router.callback_query(F.data == "menu:referral")
async def referral_handler(callback: CallbackQuery) -> None:
    await callback.answer()
    username = bot_username()
    if not username:
        text = "🎁 Реферальная ссылка пока недоступна."
    else:
        link = f"https://t.me/{username}?start={referral_code(callback.from_user.id)}"
        count = await referral_count(callback.from_user.id)
        balance = await reward_balance(callback.from_user.id)
        config = await get_referral_settings()
        text = (
            "🎁 <b>Пригласить друга</b>\\n\\n"
            f"Ваша ссылка: {escape(link)}\\n"
            f"Приглашено пользователей: <b>{count}</b>\\n\\n"
            f"Начислено бонусов: <b>{balance['earned']}</b>\\n"
            f"Доступно бонусных дней: <b>{balance['days']}</b>\\n"
            + (f"За первую оплаченную покупку друга: {config['reward_days']} бонусных дней. "
               "Бонус применяется к вашему следующему продлению."
               if config["enabled"] else "Реферальная программа пока отключена владельцем.")
        )
    if callback.message:
        await edit_menu_message(callback.message, text.replace(chr(92) + "n", chr(10)), reply_markup=back_menu("menu:bonuses"))
