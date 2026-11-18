import json
from typing import Any

from sqlalchemy import select

from app.db.models import Payment, TariffDraft, TermsAcceptance, User
from app.db.session import SessionLocal
from app.config import settings
from app.services.payment_providers import VerifiedPayment, get_provider
from app.services.tariffs import calculate_store_stars_price, ensure_tariff_draft
from app.services.store_settings import get_store_payment_settings


async def create_payment_for_user(telegram_id: int, provider_name: str) -> tuple[Payment, Any]:
    methods, credentials = await get_store_payment_settings()
    if provider_name == "telegram_stars" or not methods.get(provider_name):
        raise ValueError("This payment method is not enabled for the standalone storefront")
    quote = await ensure_tariff_draft(telegram_id)
    if not settings.remnawave_base_url or not settings.remnawave_api_token:
        raise RuntimeError("Remnawave is not configured; payment checkout is disabled")
    if not settings.remnawave_internal_squad_ids:
        raise RuntimeError("Set REMNAWAVE_ACTIVE_INTERNAL_SQUADS before accepting payments")
    async with SessionLocal() as session:
        user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
        if user is None:
            raise ValueError("Telegram user is not registered")
        draft = await session.scalar(
            select(TariffDraft).where(TariffDraft.user_id == user.id)
        )
        if draft is None:
            raise ValueError("Configure your tariff before paying")
        if quote.amount_minor <= 0:
            raise ValueError("Tariff amount must be greater than zero")

        if provider_name == "yoomoney" and quote.currency.upper() != "RUB":
            raise ValueError("Set TARIFF_CURRENCY=RUB to enable YooMoney wallet payments")
        payment = Payment(
            user_id=user.id,
            provider=provider_name,
            amount=quote.amount_minor,
            currency=quote.currency,
            status="pending",
            payload=json.dumps({
                "tariff": {
                    "duration_days": draft.duration_days,
                    "traffic_gb": draft.traffic_gb,
                    "device_limit": draft.device_limit,
                },
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
    if not settings.telegram_terms_url:
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


async def create_stars_payment_for_user(telegram_id: int) -> tuple[Payment, int]:
    await ensure_tariff_draft(telegram_id)
    if not settings.telegram_terms_url:
        raise RuntimeError("Configure TELEGRAM_TERMS_URL before enabling sales")
    if not await has_accepted_current_terms(telegram_id):
        raise ValueError("The user has not accepted the current terms")
    if not settings.remnawave_base_url or not settings.remnawave_api_token:
        raise RuntimeError("Remnawave is not configured; checkout is disabled")
    if not settings.remnawave_internal_squad_ids:
        raise RuntimeError("Set REMNAWAVE_ACTIVE_INTERNAL_SQUADS before accepting payments")

    async with SessionLocal() as session:
        user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
        if user is None:
            raise ValueError("Telegram user is not registered")
        draft = await session.scalar(select(TariffDraft).where(TariffDraft.user_id == user.id))
        if draft is None:
            raise ValueError("Configure your tariff before paying")
        stars = await calculate_store_stars_price(draft.duration_days, draft.traffic_gb, draft.device_limit)
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
                },
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
        return payment is not None


async def retry_paid_payments_for_user(telegram_id: int) -> None:
    from app.services.subscriptions import provision_paid_payment

    async with SessionLocal() as session:
        payment_ids = list((await session.scalars(
            select(Payment.id)
            .join(User, Payment.user_id == User.id)
            .where(
                User.telegram_id == telegram_id,
                Payment.provider == "telegram_stars",
                Payment.status == "paid",
            )
            .order_by(Payment.id)
        )).all())
    for payment_id in payment_ids:
        await provision_paid_payment(payment_id)
