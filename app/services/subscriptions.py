import json
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import select

from app.services.squads import fetch_catalog, require_available
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


async def get_panel_user(panel, telegram_id):
    try:
        return _unwrap(await panel.get_user_by_username(f"tg_{telegram_id}"))
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 404:
            raise
        return {}


def matches_panel_account(panel_user_id: int | str, panel_user):
    return any(panel_user.get(key) is not None and str(panel_user[key]) == str(panel_user_id)
               for key in ("id", "uuid"))


def has_panel_squads(panel_user):
    assignments = panel_user.get("activeInternalSquads")
    return isinstance(assignments, list) and bool(assignments)


def panel_expiry(panel_user):
    value = panel_user.get("expireAt")
    if not isinstance(value, str):
        return None
    expiry = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return expiry if expiry.tzinfo else expiry.replace(tzinfo=timezone.utc)


async def paid_tariff_key(session, subscription):
    if subscription.paid_tariff is not None:
        return subscription.paid_tariff
    previous = await session.scalar(select(Payment).where(
        Payment.user_id == subscription.user_id, Payment.status == "succeeded"
    ).order_by(Payment.id.desc()).limit(1))
    order = json.loads(previous.payload or "{}") if previous else {}
    return str(order["plan_id"] or "flexible") if "plan_id" in order else None


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
        order = json.loads(payment.payload or "{}")
        tariff_key = str(order["plan_id"] or "flexible") if "plan_id" in order else None
        snapshot = order.get("tariff") or {}
        days = int(snapshot.get("duration_days", 0))
        traffic_gb = int(snapshot.get("traffic_gb", -1))
        device_limit = int(snapshot.get("device_limit", 0))
        if min(days, device_limit) < 1 or traffic_gb < 0:
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
            .order_by(Subscription.created_at.desc(), Subscription.id.desc())
            .limit(1)
            .with_for_update()
        )
        username = f"tg_{user.telegram_id}"
        traffic_bytes = traffic_gb * 1024 * 1024 * 1024
        panel = RemnawaveClient()

        panel_user = await get_panel_user(panel, user.telegram_id)
        if subscription is not None and not matches_panel_account(subscription.panel_user_id, panel_user):
            raise RuntimeError("Panel account missing or replaced for existing subscription")

        marker = f"remnaum-payment:{payment_id}"
        if panel_user:
            panel_user_id = _panel_identity(panel_user)
            description = str(panel_user.get("description") or "")
            markers = {part.strip() for part in description.split("|")}
            # Reconcile a previous panel write before using the local tariff identity.
            if marker not in markers:
                pending_ids = await session.scalars(select(Payment.id).where(
                    Payment.user_id == user.id, Payment.status == "paid", Payment.id != payment_id
                ))
                if any(f"remnaum-payment:{other}" in markers for other in pending_ids):
                    raise RuntimeError("Retry the previously applied payment first")
            if marker not in markers:
                current_key = await paid_tariff_key(session, subscription) if subscription else None
                same_tariff = tariff_key is not None and tariff_key == current_key
                # Older orders have no checkout decision. Preserve only known renewals
                # or legacy orders without tariff identity against a paid subscription.
                if "preserve_squads" in snapshot:
                    preserve = snapshot["preserve_squads"] and same_tariff
                else:
                    preserve = same_tariff or (
                        tariff_key is None and subscription is not None
                        and not await session.scalar(select(TrialClaim.id).where(
                            TrialClaim.user_id == user.id, TrialClaim.status == "completed"
                        ))
                    )
                selected = None
                if preserve and not has_panel_squads(panel_user):
                    raise RuntimeError("Existing panel account has no Internal Squads to preserve")
                if not preserve:
                    selected = snapshot.get("internal_squads", settings.remnawave_internal_squad_ids)
                    require_available(selected, await fetch_catalog())
                now = datetime.now(timezone.utc)
                new_expiry = max(panel_expiry(panel_user) or now, now) + timedelta(days=total_days)
                description_parts = [part.strip() for part in description.split("|")]
                new_description = " | ".join([
                    part for part in description_parts
                    if part and not (part.startswith("remnaum-payment:") and part.removeprefix("remnaum-payment:").isdigit())
                ] + [marker])
                await panel.update_user(
                    panel_user_id,
                    traffic_limit_bytes=traffic_bytes,
                    hwid_device_limit=device_limit,
                    expire_at=new_expiry,
                    description=new_description,
                    active_internal_squads=selected,
                )
            panel_user = _unwrap(await panel.get_user(panel_user_id))
        else:
            expire_at = datetime.now(timezone.utc) + timedelta(days=total_days)
            squads = snapshot.get("internal_squads", settings.remnawave_internal_squad_ids)
            if not squads:
                raise RuntimeError("No Internal Squads configured for VPN issuance")
            require_available(squads, await fetch_catalog())
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

        if not matches_panel_account(panel_user_id, panel_user):
            raise RuntimeError("Could not confirm the updated Remnawave account")
        expires_at = panel_expiry(panel_user) or datetime.now(timezone.utc) + timedelta(days=total_days)
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

        if tariff_key:
            subscription.paid_tariff = tariff_key

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
    async with SessionLocal() as session:
        result = await session.execute(
            select(Subscription, SubscriptionDetails)
            .join(User, Subscription.user_id == User.id)
            .outerjoin(SubscriptionDetails, SubscriptionDetails.subscription_id == Subscription.id)
            .where(User.telegram_id == telegram_id)
            .order_by(Subscription.created_at.desc(), Subscription.id.desc())
            .limit(1)
        )
        row = result.first()
        if row is None:
            return None
        subscription, details = row
        tariff_key = subscription.paid_tariff
        return {
            "plan_id": int(tariff_key) if tariff_key and tariff_key.isdigit() else None,
            "status": subscription.status,
            "panel_user_id": subscription.panel_user_id,
            "expires_at": subscription.expires_at,
            "subscription_url": details.subscription_url if details else None,
            "traffic_gb": details.traffic_gb if details else None,
            "device_limit": details.device_limit if details else None,
        }
