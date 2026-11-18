import json
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import select

from app.config import settings
from app.db.models import Payment, Subscription, SubscriptionDetails, User
from app.db.session import SessionLocal
from app.services.remnawave import RemnawaveClient


def _unwrap(data: Any) -> dict[str, Any]:
    if isinstance(data, dict):
        value = data.get("response", data)
        if isinstance(value, dict):
            return value
    return {}


async def provision_paid_payment(payment_id: int) -> bool:
    async with SessionLocal() as session:
        payment = await session.scalar(
            select(Payment).where(Payment.id == payment_id).with_for_update()
        )
        if payment is None:
            raise ValueError("Payment not found")
        if payment.status == "succeeded":
            return False
        if payment.status != "paid":
            raise ValueError("Payment has not been verified as paid")

        user = await session.get(User, payment.user_id)
        if user is None:
            raise RuntimeError("Payment owner not found")
        snapshot = json.loads(payment.payload or "{}").get("tariff") or {}
        days = int(snapshot.get("duration_days", 0))
        traffic_gb = int(snapshot.get("traffic_gb", 0))
        device_limit = int(snapshot.get("device_limit", 0))
        if min(days, traffic_gb, device_limit) < 1:
            raise RuntimeError("Payment does not contain a valid tariff snapshot")
        if not settings.remnawave_base_url or not settings.remnawave_api_token:
            raise RuntimeError("Remnawave is not configured")

        subscription = await session.scalar(
            select(Subscription)
            .where(Subscription.user_id == user.id)
            .order_by(Subscription.created_at.desc())
            .with_for_update()
        )
        username = f"tg_{user.telegram_id}"
        traffic_bytes = traffic_gb * 1024 * 1024 * 1024
        panel = RemnawaveClient()

        panel_user: dict[str, Any] | None = None
        try:
            panel_user = _unwrap(await panel.get_user_by_username(username))
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise

        if panel_user and subscription and subscription.panel_user_id:
            panel_user_id = int(subscription.panel_user_id)
            await panel.extend_user(panel_user_id, days)
            await panel.update_user(
                panel_user_id,
                traffic_limit_bytes=traffic_bytes,
                hwid_device_limit=device_limit,
            )
            panel_user = _unwrap(await panel.get_user(panel_user_id))
        elif panel_user:
            panel_user_id = int(panel_user["id"])
            await panel.update_user(
                panel_user_id,
                traffic_limit_bytes=traffic_bytes,
                hwid_device_limit=device_limit,
            )
            panel_user = _unwrap(await panel.get_user(panel_user_id))
        else:
            expire_at = datetime.now(timezone.utc) + timedelta(days=days)
            created = await panel.create_user(
                username,
                expire_at,
                telegram_id=user.telegram_id,
                traffic_limit_bytes=traffic_bytes,
                traffic_limit_strategy="NO_RESET",
                hwid_device_limit=device_limit,
                active_internal_squads=settings.remnawave_internal_squad_ids or None,
                description=f"Remnaum Telegram user {user.telegram_id}",
            )
            panel_user = _unwrap(created)
            if not panel_user.get("id"):
                panel_user = _unwrap(await panel.get_user_by_username(username))
            panel_user_id = int(panel_user["id"])
            panel_user = _unwrap(await panel.get_user(panel_user_id))

        expire_value = panel_user.get("expireAt")
        expires_at = (
            datetime.fromisoformat(expire_value.replace("Z", "+00:00"))
            if isinstance(expire_value, str)
            else datetime.now(timezone.utc) + timedelta(days=days)
        )
        subscription_url = panel_user.get("subscriptionUrl")

        if subscription is None:
            subscription = Subscription(
                user_id=user.id,
                panel_user_id=str(panel_user_id),
                status="active",
                expires_at=expires_at,
            )
            session.add(subscription)
            await session.flush()
        else:
            subscription.panel_user_id = str(panel_user_id)
            subscription.status = "active"
            subscription.expires_at = expires_at

        details = await session.scalar(
            select(SubscriptionDetails).where(
                SubscriptionDetails.subscription_id == subscription.id
            )
        )
        if details is None:
            details = SubscriptionDetails(
                subscription_id=subscription.id,
                subscription_url=subscription_url,
                traffic_gb=traffic_gb,
                device_limit=device_limit,
            )
            session.add(details)
        else:
            details.subscription_url = subscription_url
            details.traffic_gb = traffic_gb
            details.device_limit = device_limit

        payment.status = "succeeded"
        await session.commit()
        return True


async def get_user_subscription(telegram_id: int) -> dict[str, Any] | None:
    from sqlalchemy import outerjoin

    async with SessionLocal() as session:
        result = await session.execute(
            select(Subscription, SubscriptionDetails)
            .join(User, Subscription.user_id == User.id)
            .outerjoin(SubscriptionDetails, SubscriptionDetails.subscription_id == Subscription.id)
            .where(User.telegram_id == telegram_id)
            .order_by(Subscription.created_at.desc())
            .limit(1)
        )
        row = result.first()
        if row is None:
            return None
        subscription, details = row
        return {
            "status": subscription.status,
            "expires_at": subscription.expires_at,
            "subscription_url": details.subscription_url if details else None,
            "traffic_gb": details.traffic_gb if details else None,
            "device_limit": details.device_limit if details else None,
        }
