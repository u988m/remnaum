import logging
from html import escape, unescape
import re
from urllib.parse import urlencode, urlsplit

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandStart
from aiogram.types import ChatMemberUpdated, CallbackQuery, CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, Message, PreCheckoutQuery, WebAppInfo, FSInputFile

from redis.exceptions import LockError

from app.bot.buttons import button
from app.bot.notifications import with_close
from app.bot.membership import SUBSCRIBE_TEXT, active_member, membership_required, send_subscription_prompt
from app.bot.menu import MenuMiddleware, current_menu, menu_session, remember_menu, remember_start_command, remove_command
from app.config import settings
from app.services.store_operations import get_operations
from app.services.users import customer_is_blocked, set_bot_blocked
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
from app.services.subscriptions import PanelAccountConflict, get_user_subscription, provision_paid_payment
from app.services.users import BlockedCustomer, get_or_create_user
from app.services.devices import DeviceNotFound, DevicesUnavailable, change_customer_device, get_customer_devices
from app.services.store_documents import DOCUMENT_TITLES, get_document, list_document_kinds
from app.services.store_settings import (
    PROVIDER_LABELS, get_payment_presentation, get_store_payment_settings, is_payment_method_enabled,
    public_payment_settings, rubles_first,
)
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

PROMO_CANCEL = "promo:cancel"


def main_menu(*, has_documents: bool = False) -> InlineKeyboardMarkup:
    rows = []
    has_webapp = settings.webapp_root_url.startswith("https://")
    if has_webapp:
        rows.append([button("miniapp",  web_app=WebAppInfo(url=settings.webapp_root_url))])
    rows.extend([
        [button("profile",  callback_data="menu:profile"),
         button("subscription",  callback_data="menu:subscription")],
        [button("plans",  callback_data="menu:plans"),
         button("bonuses",  callback_data="menu:bonuses")],
        [button("purchases",  callback_data="menu:purchases"),
         button("support",  callback_data="menu:support")],
    ])
    if has_documents:
        rows.append([button("information",  callback_data="menu:information")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def back_menu(destination: str = "menu:home") -> InlineKeyboardMarkup:
    key = "home" if destination == "menu:home" else "back"
    return InlineKeyboardMarkup(inline_keyboard=[[button(key, callback_data=destination)]])


SUBSCRIPTION_STATUS = {
    "active": "Активна", "disabled": "Отключена", "expired": "Истекла", "deleted": "Удалена",
}


async def home_text(telegram_id: int, profile: dict) -> str:
    lines = [f"<b>{escape(profile['name'])}</b>"]
    if tagline := profile.get("tagline"):
        lines.append(escape(tagline))
    if description := profile.get("description"):
        lines.extend(("", escape(description)))
    if subscription := await get_user_subscription(telegram_id):
        status = SUBSCRIPTION_STATUS.get(subscription["status"], subscription["status"])
        lines.extend(("", f"Подписка · <b>{escape(status)}</b>"))
    return "\n".join(lines)


async def send_menu(
    message: Message, text: str, markup: InlineKeyboardMarkup | None, *,
    previous: Message | None = None, with_photo: bool = True,
) -> None:
    image = welcome_image_path() if with_photo else None
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


async def send_home_menu(message: Message, telegram_id: int, profile: dict) -> None:
    keyboard = main_menu(has_documents=bool(await list_document_kinds()))
    await send_menu(message, await home_text(telegram_id, profile), keyboard)


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


async def edit_menu_message(
    message: Message, text: str, *, reply_markup: InlineKeyboardMarkup | None = None,
    with_photo: bool = True,
) -> None:
    """Edit the active menu or replace it if Telegram changes the message type."""
    has_photo = bool(getattr(message, "photo", None))
    replace_photo = (not has_photo and welcome_image_path() is not None) if with_photo else has_photo
    if getattr(message, "invoice", None) or replace_photo:
        await send_menu(message, text, reply_markup, previous=message, with_photo=with_photo)
        return
    try:
        if has_photo:
            await message.edit_caption(caption=_menu_caption(text), reply_markup=reply_markup)
        else:
            await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            await send_menu(message, text, reply_markup, previous=message, with_photo=with_photo)
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
    await send_home_menu(message, message.from_user.id, profile)
    await remember_start_command(message)


@router.message(Command("admin"))
async def admin_command_handler(message: Message) -> None:
    if message.from_user is None or message.from_user.id not in settings.admin_id_set:
        await show_command_section(message, "Доступ к панели управления запрещён.", back_menu())
        return
    keyboard = main_menu(has_documents=bool(await list_document_kinds()))
    await show_command_section(message, "Откройте в Mini App «Профиль» → «Админ-панель».", keyboard)


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
        keyboard = main_menu(has_documents=bool(await list_document_kinds()))
        await edit_menu_message(callback.message, text, reply_markup=keyboard)


@router.callback_query(F.data == "menu:information")
async def information_handler(callback: CallbackQuery) -> None:
    await callback.answer()
    if not callback.message:
        return
    kinds = await list_document_kinds()
    rows = [
        [button(f"document_{kind}", title, callback_data=f"document:{kind}:0")]
        for kind, title in DOCUMENT_TITLES.items() if kind in kinds
    ]
    text = "ℹ️ <b>Информация</b>" if rows else "Документы пока не опубликованы."
    rows.extend(back_menu().inline_keyboard)
    await edit_menu_message(callback.message, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


def _document_pages(body: str) -> list[str]:
    # Keep every page within Telegram's 4096-character limit, even with emoji.
    page_size = 1800
    pages = []
    while len(body) > page_size:
        cut = body.rfind("\n", page_size // 2, page_size)
        if cut == -1:
            cut = body.rfind(" ", page_size // 2, page_size)
        end = cut + 1 if cut != -1 else page_size
        pages.append(body[:end])
        body = body[end:]
    pages.append(body)
    return pages


@router.callback_query(F.data.regexp(r"^document:(terms|privacy):[0-9]{1,2}$"))
async def document_handler(callback: CallbackQuery) -> None:
    await callback.answer()
    if not callback.message:
        return
    _, kind, requested_page = callback.data.split(":")
    body = await get_document(kind)
    keyboard = back_menu("menu:information")
    if not body:
        await edit_menu_message(callback.message, "Документ пока не опубликован.", reply_markup=keyboard)
        return
    pages = _document_pages(body)
    page = min(int(requested_page), len(pages) - 1)
    text = f"<b>{DOCUMENT_TITLES[kind]}</b>\n\n{escape(pages[page], quote=False)}"
    if len(pages) > 1:
        text += f"\n\nСтраница {page + 1} из {len(pages)}"
        navigation = []
        if page > 0:
            navigation.append(button("previous",  callback_data=f"document:{kind}:{page - 1}"))
        if page + 1 < len(pages):
            navigation.append(button("next", "Следующая ›",  callback_data=f"document:{kind}:{page + 1}"))
        keyboard.inline_keyboard.insert(0, navigation)
    await edit_menu_message(callback.message, text, reply_markup=keyboard, with_photo=False)


@router.callback_query(F.data.in_({"menu:bonuses", PROMO_CANCEL}))
async def bonuses_handler(callback: CallbackQuery) -> None:
    await callback.answer()
    if not callback.message:
        return
    telegram_id = callback.from_user.id
    referral_days = (await reward_balance(telegram_id))["days"]
    promo_days = (await promo_balance(telegram_id))["days"]
    total_days = referral_days + promo_days
    lines = ["🎁 <b>Бонусы</b>", ""]
    if total_days:
        lines.extend((
            f"Бонусных дней · <b>{total_days}</b>",
            f"За друзей · {referral_days}  |  За промокоды · {promo_days}",
            "",
            "Дни добавятся к следующей оплаченной подписке.",
        ))
    else:
        lines.append("Бонусных дней пока нет.")
    rows = [
        [button("invite",  callback_data="menu:referral")],
        [button("promo",  callback_data="menu:promo")],
    ]
    if (await get_trial_settings())["enabled"]:
        rows.append([button("trial",  callback_data="menu:trial")])
    rows.extend(back_menu().inline_keyboard)
    await edit_menu_message(
        callback.message, "\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
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
        rows.append([button(
            "plan_item", f"{title} · {price}",
            callback_data=f"plan:select:{plan['id']}",
        )])
    rows.append([button("home",  callback_data="menu:home")])
    if callback.message:
        await edit_menu_message(callback.message, 
            "📦 <b>Выберите тариф</b>\n\n"
            "Срок, трафик, устройства и цену устанавливает владелец магазина.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )


@router.callback_query(F.data == "menu:builder")
async def builder_handler(callback: CallbackQuery) -> None:
    await callback.answer("Тарифы задаёт администратор. Выберите готовый вариант.", show_alert=True)


@router.callback_query(F.data.regexp(r"^plan:select:[0-9]+(?::[0-9]+(?::r)?)?$"))
async def fixed_plan_handler(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    plan_id = int(parts[2])
    extra_devices = int(parts[3]) if len(parts) >= 4 else 0
    renewal = parts[-1] == "r"
    suffix = ":r" if renewal else ""
    plan = await get_plan(plan_id)
    slots = (await get_operations())["extra_devices_max"]
    try:
        quote = quote_plan(plan, extra_devices, slots=slots)
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer()
    controls = []
    if extra_devices:
        controls.append(button("device_minus", "Устройство",  callback_data=f"plan:select:{plan_id}:{extra_devices - 1}{suffix}"))
    if extra_devices < max_extra_devices(plan, slots):
        controls.append(button("device_plus", "Устройство",  callback_data=f"plan:select:{plan_id}:{extra_devices + 1}{suffix}"))
    price = f"{quote.amount_minor / 100:.2f} ₽" if quote.amount_minor else ""
    if quote.stars is not None:
        price = " / ".join(filter(None, (price, f"{quote.stars} ⭐")))
    key = f"{plan_id}:{extra_devices}:{plan_revision(plan)}{suffix}"
    rows = [controls] if controls else []
    rows.extend([
        [button("checkout",  callback_data=f"plan:confirm:{key}")],
        [button("back",  callback_data="menu:subscription" if renewal else "menu:plans")],
    ])
    if callback.message:
        details = [_plan_summary(quote)]
        if max_extra_devices(plan, slots):
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
    rf"^plan:(?:confirm|accept|pay:(?:stars|{'|'.join(PROVIDER_LABELS)})):[0-9]+:[0-9]+:[0-9a-f]{{20}}(?::r)?$"
))
async def fixed_plan_checkout_handler(callback: CallbackQuery) -> None:
    renewal = callback.data.endswith(":r")
    suffix = ":r" if renewal else ""
    action, plan_id, extras, revision = callback.data.removesuffix(suffix)[5:].rsplit(":", 3)
    order = {"plan_id": int(plan_id), "extra_devices": int(extras), "revision": revision}
    try:
        slots = (await get_operations())["extra_devices_max"]
        quote = quote_plan(await get_plan(order["plan_id"]), order["extra_devices"], revision, slots=slots)
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
    key = f"{plan_id}:{extras}:{revision}{suffix}"
    rows = []
    if not accepted:
        rows.extend([
            [button("terms",  url=terms)],
            [button("accept_terms",  callback_data=f"plan:accept:{key}")],
        ])
        text = "Перед оплатой примите условия продажи."
    elif action.startswith("pay:"):
        provider = action.split(":", 1)[1]
        if provider == "stars":
            await send_stars_invoice(callback.message, callback.from_user.id, renewal=renewal, **order)
        else:
            await send_external_invoice(callback.message, callback.from_user.id, provider, renewal=renewal, **order)
        return
    else:
        methods, credentials = await get_store_payment_settings()
        configured = public_payment_settings(methods, credentials)["configured"]
        for item in rubles_first(await get_payment_presentation()):
            provider, label = item["provider"], item["label"]
            if not methods.get(provider) or not configured.get(provider):
                continue
            if provider == "telegram_stars":
                if quote.stars is not None:
                    rows.append([button("payment_stars", f"{label} · {quote.stars}", callback_data=f"plan:pay:stars:{key}")])
            elif quote.amount_minor > 0:
                rows.append([button(
                    f"payment_{provider}", f"{label} · {quote.amount_minor / 100:.2f} ₽", callback_data=f"plan:pay:{provider}:{key}",
                )])
        text = "Выберите способ оплаты." if rows else "Для этого заказа нет доступных способов оплаты."
    rows.append([button("back",  callback_data=f"plan:select:{plan_id}:{extras}{suffix}")])
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


def payment_back_button(order: dict, *, renewal: bool = False) -> InlineKeyboardButton:
    if order.get("revision") is None:
        return back_menu().inline_keyboard[0][0]
    key = f"{order['plan_id']}:{order['extra_devices']}:{order['revision']}"
    return button("back",  callback_data=f"plan:confirm:{key}" + (":r" if renewal else ""))


async def send_external_invoice(message: Message, telegram_id: int, name: str, *, renewal: bool = False, **order) -> None:
    back = payment_back_button(order, renewal=renewal)
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
            [button("pay",  url=link.url)], [back],
        ]),
    )


async def send_stars_invoice(message: Message, telegram_id: int, *, renewal: bool = False, **order) -> None:
    back = payment_back_button(order, renewal=renewal)
    reply_markup = InlineKeyboardMarkup(inline_keyboard=[[back]])
    try:
        if not await is_payment_method_enabled("telegram_stars"):
            raise ValueError("Оплата в Telegram Stars отключена")
        payment, stars = await create_stars_payment_for_user(telegram_id, **order)
    except (ValueError, RuntimeError) as exc:
        await edit_menu_message(message, escape(str(exc)), reply_markup=reply_markup)
        return
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [button("pay_stars",  pay=True)], [back],
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
        [button("subscription",  callback_data="menu:subscription")],
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
    text, keyboard = await support_menu("Поддержка по оплате")
    await show_command_section(message, text, keyboard)


async def _promo_entry(telegram_id: int, *, error: str = "") -> tuple[str, InlineKeyboardMarkup]:
    balance = await promo_balance(telegram_id)
    text = (
        "🎟 <b>Промокод</b>\n\n"
        "Отправьте код следующим сообщением.\n\n"
        f"Дней за промокоды · <b>{balance['days']}</b>\n"
        "Они добавятся к следующей оплаченной подписке."
    )
    if error:
        text += f"\n\n{escape(error)} Попробуйте ещё раз."
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        button("cancel",  callback_data=PROMO_CANCEL),
    ]])
    return text, keyboard


async def _activate_promo(message: Message, telegram_id: int, code: str) -> None:
    try:
        result = await redeem_promo(telegram_id, code)
    except ValueError as exc:
        text, keyboard = await _promo_entry(telegram_id, error=str(exc))
    else:
        text = (
            f"✅ <b>Промокод {escape(result['code'])} активирован</b>\n\n"
            f"Бонусных дней добавлено · <b>{result['bonus_days']}</b>\n"
            "Они добавятся к следующей оплаченной подписке."
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [button("promo_again",  callback_data="menu:promo")],
            *back_menu("menu:bonuses").inline_keyboard,
        ])
    await show_command_section(message, text, keyboard)


@router.message(Command("promo"))
async def promo_command_handler(message: Message) -> None:
    if message.from_user is None:
        return
    telegram_id = message.from_user.id
    chunks = (message.text or "").split(maxsplit=1)
    if len(chunks) == 2:
        await _activate_promo(message, telegram_id, chunks[1])
    else:
        text, keyboard = await _promo_entry(telegram_id)
        await show_command_section(message, text, keyboard)


@router.callback_query(F.data == "menu:promo")
async def promo_menu_handler(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message:
        text, keyboard = await _promo_entry(callback.from_user.id)
        await edit_menu_message(callback.message, text, reply_markup=keyboard)


def _trial_details(config: dict) -> str:
    return (
        f"Срок · <b>{config['days']} дн.</b>\n"
        f"Трафик · <b>{format_traffic(config['traffic_gb'])}</b>\n"
        f"Устройства · <b>{config['device_limit']}</b>"
    )


@router.callback_query(F.data == "menu:trial")
async def trial_menu_handler(callback: CallbackQuery) -> None:
    await callback.answer()
    if not callback.message:
        return
    config = await get_trial_settings()
    rows = back_menu("menu:bonuses").inline_keyboard
    if not config["enabled"]:
        details = "Пробный доступ сейчас отключён."
    elif not await trial_eligible(callback.from_user.id):
        details = "Пробный доступ можно активировать один раз до первой подписки."
    else:
        details = f"{_trial_details(config)}\n\nМожно активировать один раз до первой подписки."
        rows.insert(0, [button("trial_activate", "Активировать",  callback_data="trial:claim")])
    await edit_menu_message(
        callback.message, f"🧪 <b>Пробный доступ</b>\n\n{details}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data == "trial:claim")
async def trial_claim_handler(callback: CallbackQuery) -> None:
    await callback.answer()
    if not callback.message:
        return
    rows = back_menu("menu:bonuses").inline_keyboard
    try:
        result = await claim_trial(callback.from_user.id)
    except Exception as exc:
        logger.warning("Could not activate trial access (%s)", type(exc).__name__)
        text = "🧪 <b>Пробный доступ</b>\n\nСейчас активировать доступ не удалось."
    else:
        text = f"🧪 <b>Пробный доступ активирован</b>\n\n{_trial_details(result)}"
        link = result.get("subscription_url")
        if isinstance(link, str) and link.startswith("https://"):
            rows.insert(0, [button("connect",  url=link)])
    await edit_menu_message(
        callback.message, text, reply_markup=with_close(InlineKeyboardMarkup(inline_keyboard=rows), tracked=True),
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
        [button("devices",  callback_data="menu:devices")],
        [button("instructions",  callback_data="menu:connect")],
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
            slots = (await get_operations())["extra_devices_max"]
            extras = min(extras, max_extra_devices(plan, slots))
            rows.insert(0, [button("renew",  callback_data=f"plan:select:{plan.id}:{extras}:r",
            )])
    if isinstance(url, str) and url.startswith("https://"):
        rows.insert(0, [button("connect",  url=url)])
    await edit_menu_message(callback.message, details, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data == "menu:connect")
async def connection_help_handler(callback: CallbackQuery) -> None:
    await callback.answer()
    if not callback.message:
        return
    profile = await get_store_profile()
    instructions = profile["bot_connection_instructions"].strip()
    if not instructions:
        instructions = (
            "1. Установите приложение-клиент, совместимое с подпиской вашей панели Remnawave.\n"
            "2. Откройте ссылку подключения из раздела «Подписка».\n"
            "3. Импортируйте ссылку в клиент и обновите список серверов.\n"
            "4. Выберите сервер и включите соединение.\n\n"
            "Если профиль не открывается, напишите в поддержку магазина."
        )
    await edit_menu_message(
        callback.message, f"📖 <b>Как подключить VPN</b>\n\n{escape(instructions)}",
        reply_markup=back_menu("menu:subscription"), with_photo=False,
    )


async def support_menu(title: str = "Поддержка") -> tuple[str, InlineKeyboardMarkup]:
    profile = await get_store_profile()
    contact = str(profile.get("support_contact") or "").strip()
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
    miniapp = urlsplit(settings.webapp_root_url)
    has_tickets = miniapp.scheme == "https" and bool(miniapp.hostname)
    if has_tickets:
        url = miniapp._replace(query="section=tickets").geturl()
        rows.append([button("tickets",  web_app=WebAppInfo(url=url))])
    if button_url:
        rows.append([button("support_link",  url=button_url)])
    rows.append([button("home",  callback_data="menu:home")])
    text = f"💬 <b>{title}</b>"
    if contact and not button_url:
        text += f"\n\n{escape(contact)}"
    elif not contact and not has_tickets:
        text += "\n\nСвязь с поддержкой пока не настроена."
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "menu:support")
async def support_handler(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message:
        text, keyboard = await support_menu()
        await edit_menu_message(callback.message, text, reply_markup=keyboard)


DEVICE_PAGE_SIZE = 6
DEVICE_ACTION = re.compile(r"^device:(view|name|nameback|remove|confirm):([0-9a-f]{32}):([0-9]{1,5})$")
DEVICE_ERRORS = (DeviceNotFound, DevicesUnavailable, PanelAccountConflict, BlockedCustomer)


def _device_list_callback(page: int) -> str:
    return f"menu:devices:{page}" if page else "menu:devices"


async def _device_menu(telegram_id: int, page: int, notice: str = ""):
    lines = ["📱 <b>Мои устройства</b>"]
    if notice:
        lines.append(escape(notice))
    rows = []
    try:
        devices = await get_customer_devices(telegram_id)
    except DEVICE_ERRORS as exc:
        lines.append(escape(str(exc)))
        rows.append([button("refresh_devices",  callback_data=_device_list_callback(page))])
    else:
        items = devices["items"]
        if not devices["has_subscription"]:
            lines.append("Сначала оформите подписку.")
        else:
            limit = f" из {devices['limit']}" if devices["limit"] else ""
            lines.append(f"Подключено: <b>{devices['total']}{limit}</b>")
            if not items:
                lines.append("Зарегистрированных устройств пока нет.")
            page = min(page, max(0, (len(items) - 1) // DEVICE_PAGE_SIZE))
            start = page * DEVICE_PAGE_SIZE
            for device in items[start:start + DEVICE_PAGE_SIZE]:
                name = device["name"][:36]
                label = f"{name} · {device['client']}" if device["client"] else name
                rows.append([button(
                    "device_item", label, callback_data=f"device:view:{device['id']}:{page}",
                )])
            navigation = []
            if page:
                navigation.append(button("previous", "Предыдущие",  callback_data=_device_list_callback(page - 1)))
            if start + DEVICE_PAGE_SIZE < len(items):
                navigation.append(button("next", "Следующие ›",  callback_data=_device_list_callback(page + 1)))
            if navigation:
                rows.append(navigation)
    rows.append(back_menu("menu:subscription").inline_keyboard[0])
    return "\n\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def _device_details(device: dict, page: int, notice: str = ""):
    key = device["id"]
    lines = [f"📱 <b>{escape(device['name'])}</b>"]
    if notice:
        lines.append(escape(notice))
    if device["alias"] and device["model"] != device["name"]:
        lines.append(f"Модель: {escape(device['model'])}")
    if platform := " ".join(filter(None, (device["platform"], device["os_version"]))):
        lines.append(escape(platform))
    lines.append("Клиент: " + escape(device["client"] or "Не определён"))
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=[
        [button("rename_device",  callback_data=f"device:name:{key}:{page}"),
         button("remove_device",  callback_data=f"device:remove:{key}:{page}")],
        [button("back_devices",  callback_data=_device_list_callback(page))],
    ])


@router.callback_query(F.data.regexp(r"^menu:devices(?::[0-9]{1,5})?$"))
async def devices_handler(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message:
        page = int(callback.data.split(":")[2]) if callback.data.count(":") == 2 else 0
        text, keyboard = await _device_menu(callback.from_user.id, page)
        await edit_menu_message(callback.message, text, reply_markup=keyboard)


@router.callback_query(F.data.regexp(DEVICE_ACTION))
async def device_action_handler(callback: CallbackQuery) -> None:
    await callback.answer()
    if not callback.message or callback.message.chat.type != "private":
        return
    action, device_id, page_number = DEVICE_ACTION.fullmatch(callback.data).groups()
    page = int(page_number)
    telegram_id = callback.from_user.id
    try:
        if action == "confirm":
            await change_customer_device(telegram_id, device_id, name=None)
            text, keyboard = await _device_menu(telegram_id, page, "Устройство удалено.")
        else:
            devices = await get_customer_devices(telegram_id)
            device = next((item for item in devices["items"] if item["id"] == device_id), None)
            if device is None:
                raise DeviceNotFound()
            if action == "name":
                text = f"Введите новое имя для <b>{escape(device['name'])}</b> (до 48 символов)."
                keyboard = InlineKeyboardMarkup(inline_keyboard=[[
                    button("cancel",  callback_data=f"device:nameback:{device_id}:{page}"),
                ]])
            elif action == "remove":
                text = f"Удалить <b>{escape(device['name'])}</b>? Место освободится."
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [button("confirm_device",  callback_data=f"device:confirm:{device_id}:{page}")],
                    [button("cancel",  callback_data=f"device:view:{device_id}:{page}")],
                ])
            else:
                text, keyboard = _device_details(device, page)
    except DEVICE_ERRORS as exc:
        text = escape(str(exc))
        keyboard = back_menu(_device_list_callback(page))
    await edit_menu_message(callback.message, text, reply_markup=keyboard)


@router.message(F.chat.type == "private", F.text, ~F.text.startswith("/"))
async def menu_text_handler(message: Message) -> None:
    menu = current_menu.get()
    if not menu or not message.from_user:
        return
    if PROMO_CANCEL in menu.callbacks:
        await _activate_promo(message, message.from_user.id, message.text)
        return
    pending = next((value for value in menu.callbacks if value.startswith("device:nameback:")), None)
    if not pending:
        return
    _, _, device_id, page_number = pending.split(":")
    name = " ".join(message.text.split())
    if not name or len(name) > 48:
        text = "Введите имя от 1 до 48 символов."
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            button("cancel",  callback_data=pending),
        ]])
    else:
        try:
            device = await change_customer_device(message.from_user.id, device_id, name=name)
            text, keyboard = _device_details(device, int(page_number), "Имя сохранено.")
        except DEVICE_ERRORS as exc:
            text = escape(str(exc))
            keyboard = back_menu(_device_list_callback(int(page_number)))
        except ValueError as exc:
            text = escape(str(exc))
            keyboard = InlineKeyboardMarkup(inline_keyboard=[[
                button("cancel",  callback_data=pending),
            ]])
    await show_command_section(message, text, keyboard)


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
    if not callback.message:
        return
    telegram_id = callback.from_user.id
    count = await referral_count(telegram_id)
    balance = await reward_balance(telegram_id)
    config = await get_referral_settings()
    lines = [
        "🤝 <b>Пригласить друга</b>",
        "",
        f"Приглашено друзей · <b>{count}</b>",
        f"Получено наград · <b>{balance['earned']}</b>",
        f"Доступно бонусных дней · <b>{balance['days']}</b>",
        "",
    ]
    if config["enabled"]:
        lines.append(
            f"За первую оплату друга начислим <b>{config['reward_days']} дн.</b> "
            "к вашей следующей оплаченной подписке."
        )
    else:
        lines.append("Начисление бонусов за приглашения сейчас отключено.")
    rows = back_menu("menu:bonuses").inline_keyboard
    if username := bot_username():
        link = f"https://t.me/{username}?start={referral_code(telegram_id)}"
        lines.extend(("", f"Ваша ссылка\n<code>{escape(link)}</code>"))
        rows.insert(0, [
            button("copy_invite",  copy_text=CopyTextButton(text=link)),
            button("share_invite",  url=f"https://t.me/share/url?{urlencode({'url': link})}"),
        ])
    else:
        lines.extend(("", "Ссылка для приглашения пока недоступна."))
    await edit_menu_message(
        callback.message, "\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


@router.callback_query(F.data.regexp(r"^channel:check(?::[0-9]{1,19})?$"))
async def channel_check_handler(callback: CallbackQuery) -> None:
    policy = await get_operations()
    if await membership_required(callback.bot, callback.from_user.id, policy):
        await callback.answer(SUBSCRIBE_TEXT, show_alert=True)
        return
    await callback.answer()
    is_new_user = await get_or_create_user(callback.from_user)
    inviter = callback.data.removeprefix("channel:check:")
    if is_new_user and inviter.isdigit():
        await register_referral(callback.from_user.id, int(inviter))
    await send_home_menu(callback.message, callback.from_user.id, await get_store_profile())


@router.my_chat_member()
async def bot_membership_update(event: ChatMemberUpdated) -> None:
    if event.chat.type == "private":
        await set_bot_blocked(event.chat.id, not active_member(event.new_chat_member))


@router.chat_member()
async def required_channel_update(event: ChatMemberUpdated) -> None:
    if active_member(event.new_chat_member) or not active_member(event.old_chat_member):
        return
    policy = await get_operations()
    channel = policy["required_channel"].lower()
    if channel not in {str(event.chat.id), "@" + (event.chat.username or "").lower()}:
        return
    telegram_id = event.new_chat_member.user.id
    if await customer_is_blocked(telegram_id):
        return
    try:
        async with menu_session(event.bot, telegram_id) as state:
            if state.message_id is not None and await membership_required(event.bot, telegram_id, policy):
                await send_subscription_prompt(event.bot, telegram_id, policy)
    except TelegramForbiddenError:
        await set_bot_blocked(telegram_id, True)
    except LockError:
        # Every subsequent interaction checks membership directly with Telegram.
        return
