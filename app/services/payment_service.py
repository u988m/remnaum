import asyncio
import json
import logging
from typing import Any
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.db.models import Subscription, Payment, TariffDraft, TariffPlan, TermsAcceptance, User
from app.db.session import SessionLocal
from app.config import settings
from app.services.payment_providers import VerifiedPayment, get_provider
from app.services.tariffs import TariffQuote, calculate_store_price, calculate_store_stars_price, ensure_tariff_draft
from app.services.store_settings import PROVIDER_LABELS, get_store_payment_settings, public_payment_settings
from app.services.plans import PlanQuote, quote_plan, selected_plan_for_user
from app.services.squads import fetch_catalog, mode_squads, require_available
from app.services.remnawave import RemnawaveClient
from app.services.subscriptions import (
    get_panel_user, has_panel_squads, matches_panel_account, paid_tariff_key, panel_expiry,
)
from app.services.store_profile import get_terms_url
from app.services.users import BlockedCustomer

logger = logging.getLogger(__name__)


class TermsAcceptanceRequired(ValueError):
    def __init__(self):
        super().__init__("Перед оплатой примите условия продажи.")


class PaymentProviderUnavailable(RuntimeError):
    def __init__(self):
        super().__init__("Платёжная система не смогла создать счёт. Повторите попытку или выберите другой способ оплаты.")


async def checkout_squads(session, user, plan) -> tuple[list[str], bool]:
    selected = await mode_squads(session, "flexible") if plan is None else (plan.internal_squads or [])
    tariff_key = str(plan.id) if plan else "flexible"
    preserve = await validate_checkout_access(session, user.id, user.telegram_id, tariff_key, selected)
    return selected, preserve


async def validate_checkout_access(session, user_id, telegram_id, tariff_key, selected, allow_preserve=True):
    current = await session.scalar(select(Subscription).where(
        Subscription.user_id == user_id
    ).order_by(Subscription.created_at.desc(), Subscription.id.desc()).limit(1))
    try:
        panel_user = await get_panel_user(RemnawaveClient(), telegram_id)
        if current is not None and not matches_panel_account(current.panel_user_id, panel_user):
            raise ValueError("Аккаунт подписки в Remnawave отсутствует или был заменён")
        now = datetime.now(timezone.utc)
        remote_expiry = panel_expiry(panel_user)
        if (allow_preserve and tariff_key is not None and current is not None and current.status == "active" and current.expires_at
                and current.expires_at > now and panel_user.get("status") == "ACTIVE"
                and remote_expiry and remote_expiry > now and has_panel_squads(panel_user)
                and await paid_tariff_key(session, current) == tariff_key):
            return True
        if not selected:
            require_available(selected, {})
        require_available(selected, await fetch_catalog())
    except httpx.HTTPError as exc:
        raise ValueError("Не удалось проверить доступ в Remnawave. Повторите попытку позже.") from exc
    return False


async def _payment_quote(
    session, user_id: int, draft_quote: TariffQuote | None, plan_id: int | None,
    extra_devices: int, revision: str | None,
) -> tuple[TariffPlan | None, TariffQuote]:
    if plan_id is not None:
        plan = await session.get(TariffPlan, plan_id)
        return plan, quote_plan(plan, extra_devices, revision)

    plan = await selected_plan_for_user(session, user_id)
    draft = await session.scalar(select(TariffDraft).where(TariffDraft.user_id == user_id))
    if draft is None or draft_quote is None:
        raise ValueError("Настройте тариф перед оплатой")
    if plan is not None:
        quote = quote_plan(plan)
        if (draft.duration_days, draft.traffic_gb, draft.device_limit, draft.amount_minor) != (
            quote.duration_days, quote.traffic_gb, quote.device_limit, quote.amount_minor
        ):
            raise ValueError("Условия тарифа изменились. Выберите тариф заново.")
        return plan, quote
    amount = await calculate_store_price(draft.duration_days, draft.traffic_gb, draft.device_limit)
    return None, TariffQuote(draft.duration_days, draft.traffic_gb, draft.device_limit, amount, draft_quote.currency)


def _order_snapshot(quote: TariffQuote, plan: TariffPlan | None, squads: list[str], preserve: bool) -> dict:
    order = {
        "tariff": {
            "duration_days": quote.duration_days,
            "traffic_gb": quote.traffic_gb,
            "device_limit": quote.device_limit,
            "internal_squads": squads,
            "preserve_squads": preserve,
        },
        "plan_id": plan.id if plan is not None else None,
    }
    if isinstance(quote, PlanQuote):
        order["tariff"].update(name=quote.name, devices_included=quote.devices_included, extra_devices=quote.extra_devices)
    return order


async def create_payment_for_user(
    telegram_id: int, provider_name: str, *,
    plan_id: int | None = None, extra_devices: int = 0, revision: str | None = None,
) -> tuple[Payment, Any]:
    methods, credentials = await get_store_payment_settings()
    if provider_name not in PROVIDER_LABELS or not methods.get(provider_name):
        raise ValueError("Выбранный способ оплаты отключён администратором")
    await require_checkout_terms(telegram_id)
    if not public_payment_settings(methods, credentials)["configured"][provider_name]:
        raise ValueError("Реквизиты выбранного способа оплаты недоступны. Обратитесь в поддержку магазина.")
    draft_quote = await ensure_tariff_draft(telegram_id) if plan_id is None else None
    if not settings.remnawave_base_url or not settings.remnawave_api_token:
        raise RuntimeError("Магазин не подключён к Remnawave. Обратитесь в поддержку.")
    async with SessionLocal() as session:
        user = await session.scalar(select(User).where(User.telegram_id == telegram_id).with_for_update())
        if user is None:
            raise ValueError("Telegram user is not registered")
        if user.blocked_at:
            raise BlockedCustomer()
        plan, quote = await _payment_quote(session, user.id, draft_quote, plan_id, extra_devices, revision)
        amount_minor = quote.amount_minor
        if amount_minor <= 0:
            raise ValueError("Оплата в рублях недоступна для выбранного числа устройств.")

        if provider_name == "yoomoney" and quote.currency.upper() != "RUB":
            raise ValueError("Set TARIFF_CURRENCY=RUB to enable YooMoney wallet payments")
        selected_squads, preserve = await checkout_squads(session, user, plan)
        payment = Payment(
            user_id=user.id,
            provider=provider_name,
            amount=amount_minor,
            currency=quote.currency,
            status="pending",
            payload=json.dumps(_order_snapshot(quote, plan, selected_squads, preserve)),
        )
        session.add(payment)
        await session.commit()
        await session.refresh(payment)
        payment_id = payment.id
        amount = payment.amount
        currency = payment.currency

    provider = get_provider(provider_name, credentials.get(provider_name))
    try:
        link = await provider.create_payment(
            local_payment_id=payment_id,
            amount_minor=amount,
            currency=currency,
            description=f"Remnaum VPN subscription #{payment_id}",
            telegram_id=telegram_id,
        )
        url = urlsplit(link.url)
        if url.scheme != "https" or not url.hostname:
            raise ValueError("Payment provider did not return an HTTPS payment URL")
    except Exception as exc:
        status_code = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
        logger.warning("Could not create %s invoice %s (%s, HTTP %s)",
                       provider_name, payment_id, type(exc).__name__, status_code)
        async with SessionLocal() as session:
            failed = await session.get(Payment, payment_id, with_for_update=True)
            if failed is not None and failed.status == "pending":
                failed.status = "failed"
                await session.commit()
        raise PaymentProviderUnavailable() from exc

    async with SessionLocal() as session:
        stored = await session.get(Payment, payment_id, with_for_update=True)
        if stored is None:
            raise RuntimeError("Local payment record disappeared")
        if stored.status == "pending":
            stored.provider_payment_id = link.payment_id
        await session.commit()
    return payment, link


def _tariff_currency() -> str:
    from app.config import settings
    return settings.tariff_currency.upper()


async def settle_payment(verified: VerifiedPayment, provider_name: str) -> bool:
    async with SessionLocal() as session:
        payment = await session.scalar(
            select(Payment)
            .where(Payment.id == verified.local_payment_id)
            .with_for_update()
        )
        if payment is None:
            raise ValueError("Local payment not found")
        if payment.provider != provider_name:
            raise ValueError("Payment provider mismatch")
        if payment.amount != verified.amount_minor or payment.currency.upper() != verified.currency.upper():
            raise ValueError("Payment amount or currency mismatch")
        if (
            payment.provider_payment_id
            and not payment.provider_payment_id.startswith("ym-")
            and payment.provider_payment_id != verified.provider_payment_id
        ):
            raise ValueError("Provider payment ID mismatch")
        if payment.status == "succeeded":
            return False
        can_provision = payment.user_id is not None and await session.scalar(select(User.id).where(
            User.id == payment.user_id, User.blocked_at.is_(None),
        )) is not None
        if payment.status == "paid":
            return can_provision
        if payment.status != "pending":
            raise ValueError("Payment is not pending")
        payment.provider_payment_id = verified.provider_payment_id
        payment.status = "paid"
        await session.commit()
        return can_provision


async def has_accepted_current_terms(telegram_id: int) -> bool:
    async with SessionLocal() as session:
        accepted = await session.scalar(
            select(TermsAcceptance.id)
            .join(User, TermsAcceptance.user_id == User.id)
            .where(
                User.telegram_id == telegram_id, User.blocked_at.is_(None),
                TermsAcceptance.terms_version == settings.telegram_terms_version,
            )
        )
        return accepted is not None


async def require_checkout_terms(telegram_id: int) -> None:
    if await get_terms_url() and not await has_accepted_current_terms(telegram_id):
        raise TermsAcceptanceRequired()


async def accept_current_terms(telegram_id: int) -> None:
    if not await get_terms_url():
        return
    async with SessionLocal() as session:
        user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
        if user is None:
            raise ValueError("Telegram user is not registered")
        await session.execute(insert(TermsAcceptance).values(
            user_id=user.id, terms_version=settings.telegram_terms_version,
        ).on_conflict_do_nothing(index_elements=[TermsAcceptance.user_id, TermsAcceptance.terms_version]))
        await session.commit()


async def create_stars_payment_for_user(
    telegram_id: int, *, plan_id: int | None = None, extra_devices: int = 0, revision: str | None = None,
) -> tuple[Payment, int]:
    await require_checkout_terms(telegram_id)
    draft_quote = await ensure_tariff_draft(telegram_id) if plan_id is None else None
    if not settings.remnawave_base_url or not settings.remnawave_api_token:
        raise RuntimeError("Магазин не подключён к Remnawave. Обратитесь в поддержку.")

    async with SessionLocal() as session:
        user = await session.scalar(select(User).where(User.telegram_id == telegram_id).with_for_update())
        if user is None:
            raise ValueError("Telegram user is not registered")
        if user.blocked_at:
            raise BlockedCustomer()
        plan, quote = await _payment_quote(session, user.id, draft_quote, plan_id, extra_devices, revision)
        if isinstance(quote, PlanQuote):
            if quote.stars is None:
                raise ValueError("Для этих устройств оплата в Telegram Stars недоступна")
            stars = quote.stars
        else:
            stars = await calculate_store_stars_price(quote.duration_days, quote.traffic_gb, quote.device_limit)
        selected_squads, preserve = await checkout_squads(session, user, plan)
        payment = Payment(
            user_id=user.id,
            provider="telegram_stars",
            amount=stars,
            currency="XTR",
            status="pending",
            payload=json.dumps(_order_snapshot(quote, plan, selected_squads, preserve) | {
                "terms_version": settings.telegram_terms_version,
            }),
        )
        session.add(payment)
        await session.commit()
        await session.refresh(payment)
        return payment, stars


async def validate_stars_pre_checkout(
    telegram_id: int, payment_id: int, amount: int, currency: str, payload: str
) -> bool:
    if currency != "XTR" or payload != f"remnaum:{payment_id}":
        return False
    try:
        await require_checkout_terms(telegram_id)
    except TermsAcceptanceRequired:
        return False
    async with SessionLocal() as session:
        payment = await session.scalar(
            select(Payment)
            .join(User, Payment.user_id == User.id)
            .where(
                Payment.id == payment_id,
                Payment.provider == "telegram_stars",
                Payment.status == "pending",
                Payment.amount == amount,
                Payment.currency == "XTR",
                User.telegram_id == telegram_id, User.blocked_at.is_(None),
            )
        )
        if payment is None:
            return False
        order = json.loads(payment.payload or "{}")
        snapshot = order.get("tariff") or {}
        tariff_key = str(order["plan_id"] or "flexible") if "plan_id" in order else None
        try:
            async with asyncio.timeout(8):
                await validate_checkout_access(
                    session, payment.user_id, telegram_id, tariff_key,
                    snapshot.get("internal_squads", settings.remnawave_internal_squad_ids),
                    allow_preserve=snapshot.get("preserve_squads", True),
                )
        except (ValueError, RuntimeError, TimeoutError):
            return False
        return True


async def retry_paid_payments_for_user(telegram_id: int) -> None:
    from app.services.subscriptions import provision_paid_payment

    async with SessionLocal() as session:
        payment_ids = list((await session.scalars(
            select(Payment.id)
            .join(User, Payment.user_id == User.id)
            .where(
                User.telegram_id == telegram_id, User.blocked_at.is_(None),
                Payment.status == "paid",
            )
            .order_by(Payment.id)
        )).all())
    for payment_id in payment_ids:
        await provision_paid_payment(payment_id)
