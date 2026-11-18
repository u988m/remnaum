"""Notify a customer after a verified payment and successful activation."""
import asyncio
import logging

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select

from app.db.models import Payment, User
from app.db.session import SessionLocal

log = logging.getLogger(__name__)


async def notify_payment_activated(payment_id: int) -> None:
    from app.bot.runtime import bot

    if bot is None:
        return
    async with SessionLocal() as session:
        telegram_id = await session.scalar(
            select(User.telegram_id)
            .join(Payment, Payment.user_id == User.id)
            .where(Payment.id == payment_id, Payment.status == "succeeded")
        )
    if telegram_id is None:
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Открыть подписку", callback_data="menu:subscription"),
    ]])
    try:
        async with asyncio.timeout(5):
            await bot.send_message(
                telegram_id, f"✅ Подписка по заказу №{payment_id} активирована.", reply_markup=keyboard,
            )
    except Exception:
        # E.g. customer logged in on the web but has not started the bot.
        log.info("Could not deliver Telegram confirmation for order %d", payment_id)
