"""Manage hardware registrations scoped to a customer's Remnawave account."""
import hmac
import re

import httpx
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from app.config import settings
from app.db.models import DeviceAlias, Subscription, SubscriptionDetails, User
from app.db.session import SessionLocal
from app.services.remnawave import RemnawaveClient
from app.services.subscriptions import PanelAccountConflict, _panel_identity, resolve_panel_user
from app.services.users import BlockedCustomer

CLIENTS = {
    "happ": "Happ", "incy": "Incy", "v2rayng": "v2rayNG", "v2rayn": "v2rayN",
    "shadowrocket": "Shadowrocket", "hiddify": "Hiddify", "streisand": "Streisand",
    "nekobox": "NekoBox", "nekoray": "Nekoray", "mihomo": "Mihomo", "sing-box": "sing-box",
    "clash": "Clash", "v2box": "V2Box", "karing": "Karing", "foxray": "FoXray", "flclash": "FlClash",
}
CLIENT_PATTERN = re.compile(r"\b(" + "|".join(CLIENTS) + r")\b", re.IGNORECASE)


class DeviceNotFound(LookupError):
    def __init__(self):
        super().__init__("Устройство больше не зарегистрировано.")


class DevicesUnavailable(RuntimeError):
    def __init__(self):
        super().__init__("Не удалось связаться с Remnawave. Попробуйте позже.")


def _device_id(user_id: int, hwid: str) -> str:
    return hmac.digest(settings.app_secret_key.encode(), f"device:{user_id}:{hwid}".encode(), "sha256").hex()[:32]


def _field(value, length: int) -> str:
    return " ".join(value.split())[:length] if isinstance(value, str) else ""


def _public_device(device_id: str, record: dict, alias: str = "") -> dict:
    platform = _field(record.get("platform"), 40)
    model = _field(record.get("deviceModel"), 100) or platform or "Устройство"
    match = CLIENT_PATTERN.search(_field(record.get("userAgent"), 300))
    return {"id": device_id, "name": alias or model, "alias": alias, "model": model, "platform": platform,
            "os_version": _field(record.get("osVersion"), 40), "client": CLIENTS[match[1].lower()] if match else None}


async def _customer_subscription(session, telegram_id: int, *, lock: bool = False):
    query = select(User).where(User.telegram_id == telegram_id)
    user = await session.scalar(query.with_for_update() if lock else query)
    if not user:
        return None, None, None
    if user.blocked_at is not None:
        raise BlockedCustomer()
    result = await session.execute(
        select(Subscription, SubscriptionDetails.device_limit)
        .outerjoin(SubscriptionDetails, SubscriptionDetails.subscription_id == Subscription.id)
        .where(Subscription.user_id == user.id)
        .order_by(Subscription.created_at.desc(), Subscription.id.desc()).limit(1)
    )
    subscription, limit = result.first() or (None, None)
    return user, subscription, limit


async def _panel_devices(panel: RemnawaveClient, user: User, subscription: Subscription):
    try:
        account = await resolve_panel_user(panel, user.telegram_id, subscription)
        if not account:
            return None, {}
        panel_id = _panel_identity(account)
        response = await panel.get_hwid_devices(panel_id)
    except PanelAccountConflict:
        raise
    except (RuntimeError, ValueError, httpx.HTTPError) as exc:
        raise DevicesUnavailable() from exc
    data = response.get("response", response) if isinstance(response, dict) else response
    records = data.get("devices", data.get("items")) if isinstance(data, dict) else data
    if not isinstance(records, list):
        raise DevicesUnavailable()
    return panel_id, {
        _device_id(user.id, record["hwid"]): record for record in records
        if isinstance(record, dict) and isinstance(record.get("hwid"), str) and record["hwid"]
    }


async def get_customer_devices(telegram_id: int) -> dict:
    async with SessionLocal() as session:
        user, subscription, limit = await _customer_subscription(session, telegram_id)
        if not subscription or not subscription.panel_user_id:
            return {"has_subscription": bool(subscription), "limit": limit, "total": 0, "items": []}
        _, records = await _panel_devices(RemnawaveClient(), user, subscription)
        aliases = dict((await session.execute(select(DeviceAlias.device_id, DeviceAlias.name)
                        .where(DeviceAlias.user_id == user.id))).all())
    items = [_public_device(key, record, aliases.get(key, "")) for key, record in records.items()]
    return {"has_subscription": True, "limit": limit, "total": len(items), "items": items}


async def change_customer_device(telegram_id: int, device_id: str, *, name: str | None) -> dict | None:
    """An empty name restores the default. A missing name removes the device."""
    if name is not None:
        name = " ".join(name.split())
        if "\x00" in name:
            raise ValueError("Имя содержит недопустимый символ.")
        if len(name) > 48:
            raise ValueError("Название должно содержать не более 48 символов.")
    async with SessionLocal.begin() as session:
        user, subscription, _ = await _customer_subscription(session, telegram_id, lock=True)
        if not subscription or not subscription.panel_user_id:
            raise DeviceNotFound()
        panel = RemnawaveClient()
        panel_id, records = await _panel_devices(panel, user, subscription)
        record = records.get(device_id)
        if not record:
            raise DeviceNotFound()
        if name is None:
            try:
                await panel.delete_hwid_device(panel_id, record["hwid"])
            except (RuntimeError, ValueError, httpx.HTTPError) as exc:
                raise DevicesUnavailable() from exc
        if name:
            await session.execute(insert(DeviceAlias).values(user_id=user.id, device_id=device_id, name=name)
                                  .on_conflict_do_update(index_elements=[DeviceAlias.user_id, DeviceAlias.device_id],
                                                         set_={"name": name}))
        else:
            await session.execute(delete(DeviceAlias).where(DeviceAlias.user_id == user.id, DeviceAlias.device_id == device_id))
        return None if name is None else _public_device(device_id, record, name)
