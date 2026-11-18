"""Send one renewal reminder per expiration date for an active subscription."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNotFound, TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import exists, select
from sqlalchemy.orm import aliased

from app.db.models import Payment, Subscription, User
from app.db.session import SessionLocal

log = logging.getLogger(__name__)


async def send_renewal_reminders(bot: Bot, days: int) -> None:
    latest = aliased(Subscription)
    now = datetime.now(timezone.utc)
    async with SessionLocal() as session:
        due_ids = (await session.scalars(
            select(Subscription.id)
            .where(
                Subscription.status == "active",
                Subscription.expires_at > now,
                Subscription.expires_at <= now + timedelta(days=days),
                Subscription.reminded_expiry.is_distinct_from(Subscription.expires_at),
                ~exists().where(latest.user_id == Subscription.user_id, latest.id > Subscription.id),
                ~exists().where(Payment.user_id == Subscription.user_id, Payment.status == "paid"),
            ).order_by(Subscription.expires_at).limit(100)
        )).all()

    for subscription_id in due_ids:
        async with SessionLocal.begin() as session:
            subscription = await session.get(Subscription, subscription_id, with_for_update={"skip_locked": True})
            if (subscription is None or subscription.status != "active" or not subscription.expires_at
                    or not now < subscription.expires_at <= now + timedelta(days=days)
                    or subscription.reminded_expiry == subscription.expires_at):
                continue
            user = await session.get(User, subscription.user_id)
            if user is None:
                continue
            expiry = subscription.expires_at.astimezone(timezone.utc).strftime("%d.%m.%Y в %H:%M UTC")
            keyboard = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="Продлить подписку", callback_data="menu:plans"),
            ]])
            try:
                async with asyncio.timeout(5):
                    await bot.send_message(user.telegram_id, f"VPN-подписка действует до {expiry}.", reply_markup=keyboard)
            except (TelegramForbiddenError, TelegramBadRequest, TelegramNotFound):
                # A blocked bot or inaccessible chat cannot receive this message.
                pass
            except TelegramRetryAfter:
                return
            except Exception as exc:
                log.warning("Reminder for subscription %d failed (%s)", subscription_id, type(exc).__name__)
                continue
            subscription.reminded_expiry = subscription.expires_at


async def run_renewal_reminders(bot: Bot, days: int) -> None:
    while True:
        try:
            await send_renewal_reminders(bot, days)
        except Exception as exc:
            log.warning("Renewal reminders unavailable (%s)", type(exc).__name__)
        await asyncio.sleep(300)
