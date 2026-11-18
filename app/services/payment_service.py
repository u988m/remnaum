import json

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.db.models import Payment, TariffDraft, TariffPlan, TermsAcceptance, User
from app.db.session import SessionLocal
from app.config import settings
from app.services.payment_providers import PaymentLink, VerifiedPayment, get_provider
from app.services.tariffs import calculate_store_price, calculate_store_stars_price, get_store_tariff_pricing, validate_tariff_dimensions
from app.services.store_settings import get_store_payment_settings
from app.services.plans import selected_plan_for_user
from app.services.store_profile import get_terms_url


async def _checkout_selection(
    session, user: User, plan_id: int | None, dimensions: tuple[int, int, int] | None,
) -> tuple[tuple[int, int, int], TariffPlan | None]:
    if plan_id is not None:
        plan = await session.get(TariffPlan, plan_id)
        if not plan or not plan.is_active:
            raise ValueError("Выбранный тариф недоступен")
        return (plan.duration_days, plan.traffic_gb, plan.device_limit), plan
    if dimensions is not None:
        validate_tariff_dimensions(dimensions)
        return dimensions, None

    # Only the bot uses persisted drafts. Web checkouts snapshot the request
    # itself, so another tab or chat cannot change the invoice dimensions.
    draft = await session.scalar(select(TariffDraft).where(TariffDraft.user_id == user.id))
    if draft is None:
        return (settings.tariff_min_days, settings.tariff_min_traffic_gb, settings.tariff_min_devices), None
    values = (draft.duration_days, draft.traffic_gb, draft.device_limit)
    plan = await selected_plan_for_user(session, user.id)
    if plan and (not plan.is_active or values != (plan.duration_days, plan.traffic_gb, plan.device_limit)):
        raise ValueError("Выбранный тариф изменился. Выберите его заново.")
    return values, plan


def _tariff_snapshot(values: tuple[int, int, int]) -> dict:
    days, traffic, devices = values
    return {"duration_days": days, "traffic_gb": traffic, "device_limit": devices}


async def create_payment_for_user(
    telegram_id: int, provider_name: str, *,
    plan_id: int | None = None, dimensions: tuple[int, int, int] | None = None,
) -> tuple[Payment, PaymentLink]:
    methods, credentials = await get_store_payment_settings()
    if provider_name not in {"yookassa", "yoomoney", "cryptopay"} or not methods.get(provider_name):
        raise ValueError("Выбранный способ оплаты отключён администратором")
    if not await get_terms_url() or not await has_accepted_current_terms(telegram_id):
        raise ValueError("Перед покупкой примите опубликованные условия продажи")
    if not settings.remnawave_base_url or not settings.remnawave_api_token:
        raise RuntimeError("Remnawave is not configured; payment checkout is disabled")
    if not settings.remnawave_internal_squad_ids:
        raise RuntimeError("Set REMNAWAVE_ACTIVE_INTERNAL_SQUADS before accepting payments")
    async with SessionLocal() as session:
        user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
        if user is None:
            raise ValueError("Telegram user is not registered")
        values, plan = await _checkout_selection(session, user, plan_id, dimensions)
        pricing = await get_store_tariff_pricing()
        currency = pricing["currency"]
        if plan is not None and currency != "RUB":
            raise ValueError("Fixed RUB plans require RUB for external checkout")
        if provider_name == "yoomoney" and currency != "RUB":
            raise ValueError("YooMoney wallet payments require RUB")
        amount_minor = plan.price_rub_minor if plan else await calculate_store_price(*values, pricing=pricing)
        if not 1 <= amount_minor <= 2147483647:
            raise ValueError("Цена находится вне допустимого диапазона")
        payment = Payment(
            user_id=user.id,
            provider=provider_name,
            amount=amount_minor,
            currency=currency,
            status="pending",
            payload=json.dumps({
                "tariff": _tariff_snapshot(values),
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
        await mark_payment_creation_failed(payment_id)
        raise

    async with SessionLocal() as session:
        stored = await session.get(Payment, payment_id, with_for_update=True)
        if stored is None:
            raise RuntimeError("Local payment record disappeared")
        if stored.status == "pending":
            stored.provider_payment_id = link.payment_id
        await session.commit()
    return payment, link


async def mark_payment_creation_failed(payment_id: int) -> None:
    async with SessionLocal.begin() as session:
        payment = await session.get(Payment, payment_id, with_for_update=True)
        if payment is not None and payment.status == "pending":
            payment.status = "failed"


async def settle_payment(
    verified: VerifiedPayment, provider_name: str, *, telegram_id: int | None = None,
) -> bool:
    if not verified.provider_payment_id or len(verified.provider_payment_id) > 128:
        raise ValueError("Invalid provider payment ID")
    async with SessionLocal() as session:
        payment = await session.scalar(
            select(Payment)
            .where(Payment.id == verified.local_payment_id)
            .with_for_update()
        )
        if payment is None:
            raise ValueError("Local payment not found")
        if telegram_id is not None:
            owner = await session.get(User, payment.user_id)
            if owner is None or owner.telegram_id != telegram_id:
                raise ValueError("Payment owner mismatch")
        if payment.provider != provider_name:
            raise ValueError("Payment provider mismatch")
        if payment.amount != verified.amount_minor or payment.currency.upper() != verified.currency.upper():
            raise ValueError("Payment amount or currency mismatch")
        if (
            payment.provider_payment_id
            and not (
                provider_name == "yoomoney" and payment.status == "pending"
                and payment.provider_payment_id == f"ym-{payment.id}"
            )
            and payment.provider_payment_id != verified.provider_payment_id
        ):
            raise ValueError("Provider payment ID mismatch")
        if payment.status in {"paid", "succeeded"}:
            return False
        if payment.status not in {"pending", "failed"}:
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
        await session.execute(insert(TermsAcceptance).values(
            user_id=user.id, terms_version=settings.telegram_terms_version,
        ).on_conflict_do_nothing(constraint="uq_terms_acceptance_user_version"))
        await session.commit()


async def create_stars_payment_for_user(
    telegram_id: int, *, plan_id: int | None = None, dimensions: tuple[int, int, int] | None = None,
) -> tuple[Payment, int]:
    if not await get_terms_url():
        raise RuntimeError("Publish the terms URL in the admin branding settings before enabling sales")
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
        values, plan = await _checkout_selection(session, user, plan_id, dimensions)
        stars = plan.price_stars if plan else await calculate_store_stars_price(*values)
        if not 1 <= stars <= 2147483647:
            raise ValueError("Цена находится вне допустимого диапазона")
        payment = Payment(
            user_id=user.id,
            provider="telegram_stars",
            amount=stars,
            currency="XTR",
            status="pending",
            payload=json.dumps({
                "tariff": _tariff_snapshot(values),
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
        return payment is not None


async def recent_purchases(telegram_id: int, limit: int = 20) -> list[dict]:
    async with SessionLocal() as session:
        payments = (await session.scalars(
            select(Payment).join(User)
            .where(User.telegram_id == telegram_id)
            .order_by(Payment.created_at.desc(), Payment.id.desc()).limit(limit)
        )).all()
        return [{
            "id": payment.id, "provider": payment.provider, "amount": payment.amount,
            "currency": payment.currency, "status": payment.status,
            "created_at": payment.created_at.isoformat(),
        } for payment in payments]
