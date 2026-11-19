"""Owner-configured, one-time Remnawave trials.

Lock the customer row while provisioning to prevent concurrent duplicate claims.
A remote user created before a transient database failure can be recovered only
when it carries our exact trial marker.
"""
from datetime import datetime, timedelta, timezone
import httpx
from sqlalchemy import select

from app.services.squads import mode_squads, require_available, fetch_catalog
from app.config import settings
from app.db.models import Payment, Subscription, SubscriptionDetails, TrialClaim, TrialSettings, User
from app.db.session import SessionLocal
from app.services.remnawave import RemnawaveClient
from app.services.subscriptions import _panel_identity, _unwrap

DEFAULT_TRIAL = {"enabled": False, "days": 3, "traffic_gb": 10, "device_limit": 1}


def validate_trial_settings(data: dict) -> dict:
    required = set(DEFAULT_TRIAL)
    if set(data) != required or type(data["enabled"]) is not bool:
        raise ValueError("Invalid trial settings")
    caps = {"days": 30, "traffic_gb": 200, "device_limit": 5}
    for field, maximum in caps.items():
        value = data[field]
        if type(value) is not int or value < 1 or value > maximum:
            raise ValueError(f"{field} must be between 1 and {maximum}")
    return data


async def get_trial_settings() -> dict:
    async with SessionLocal() as session:
        row = await session.get(TrialSettings, 1)
        if row is None:
            return DEFAULT_TRIAL.copy()
        return {"enabled": row.enabled, "days": row.days,
                "traffic_gb": row.traffic_gb, "device_limit": row.device_limit}


async def update_trial_settings(data: dict) -> dict:
    validate_trial_settings(data)
    async with SessionLocal() as session:
        row = await session.get(TrialSettings, 1)
        if row is None:
            row = TrialSettings(id=1)
            session.add(row)
        for field, value in data.items():
            setattr(row, field, value)
        await session.commit()
    return data


async def trial_eligible(telegram_id: int) -> bool:
    if not (await get_trial_settings())["enabled"]:
        return False
    async with SessionLocal() as session:
        user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
        if not user:
            return False
        if await session.scalar(select(TrialClaim.id).where(TrialClaim.user_id == user.id)):
            return False
        if await session.scalar(select(Subscription.id).where(Subscription.user_id == user.id)):
            return False
        if await session.scalar(select(Payment.id).where(
            Payment.user_id == user.id, Payment.status.in_(("paid", "succeeded"))
        )):
            return False
        return True


async def claim_trial(telegram_id: int) -> dict:
    if not settings.remnawave_base_url or not settings.remnawave_api_token:
        raise RuntimeError("Remnawave API is not configured")
    async with SessionLocal() as session:
        user = await session.scalar(
            select(User).where(User.telegram_id == telegram_id).with_for_update()
        )
        if user is None:
            raise ValueError("Register with /start first")
        config = await session.get(TrialSettings, 1)
        if not config or not config.enabled:
            raise ValueError("Trial is disabled")
        if await session.scalar(select(TrialClaim.id).where(TrialClaim.user_id == user.id)):
            raise ValueError("Trial already used")
        if await session.scalar(select(Subscription.id).where(Subscription.user_id == user.id)):
            raise ValueError("Trial is available only before your first subscription")
        if await session.scalar(select(Payment.id).where(
            Payment.user_id == user.id, Payment.status.in_(("paid", "succeeded"))
        )):
            raise ValueError("Trial is available only before your first purchase")

        panel = RemnawaveClient()
        username = f"tg_{telegram_id}"
        marker = f"remnaum-trial:{telegram_id}"
        existing = None
        try:
            existing = _unwrap(await panel.get_user_by_username(username))
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise

        if existing:
            if marker not in str(existing.get("description") or ""):
                raise ValueError("Panel user already exists; refusing to override existing access")
            panel_user_id = _panel_identity(existing)
            panel_user = _unwrap(await panel.get_user(panel_user_id))
        else:
            expire_at = datetime.now(timezone.utc) + timedelta(days=config.days)
            selected = await mode_squads(session, "trial")
            require_available(selected, await fetch_catalog())
            created = _unwrap(await panel.create_user(
                username,
                expire_at,
                telegram_id=telegram_id,
                traffic_limit_bytes=config.traffic_gb * 1024 ** 3,
                hwid_device_limit=config.device_limit,
                active_internal_squads=selected,
                description=marker,
            ))
            if created.get("id") is None and not created.get("uuid"):
                created = _unwrap(await panel.get_user_by_username(username))
            panel_user_id = _panel_identity(created)
            panel_user = _unwrap(await panel.get_user(panel_user_id))

        expiry_raw = panel_user.get("expireAt")
        expiry = datetime.fromisoformat(expiry_raw.replace("Z", "+00:00")) if isinstance(expiry_raw, str) else (
            datetime.now(timezone.utc) + timedelta(days=config.days)
        )
        subscription = Subscription(
            user_id=user.id, panel_user_id=str(panel_user_id),
            status="active", expires_at=expiry,
        )
        session.add(subscription)
        await session.flush()
        session.add(SubscriptionDetails(
            subscription_id=subscription.id,
            subscription_url=panel_user.get("subscriptionUrl"),
            traffic_gb=config.traffic_gb,
            device_limit=config.device_limit,
        ))
        session.add(TrialClaim(
            user_id=user.id, panel_user_id=str(panel_user_id), status="completed",
        ))
        await session.commit()
        return {"days": config.days, "traffic_gb": config.traffic_gb,
                "device_limit": config.device_limit, "subscription_url": panel_user.get("subscriptionUrl")}
