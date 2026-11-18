"""Deduplicate signed Remnawave deliveries and reconcile known VPN subscriptions.

Only a small, explicit set of subscription-state events can change local status.
Unknown users and unrelated Remnawave events are recorded without side effects.
Payloads and credentials are not persisted.
"""
import hashlib
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from app.db.models import RemnawaveEvent, Subscription
from app.db.session import SessionLocal

STATUS_EVENTS = {
    "user.disabled": "disabled",
    "user.revoked": "disabled",
    "user.expired": "expired",
    "user.deleted": "deleted",
    "user.enabled": "active",
}


def panel_subscription_id(event: dict) -> str | None:
    data = event.get("data")
    if not isinstance(data, dict):
        return None
    # Only identifiers, not a telegram_id, may select a panel subscription.
    value = data.get("uuid") or data.get("id")
    return str(value) if value is not None and value != "" else None


async def record_and_reconcile(body: bytes, event: dict) -> bool:
    digest = hashlib.sha256(body).hexdigest()
    kind = str(event["event"])
    scope = str(event.get("scope") or "")[:48]
    if len(kind) > 128:
        raise ValueError("Event name is too long")
    async with SessionLocal() as session:
        stmt = insert(RemnawaveEvent).values(
            body_digest=digest, event=kind, scope=scope, status="received",
        ).on_conflict_do_nothing(index_elements=[RemnawaveEvent.body_digest]).returning(RemnawaveEvent.id)
        event_id = await session.scalar(stmt)
        if event_id is None:
            return False
        status = STATUS_EVENTS.get(kind) if scope in ("", "user") else None
        panel_id = panel_subscription_id(event) if status else None
        if panel_id:
            subscription = await session.scalar(
                select(Subscription)
                .where(Subscription.panel_user_id == panel_id)
                .order_by(Subscription.created_at.desc())
                .with_for_update()
            )
            if subscription is not None and subscription.status != "pending":
                subscription.status = status
                record = await session.get(RemnawaveEvent, event_id)
                record.status = "reconciled"
        await session.commit()
        return True
