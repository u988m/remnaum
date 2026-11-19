import json
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import select

from app.config import settings
from app.db.models import Payment, Subscription, SubscriptionDetails, TrialClaim, User
from app.db.session import SessionLocal
from app.services.remnawave import RemnawaveClient
from app.services.referrals import pending_rewards_for_payment, grant_referral_reward
from app.services.promos import pending_promo_redemptions


def _unwrap(data: Any) -> dict[str, Any]:
    if isinstance(data, dict):
        value = data.get("response", data)
        if isinstance(value, dict):
            return value
    return {}


def _panel_identity(user: dict[str, Any]) -> int | str:
    value = user.get("id") if user.get("id") is not None else user.get("uuid")
    if value is None or value == "":
        raise RuntimeError("Remnawave user has no id/uuid")
    return int(value) if str(value).isdigit() else str(value)


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

        user = await session.get(User, payment.user_id, with_for_update=True)
        if user is None:
            raise RuntimeError("Payment owner not found")
        snapshot = json.loads(payment.payload or "{}").get("tariff") or {}
        days = int(snapshot.get("duration_days", 0))
        traffic_gb = int(snapshot.get("traffic_gb", 0))
        device_limit = int(snapshot.get("device_limit", 0))
        if min(days, traffic_gb, device_limit) < 1:
            raise RuntimeError("Payment does not contain a valid tariff snapshot")
        pending_rewards = await pending_rewards_for_payment(session, user.id)
        promo_credits = await pending_promo_redemptions(session, user.id)
        bonus_days = sum(reward.days for reward in pending_rewards) + sum(p.bonus_days for p in promo_credits)
        total_days = days + bonus_days
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

        marker = f"remnaum-payment:{payment_id}"
        if panel_user:
            panel_user_id = _panel_identity(panel_user)
            description = str(panel_user.get("description") or "")
            markers = {part.strip() for part in description.split("|")}
            if marker not in markers:
                first_purchase = subscription is None
                if subscription is not None and await session.scalar(
                    select(TrialClaim.id).where(TrialClaim.user_id == user.id, TrialClaim.status == "completed")
                ):
                    previous_payment = await session.scalar(select(Payment.id).where(
                        Payment.user_id == user.id, Payment.status == "succeeded"
                    ).limit(1))
                    first_purchase = previous_payment is None
                expire_value = panel_user.get("expireAt")
                if isinstance(expire_value, str):
                    current_expiry = datetime.fromisoformat(expire_value.replace("Z", "+00:00"))
                    if current_expiry.tzinfo is None:
                        current_expiry = current_expiry.replace(tzinfo=timezone.utc)
                else:
                    current_expiry = datetime.now(timezone.utc)
                new_expiry = max(current_expiry, datetime.now(timezone.utc)) + timedelta(days=total_days)
                new_description = f"{description} | {marker}".strip(" |")
                await panel.update_user(
                    panel_user_id,
                    traffic_limit_bytes=traffic_bytes,
                    hwid_device_limit=device_limit,
                    expire_at=new_expiry,
                    description=new_description,
                    active_internal_squads=snapshot.get("internal_squads") if first_purchase else None,
                )
            else:
                await panel.update_user(
                    panel_user_id,
                    traffic_limit_bytes=traffic_bytes,
                    hwid_device_limit=device_limit,
                    description=description,
                )
            panel_user = _unwrap(await panel.get_user(panel_user_id))
        else:
            if subscription is not None:
                raise RuntimeError("Panel account missing for existing subscription; check before retrying renewal")
            expire_at = datetime.now(timezone.utc) + timedelta(days=total_days)
            squads = snapshot.get("internal_squads", settings.remnawave_internal_squad_ids)
            if not squads:
                raise RuntimeError("No Internal Squads configured for VPN issuance")
            created = await panel.create_user(
                username,
                expire_at,
                telegram_id=user.telegram_id,
                traffic_limit_bytes=traffic_bytes,
                traffic_limit_strategy="NO_RESET",
                hwid_device_limit=device_limit,
                active_internal_squads=squads,
                description=marker,
            )
            panel_user = _unwrap(created)
            if panel_user.get("id") is None and not panel_user.get("uuid"):
                panel_user = _unwrap(await panel.get_user_by_username(username))
            panel_user_id = _panel_identity(panel_user)
            panel_user = _unwrap(await panel.get_user(panel_user_id))

        expire_value = panel_user.get("expireAt")
        expires_at = (
            datetime.fromisoformat(expire_value.replace("Z", "+00:00"))
            if isinstance(expire_value, str)
            else datetime.now(timezone.utc) + timedelta(days=total_days)
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

        for reward in pending_rewards:
            reward.redeemed_payment_id = payment_id
        for credit in promo_credits:
            credit.applied_payment_id = payment_id
        await grant_referral_reward(session, user.id, payment_id)
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
            "panel_user_id": subscription.panel_user_id,
            "expires_at": subscription.expires_at,
            "subscription_url": details.subscription_url if details else None,
            "traffic_gb": details.traffic_gb if details else None,
            "device_limit": details.device_limit if details else None,
        }
