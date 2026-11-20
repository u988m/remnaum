"""Close unpaid orders and deliver expiry notices from persisted payment state."""
import asyncio
import logging
from datetime import datetime, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError
from sqlalchemy import select, update

from app.bot.notifications import with_close
from app.db.models import Payment, User
from app.db.session import SessionLocal
from app.services.payment_providers import PAYMENT_WINDOW

log = logging.getLogger(__name__)

SCAN_INTERVAL_SECONDS = 15
EXPIRY_BATCH_SIZE = 100
NOTICE_BATCH_SIZE = 10


async def expire_unpaid_payments() -> int:
    now = datetime.now(timezone.utc)
    due = (
        select(Payment.id)
        .where(Payment.status == "pending", Payment.created_at <= now - PAYMENT_WINDOW)
        .order_by(Payment.created_at, Payment.id)
        .limit(EXPIRY_BATCH_SIZE)
        .with_for_update(skip_locked=True)
    )
    async with SessionLocal.begin() as session:
        expired = await session.scalars(
            update(Payment).where(Payment.id.in_(due))
            .values(status="cancelled", expired_at=now)
            .returning(Payment.id)
        )
        return len(expired.all())


def pending_notice_conditions():
    return (
        Payment.status == "cancelled",
        Payment.expired_at.is_not(None),
        Payment.expiry_notice_completed_at.is_(None),
    )


async def _deliver_expiry_notice(bot: Bot, payment_id: int) -> None:
    async with SessionLocal.begin() as session:
        result = await session.execute(
            select(Payment, User.telegram_id, User.bot_blocked_at)
            .join(User, Payment.user_id == User.id)
            .where(Payment.id == payment_id, *pending_notice_conditions())
            .with_for_update(of=Payment, skip_locked=True)
        )
        record = result.one_or_none()
        if record is None:
            return
        payment, telegram_id, bot_blocked_at = record
        if bot_blocked_at is None:
            try:
                async with asyncio.timeout(8):
                    await bot.send_message(
                        telegram_id,
                        f"Заказ №{payment.id} отменён. Оплата не поступила в течение 30 минут.\n\n"
                        "Если вы уже оплатили, дождитесь подтверждения. "
                        "Для новой оплаты создайте счёт в разделе «Тарифы».",
                        reply_markup=with_close(),
                    )
            except (TelegramForbiddenError, TelegramBadRequest):
                log.info("Cannot deliver expiry notice for order %s to Telegram", payment_id)
            except (TelegramAPIError, TimeoutError) as exc:
                log.info("Expiry notice for order %s will be retried (%s)", payment_id, type(exc).__name__)
                return
        payment.expiry_notice_completed_at = datetime.now(timezone.utc)


async def deliver_expiry_notices(bot: Bot, *, after_id: int = 0) -> tuple[int, int]:
    async with SessionLocal() as session:
        payment_ids = (await session.scalars(
            select(Payment.id).join(User, Payment.user_id == User.id)
            .where(Payment.id > after_id, *pending_notice_conditions())
            .order_by(Payment.id)
            .limit(NOTICE_BATCH_SIZE)
        )).all()
    if not payment_ids:
        return 0, 0
    results = await asyncio.gather(*(_deliver_expiry_notice(bot, payment_id) for payment_id in payment_ids),
                                   return_exceptions=True)
    for payment_id, result in zip(payment_ids, results):
        if isinstance(result, BaseException):
            log.error("Expiry notification transaction failed for order %s", payment_id, exc_info=result)
    return payment_ids[-1], len(payment_ids)


async def run_payment_expiry_forever(bot: Bot | None) -> None:
    cursor = 0
    while True:
        delay = SCAN_INTERVAL_SECONDS
        try:
            if await expire_unpaid_payments() == EXPIRY_BATCH_SIZE:
                delay = 1
            if bot:
                cursor, count = await deliver_expiry_notices(bot, after_id=cursor)
                if count == NOTICE_BATCH_SIZE:
                    delay = 1
        except Exception:
            log.exception("Unpaid payment expiry scan failed")
        await asyncio.sleep(delay)
