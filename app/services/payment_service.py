import asyncio
import json
from typing import Any
from datetime import datetime, timezone

import httpx

from sqlalchemy import select

from app.db.models import Subscription, Payment, TariffDraft, TermsAcceptance, User
from app.db.session import SessionLocal
from app.config import settings
from app.services.payment_providers import VerifiedPayment, get_provider
from app.services.tariffs import calculate_store_stars_price, ensure_tariff_draft
from app.services.store_settings import get_store_payment_settings
from app.services.plans import selected_plan_for_user
from app.services.squads import fetch_catalog, mode_squads, require_available
from app.services.remnawave import RemnawaveClient
from app.services.subscriptions import (
    get_panel_user, has_panel_squads, matches_panel_account, paid_tariff_key, panel_expiry,
)
from app.services.store_profile import get_terms_url


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


async def create_payment_for_user(telegram_id: int, provider_name: str, expected_plan_id: int | None = None) -> tuple[Payment, Any]:
    methods, credentials = await get_store_payment_settings()
    if provider_name not in {"yookassa", "yoomoney", "cryptopay"} or not methods.get(provider_name):
        raise ValueError("Выбранный способ оплаты отключён администратором")
    if not await get_terms_url() or not await has_accepted_current_terms(telegram_id):
        raise ValueError("Перед покупкой примите опубликованные условия продажи")
    quote = await ensure_tariff_draft(telegram_id)
    if not settings.remnawave_base_url or not settings.remnawave_api_token:
        raise RuntimeError("Remnawave is not configured; payment checkout is disabled")
    async with SessionLocal() as session:
        user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
        if user is None:
            raise ValueError("Telegram user is not registered")
        draft = await session.scalar(
            select(TariffDraft).where(TariffDraft.user_id == user.id)
        )
        if draft is None:
            raise ValueError("Configure your tariff before paying")
        plan = await selected_plan_for_user(session, user.id)
        if expected_plan_id is not None and (plan is None or plan.id != expected_plan_id):
            raise ValueError("Выбранный тариф изменился. Откройте тариф ещё раз.")
        if plan is not None:
            if not plan.is_active or (
                draft.duration_days, draft.traffic_gb, draft.device_limit
            ) != (plan.duration_days, plan.traffic_gb, plan.device_limit):
                raise ValueError("Selected plan has changed; select it again")
            if quote.currency.upper() != "RUB":
                raise ValueError("Fixed RUB plans require TARIFF_CURRENCY=RUB for external checkout")
            amount_minor = plan.price_rub_minor
        else:
            amount_minor = quote.amount_minor
        if amount_minor <= 0:
            raise ValueError("Tariff amount must be greater than zero")

        if provider_name == "yoomoney" and quote.currency.upper() != "RUB":
            raise ValueError("Set TARIFF_CURRENCY=RUB to enable YooMoney wallet payments")
        selected_squads, preserve = await checkout_squads(session, user, plan)
        payment = Payment(
            user_id=user.id,
            provider=provider_name,
            amount=amount_minor,
            currency=quote.currency,
            status="pending",
            payload=json.dumps({
                "tariff": {
                    "duration_days": draft.duration_days,
                    "traffic_gb": draft.traffic_gb,
                    "device_limit": draft.device_limit,
                    "internal_squads": selected_squads,
                    "preserve_squads": preserve,
                },
                "plan_id": plan.id if plan is not None else None,
            }),
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
        )
    except Exception:
        async with SessionLocal() as session:
            failed = await session.get(Payment, payment_id)
            if failed is not None and failed.status == "pending":
                failed.status = "failed"
                await session.commit()
        raise

    async with SessionLocal() as session:
        stored = await session.get(Payment, payment_id)
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
        if payment.status == "paid":
            return True
        if payment.status != "pending":
            raise ValueError("Payment is not pending")
        payment.provider_payment_id = verified.provider_payment_id
        payment.status = "paid"
        await session.commit()
        return True


async def has_accepted_current_terms(telegram_id: int) -> bool:
    async with SessionLocal() as session:
        accepted = await session.scalar(
            select(TermsAcceptance.id)
            .join(User, TermsAcceptance.user_id == User.id)
            .where(
                User.telegram_id == telegram_id,
                TermsAcceptance.terms_version == settings.telegram_terms_version,
            )
        )
        return accepted is not None


async def accept_current_terms(telegram_id: int) -> None:
    if not await get_terms_url():
        raise RuntimeError("Terms URL is not configured")
    async with SessionLocal() as session:
        user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
        if user is None:
            raise ValueError("Telegram user is not registered")
        existing = await session.scalar(
            select(TermsAcceptance).where(
                TermsAcceptance.user_id == user.id,
                TermsAcceptance.terms_version == settings.telegram_terms_version,
            )
        )
        if existing is None:
            session.add(TermsAcceptance(
                user_id=user.id,
                terms_version=settings.telegram_terms_version,
            ))
            await session.commit()


async def create_stars_payment_for_user(telegram_id: int, expected_plan_id: int | None = None) -> tuple[Payment, int]:
    await ensure_tariff_draft(telegram_id)
    if not await get_terms_url():
        raise RuntimeError("Publish the terms URL in the admin branding settings before enabling sales")
    if not await has_accepted_current_terms(telegram_id):
        raise ValueError("The user has not accepted the current terms")
    if not settings.remnawave_base_url or not settings.remnawave_api_token:
        raise RuntimeError("Remnawave is not configured; checkout is disabled")

    async with SessionLocal() as session:
        user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
        if user is None:
            raise ValueError("Telegram user is not registered")
        draft = await session.scalar(select(TariffDraft).where(TariffDraft.user_id == user.id))
        if draft is None:
            raise ValueError("Configure your tariff before paying")
        plan = await selected_plan_for_user(session, user.id)
        if expected_plan_id is not None and (plan is None or plan.id != expected_plan_id):
            raise ValueError("Выбранный тариф изменился. Откройте тариф ещё раз.")
        if plan is not None:
            if not plan.is_active or (
                draft.duration_days, draft.traffic_gb, draft.device_limit
            ) != (plan.duration_days, plan.traffic_gb, plan.device_limit):
                raise ValueError("Selected plan has changed; select it again")
            stars = plan.price_stars
        else:
            stars = await calculate_store_stars_price(draft.duration_days, draft.traffic_gb, draft.device_limit)
        selected_squads, preserve = await checkout_squads(session, user, plan)
        payment = Payment(
            user_id=user.id,
            provider="telegram_stars",
            amount=stars,
            currency="XTR",
            status="pending",
            payload=json.dumps({
                "tariff": {
                    "duration_days": draft.duration_days,
                    "traffic_gb": draft.traffic_gb,
                    "device_limit": draft.device_limit,
                    "internal_squads": selected_squads,
                    "preserve_squads": preserve,
                },
                "plan_id": plan.id if plan is not None else None,
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
    async with SessionLocal() as session:
        payment = await session.scalar(
            select(Payment)
            .join(User, Payment.user_id == User.id)
            .join(
                TermsAcceptance,
                (TermsAcceptance.user_id == User.id)
                & (TermsAcceptance.terms_version == settings.telegram_terms_version),
            )
            .where(
                Payment.id == payment_id,
                Payment.provider == "telegram_stars",
                Payment.status == "pending",
                Payment.amount == amount,
                Payment.currency == "XTR",
                User.telegram_id == telegram_id,
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
                User.telegram_id == telegram_id,
                Payment.status == "paid",
            )
            .order_by(Payment.id)
        )).all())
    for payment_id in payment_ids:
        await provision_paid_payment(payment_id)
