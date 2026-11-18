import json
from typing import Any

from sqlalchemy import select

from app.db.models import Payment, TariffDraft, User
from app.db.session import SessionLocal
from app.config import settings
from app.services.payment_providers import VerifiedPayment, get_provider


async def create_payment_for_user(telegram_id: int, provider_name: str) -> tuple[Payment, Any]:
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
        if draft.amount_minor <= 0:
            raise ValueError("Tariff amount must be greater than zero")

        if provider_name == "yoomoney" and _tariff_currency().upper() != "RUB":
            raise ValueError("Set TARIFF_CURRENCY=RUB to enable YooMoney wallet payments")
        payment = Payment(
            user_id=user.id,
            provider=provider_name,
            amount=draft.amount_minor,
            currency=_tariff_currency(),
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

    provider = get_provider(provider_name)
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
