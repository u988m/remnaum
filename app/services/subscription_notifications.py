"""Send Telegram reminders and expiry notices for current subscriptions."""
import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import aliased

from app.db.models import (
    Payment, Subscription, SubscriptionDetails, SubscriptionNotification, TrialClaim, User, UserNotificationPreferences,
)
from app.db.session import SessionLocal
from app.services.notification_settings import NotificationPreferences, NotificationSettings, get_notification_settings
from app.services.remnawave import RemnawaveClient, panel_client
from app.services.subscriptions import _unwrap, panel_expiry

log = logging.getLogger(__name__)
SCAN_INTERVAL = 300
EXPIRY_GRACE = timedelta(days=1)
PANEL_PAGE_SIZE = 100


@dataclass
class Recipient:
    subscription: Subscription
    telegram_id: int
    is_trial: bool
    subscription_url: str | None
    preferences: NotificationPreferences


@dataclass(frozen=True)
class Notice:
    kind: str
    period: str
    text: str
    deadline: datetime


async def _recipients(now: datetime) -> list[Recipient]:
    newest = aliased(Subscription)
    latest_id = (
        select(newest.id).where(newest.user_id == Subscription.user_id)
        .order_by(newest.created_at.desc(), newest.id.desc()).limit(1).scalar_subquery()
    )
    has_paid_after_trial = select(Payment.id).where(
        Payment.user_id == User.id, Payment.status == "succeeded", Payment.created_at >= TrialClaim.created_at,
    ).exists()
    async with SessionLocal() as session:
        rows = await session.execute(
            select(Subscription, User.telegram_id, TrialClaim.id.is_not(None) & ~has_paid_after_trial,
                   SubscriptionDetails.subscription_url,
                   UserNotificationPreferences.data)
            .join(User, User.id == Subscription.user_id)
            .outerjoin(TrialClaim, (TrialClaim.user_id == User.id)
                       & (TrialClaim.panel_user_id == Subscription.panel_user_id) & (TrialClaim.status == "completed"))
            .outerjoin(SubscriptionDetails, SubscriptionDetails.subscription_id == Subscription.id)
            .outerjoin(UserNotificationPreferences, UserNotificationPreferences.user_id == User.id)
            .where(Subscription.id == latest_id, User.blocked_at.is_(None),
                   Subscription.status.in_(("active", "expired")), Subscription.expires_at > now - EXPIRY_GRACE)
        )
        return [
            Recipient(subscription, telegram_id, is_trial and subscription.paid_tariff is None,
                      url, NotificationPreferences.model_validate(data or {}))
            for subscription, telegram_id, is_trial, url, data in rows
        ]


def _time_notice(recipient: Recipient, rules: NotificationSettings, now: datetime) -> Notice | None:
    expiry = recipient.subscription.expires_at
    category = "trial" if recipient.is_trial else "subscription"
    rule = getattr(rules, category)
    remaining = expiry - now
    if -EXPIRY_GRACE < remaining <= timedelta(0):
        phase, message, deadline = "expired", rule.expired, expiry + EXPIRY_GRACE
    elif (recipient.subscription.status == "active" and
          timedelta(0) < remaining <= timedelta(hours=rule.lead)):
        phase, message, deadline = "reminder", rule.reminder, expiry
    else:
        return None
    kind = f"{category}_{phase}"
    if message.enabled and getattr(recipient.preferences, kind):
        return Notice(kind, expiry.astimezone(timezone.utc).isoformat(), message.text, deadline)
    return None


def _traffic_notice(recipient: Recipient, account: dict, rules: NotificationSettings, now: datetime) -> Notice | None:
    status = account.get("status")
    if status not in ("ACTIVE", "LIMITED"):
        return None
    try:
        expiry = panel_expiry(account)
    except ValueError:
        return None
    deadline = min(expiry, recipient.subscription.expires_at) if expiry else recipient.subscription.expires_at
    limit = account.get("trafficLimitBytes")
    if deadline <= now or type(limit) is not int or limit <= 0:
        return None
    traffic = account.get("userTraffic")
    used = traffic.get("usedTrafficBytes") if isinstance(traffic, dict) else account.get("usedTrafficBytes")
    if status == "LIMITED" or (type(used) is int and used >= limit):
        phase, message = "expired", rules.traffic.expired
    elif type(used) is int and 0 <= used < limit and (limit - used) * 100 <= limit * rules.traffic.lead:
        phase, message = "reminder", rules.traffic.reminder
    else:
        return None
    kind = f"traffic_{phase}"
    if message.enabled and getattr(recipient.preferences, kind):
        period = json.dumps([account.get("shortUuid") or recipient.subscription.panel_user_id,
                             limit, account.get("lastTrafficResetAt")])
        return Notice(kind, sha256(period.encode()).hexdigest(), message.text, deadline)
    return None


async def _deliver(bot: Bot, recipient: Recipient, notice: Notice) -> None:
    """Keep successful sends across restarts and retry temporary delivery failures."""
    snapshot = recipient.subscription
    key = (snapshot.id, notice.kind, notice.period)
    try:
        async with SessionLocal.begin() as session:
            if await session.get(SubscriptionNotification, key) is not None:
                return
            user = await session.get(User, snapshot.user_id, with_for_update=True)
            if user is None or user.blocked_at is not None or user.telegram_id != recipient.telegram_id:
                return
            prefs = await session.get(UserNotificationPreferences, user.id)
            if prefs and not getattr(NotificationPreferences.model_validate(prefs.data), notice.kind):
                return
            subscription = await session.scalar(
                select(Subscription).where(Subscription.user_id == user.id)
                .order_by(Subscription.created_at.desc(), Subscription.id.desc()).limit(1).with_for_update()
            )
            if subscription is None or (
                subscription.id, subscription.expires_at, subscription.status,
                subscription.panel_user_id, subscription.paid_tariff,
            ) != (snapshot.id, snapshot.expires_at, snapshot.status, snapshot.panel_user_id, snapshot.paid_tariff):
                return
            if datetime.now(timezone.utc) >= notice.deadline:
                return
            inserted = await session.scalar(
                insert(SubscriptionNotification).values(subscription_id=key[0], kind=key[1], period=key[2])
                .on_conflict_do_nothing().returning(SubscriptionNotification.subscription_id)
            )
            if inserted is None:
                return
            try:
                await asyncio.wait_for(bot.send_message(user.telegram_id, notice.text, parse_mode=None), timeout=10)
            except (TelegramForbiddenError, TelegramBadRequest):
                # This chat cannot receive this notice. Avoid retrying it every scan.
                log.info("Telegram notification %s could not be delivered for subscription %s", notice.kind, snapshot.id)
    except TelegramRetryAfter as exc:
        log.info("Telegram asked subscription notifications to retry in %s seconds", exc.retry_after)
        await asyncio.sleep(exc.retry_after)
        return
    except Exception:
        log.exception("Subscription notification %s failed for subscription %s", notice.kind, snapshot.id)
        return
    await asyncio.sleep(0.05)


async def _scan_traffic(bot: Bot, panel: RemnawaveClient, recipients: list[Recipient], rules: NotificationSettings) -> None:
    by_id: dict[str, list[Recipient]] = {}
    legacy_by_url: dict[str, list[Recipient]] = {}
    now = datetime.now(timezone.utc)
    for recipient in recipients:
        subscription = recipient.subscription
        panel_id = subscription.panel_user_id
        if (subscription.status != "active" or subscription.expires_at <= now or not panel_id or
            not ((rules.traffic.reminder.enabled and recipient.preferences.traffic_reminder)
                 or (rules.traffic.expired.enabled and recipient.preferences.traffic_expired))):
            continue
        by_id.setdefault(panel_id, []).append(recipient)
        if not panel_id.isdigit() and recipient.subscription_url:
            legacy_by_url.setdefault(recipient.subscription_url, []).append(recipient)
    if not by_id:
        return

    start = 0
    while True:
        batch = _unwrap(await panel.list_users_page(start=start, size=PANEL_PAGE_SIZE))
        accounts, total = batch.get("users"), batch.get("total")
        if not isinstance(accounts, list) or type(total) is not int or (not accounts and start < total):
            raise ValueError("Invalid Remnawave user list")
        for account in accounts:
            if not isinstance(account, dict):
                continue
            # Panel 3.x can return a numeric ID for a locally stored 2.x UUID.
            candidates = {r.subscription.id: r for field in ("id", "uuid") for r in by_id.get(str(account.get(field)), [])}
            matches = list(candidates.values())
            url = account.get("subscriptionUrl")
            if not matches and account.get("uuid") is None and isinstance(url, str):
                matches = legacy_by_url.get(url, [])
                matches = [r for r in matches if account.get("telegramId") in (r.telegram_id, str(r.telegram_id))]
            matches = [r for r in matches if account.get("telegramId") in (None, r.telegram_id, str(r.telegram_id))]
            if len(matches) != 1:
                continue
            recipient = matches[0]
            if notice := _traffic_notice(recipient, account, rules, datetime.now(timezone.utc)):
                await _deliver(bot, recipient, notice)
        start += len(accounts)
        if start >= total:
            return


async def scan_subscription_notifications(bot: Bot, panel: RemnawaveClient) -> None:
    now = datetime.now(timezone.utc)
    rules = await get_notification_settings()
    recipients = await _recipients(now)
    for recipient in recipients:
        if notice := _time_notice(recipient, rules, now):
            await _deliver(bot, recipient, notice)
    if panel.base_url and panel.token:
        await _scan_traffic(bot, panel, recipients, rules)


async def run_subscription_notifications(bot: Bot) -> None:
    while True:
        try:
            panel = await panel_client(RemnawaveClient)
            await scan_subscription_notifications(bot, panel)
        except Exception:
            log.exception("Subscription notification scan failed")
        await asyncio.sleep(SCAN_INTERVAL)
