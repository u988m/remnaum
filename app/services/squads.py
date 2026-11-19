"""Persistent Remnawave discovery catalogue. Only the owner changes plan membership."""
from datetime import datetime, timezone
from uuid import UUID

import httpx
from sqlalchemy.dialects.postgresql import insert

from app.db.models import InternalSquadCatalog, SquadPolicy
from app.db.session import SessionLocal
from app.services.remnawave import RemnawaveClient, panel_client


def parse_catalog(payload):
    if isinstance(payload, dict):
        payload = payload.get("response", payload)
    if isinstance(payload, dict):
        rows = payload.get("internalSquads")
    else:
        rows = payload
    if not isinstance(rows, list):
        raise ValueError("Invalid squad list")
    items = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str):
            raise ValueError("Invalid squad")
        identifier = str(UUID(str(row.get("uuid"))))
        items[identifier] = {"uuid": identifier, "name": row["name"], "available": row.get("isActive", True) is True and row.get("isEnabled", True) is True and not row.get("isDeleted", False) and row.get("status", "ACTIVE") == "ACTIVE"}
    return items


async def fetch_catalog():
    client = await panel_client(RemnawaveClient)
    items = {}
    params = {}
    while True:
        payload = await client.request("GET", "/api/internal-squads", params=params)
        page = parse_catalog(payload)
        body = payload.get("response", payload) if isinstance(payload, dict) else payload
        total = body.get("total", len(page)) if isinstance(body, dict) else len(page)
        if not isinstance(total, int) or items.keys() & page.keys():
            raise ValueError("Invalid squad pagination")
        items.update(page)
        if len(items) == total:
            return items
        if not page or len(items) > total:
            raise ValueError("Incomplete squad list")
        params = {"start": len(items), "size": len(page)}


def require_available(selected, items):
    if not selected or any(not items.get(key, {}).get("available") for key in selected):
        raise ValueError("Сквады доступа не настроены или недоступны")


async def mode_squads(session, mode):
    policy = await session.get(SquadPolicy, mode)
    return policy.squads if policy else []


def catalog_view(row):
    return {
        "items": sorted(row.items.values(), key=lambda item: item["name"].casefold()) if row else [],
        "last_success": row.last_success if row else None,
        "failed": row.failed if row else False,
    }


async def get_catalog():
    async with SessionLocal() as session:
        return catalog_view(await session.get(InternalSquadCatalog, 1))


async def refresh_catalog():
    async with SessionLocal.begin() as session:
        await session.execute(insert(InternalSquadCatalog).values(id=1).on_conflict_do_nothing())
        row = await session.get(InternalSquadCatalog, 1, with_for_update=True)
        try:
            items = await fetch_catalog()
        except (httpx.HTTPError, RuntimeError, ValueError):
            row.failed = True
        else:
            missing = {key: dict(item, available=False) for key, item in row.items.items() if key not in items}
            row.items = missing | items
            row.last_success = datetime.now(timezone.utc)
            row.failed = False
        return catalog_view(row)
