"""Use verified payments in PostgreSQL as the durable activation queue."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Payment, PaymentActivation, User
from app.db.session import SessionLocal
from app.services.payment_notifications import notify_payment_activated
from app.services.promos import pending_promo_redemptions
from app.services.referrals import pending_rewards_for_payment
from app.services.subscriptions import provision_paid_payment

log = logging.getLogger(__name__)
WORKER_CONCURRENCY = 4
POLL_INTERVAL = 3


def _paid_orders():
    prepared = case((PaymentActivation.bonus_days.is_not(None), 0), else_=1)
    return (
        select(Payment.id, Payment.user_id, PaymentActivation.next_attempt_at,
               func.row_number().over(partition_by=Payment.user_id, order_by=(prepared, Payment.id)).label("position"))
        .outerjoin(PaymentActivation, PaymentActivation.payment_id == Payment.id)
        .where(Payment.status == "paid")
    )


async def _get_ready_order(session: AsyncSession, payment_id: int) -> tuple[Payment, User] | None:
    owner_id = select(Payment.user_id).where(Payment.id == payment_id).scalar_subquery()
    # The same lock used by free trials protects first activation and renewals.
    user = await session.scalar(select(User).where(User.id == owner_id).with_for_update(key_share=True))
    if user is None:
        return None
    orders = _paid_orders().where(Payment.user_id == user.id).subquery()
    ready = await session.scalar(select(orders.c.id).where(
        orders.c.position == 1, orders.c.id == payment_id,
        or_(orders.c.next_attempt_at.is_(None), orders.c.next_attempt_at <= func.now()),
    ))
    if ready is None:
        return None
    payment = await session.get(Payment, payment_id)
    return payment, user


async def _prepare_payment(payment_id: int) -> bool:
    async with SessionLocal.begin() as session:
        ready = await _get_ready_order(session, payment_id)
        if ready is None:
            return False
        payment, user = ready
        activation = await session.get(PaymentActivation, payment.id)
        if activation is None:
            activation = PaymentActivation(payment_id=payment.id)
            session.add(activation)
        if activation.bonus_days is None:
            # Commit credit allocation before calling the Panel. A retry after a
            # remote success uses the same bonuses, even if more arrived later.
            rewards = await pending_rewards_for_payment(session, user.id)
            promos = await pending_promo_redemptions(session, user.id)
            activation.bonus_days = sum(reward.days for reward in rewards) + sum(promo.bonus_days for promo in promos)
            for reward in rewards:
                reward.redeemed_payment_id = payment.id
            for promo in promos:
                promo.applied_payment_id = payment.id
        return True


def _failure_code(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"panel_http_{exc.response.status_code}"
    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        return "panel_timeout"
    if isinstance(exc, httpx.TransportError):
        return "panel_unreachable"
    return "activation_error"


async def activate_payment(payment_id: int) -> None:
    if not await _prepare_payment(payment_id):
        return
    async with SessionLocal.begin() as session:
        ready = await _get_ready_order(session, payment_id)
        if ready is None:
            return
        payment, user = ready
        activation = await session.get(PaymentActivation, payment.id, with_for_update=True)
        try:
            # Roll back partial local writes, but keep failure details and the
            # original credit allocation. The Panel marker handles an ambiguous
            # response or a crash after the remote write.
            async with session.begin_nested(), asyncio.timeout(80):
                await provision_paid_payment(session, payment, user, activation)
        except Exception as exc:
            activation.attempts += 1
            delay = min(15 * 2 ** min(activation.attempts - 1, 6), 900)
            activation.next_attempt_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
            activation.last_error = _failure_code(exc)
            log.warning("Order %d activation attempt %d failed (%s)", payment_id, activation.attempts,
                        activation.last_error)
            return
        activation.attempts += 1
        activation.next_attempt_at = None
        activation.last_error = None
    await notify_payment_activated(payment_id)


async def schedule_activation_retry(payment_id: int) -> str | None:
    async with SessionLocal.begin() as session:
        status = await session.scalar(select(Payment.status).where(Payment.id == payment_id))
        if status == "paid":
            activation = await session.get(PaymentActivation, payment_id, with_for_update={"skip_locked": True})
            if activation is not None:
                activation.next_attempt_at = None
        return status


async def has_paid_orders(telegram_id: int) -> bool:
    async with SessionLocal() as session:
        return await session.scalar(
            select(Payment.id).join(User)
            .where(User.telegram_id == telegram_id, Payment.status == "paid").limit(1)
        ) is not None


async def run_activation_worker() -> None:
    while True:
        try:
            orders = _paid_orders().subquery()
            async with SessionLocal() as session:
                payment_ids = (await session.scalars(
                    select(orders.c.id).where(
                        orders.c.position == 1,
                        or_(orders.c.next_attempt_at.is_(None), orders.c.next_attempt_at <= func.now()),
                    ).order_by(orders.c.id).limit(WORKER_CONCURRENCY)
                )).all()
            outcomes = await asyncio.gather(*(activate_payment(order_id) for order_id in payment_ids),
                                            return_exceptions=True)
            for order_id, outcome in zip(payment_ids, outcomes):
                if isinstance(outcome, Exception):
                    log.error("Order %d worker error (%s)", order_id, type(outcome).__name__)
            if payment_ids and not any(isinstance(outcome, BaseException) for outcome in outcomes):
                continue
        except Exception as exc:
            log.error("Payment activation worker error (%s)", type(exc).__name__)
        await asyncio.sleep(POLL_INTERVAL)
