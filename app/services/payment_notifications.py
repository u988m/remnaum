"""Notify a customer after a verified external payment and provisioning."""
import logging
from sqlalchemy import select

from app.bot.handlers import show_payment_result
from app.bot.runtime import bot
from app.db.models import Payment, User
from app.db.session import SessionLocal

log = logging.getLogger(__name__)


async def notify_external_payment_success(payment_id: int) -> None:
    if bot is None:
        return
    async with SessionLocal() as session:
        result = await session.execute(
            select(Payment, User.telegram_id)
            .join(User, Payment.user_id == User.id)
            .where(Payment.id == payment_id, Payment.status == "succeeded")
        )
        row = result.first()
    if not row:
        return
    _, telegram_id = row
    message = f"<b>Заказ №{payment_id} оплачен</b>\nПодписка готова к подключению."
    try:
        await show_payment_result(bot, telegram_id, message)
    except Exception:
        # E.g. customer logged in on the web but has not started the bot.
        log.info("Could not deliver payment confirmation to Telegram account %s", telegram_id)
