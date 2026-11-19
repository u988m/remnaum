"""Retry verified payments independently of payment webhooks and customer actions.

A batch cursor prevents a permanently failing old order from starving newer ones.
The existing row lock and Remnawave payment marker make replays idempotent.
"""
import asyncio
import logging

from sqlalchemy import select

from app.db.models import Payment, User
from app.db.session import SessionLocal
from app.services.payment_notifications import notify_external_payment_success
from app.services.subscriptions import provision_paid_payment

log = logging.getLogger(__name__)

RECOVERY_INTERVAL_SECONDS = 60
RECOVERY_BATCH_SIZE = 50


async def recover_paid_payments_batch(*, after_id: int = 0, limit: int = RECOVERY_BATCH_SIZE) -> int:
    """Process a bounded slice of paid orders; return the next scan cursor.

    Failures are isolated to individual payments and retried on the next scan.
    This function does not mark an order succeeded unless Remnawave confirms it.
    """
    if limit < 1 or limit > 500:
        raise ValueError("Invalid recovery batch size")

    async with SessionLocal() as session:
        query = (
            select(Payment.id).join(User)
            .where(User.blocked_at.is_(None), Payment.status == "paid", Payment.id > after_id)
            .order_by(Payment.id)
            .limit(limit)
        )
        payment_ids = list((await session.scalars(query)).all())
        if not payment_ids and after_id:
            payment_ids = list((await session.scalars(
                select(Payment.id).join(User)
                .where(User.blocked_at.is_(None), Payment.status == "paid")
                .order_by(Payment.id)
                .limit(limit)
            )).all())

    if not payment_ids:
        return 0

    for payment_id in payment_ids:
        try:
            provisioned = await provision_paid_payment(payment_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Could not recover verified payment %s; will retry", payment_id)
            continue

        if provisioned:
            try:
                await notify_external_payment_success(payment_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Payment %s provisioned but notification failed", payment_id)

    return payment_ids[-1]


async def run_payment_recovery_forever() -> None:
    """Resume unfinished paid orders after a restart or a lost webhook."""
    cursor = 0
    while True:
        try:
            cursor = await recover_paid_payments_batch(after_id=cursor)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Paid payment recovery scan failed")
        await asyncio.sleep(RECOVERY_INTERVAL_SECONDS)
