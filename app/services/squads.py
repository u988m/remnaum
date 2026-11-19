"""Persistent Remnawave discovery catalogue. Only the owner changes plan membership."""
from datetime import datetime, timezone
from uuid import UUID

import httpx
from sqlalchemy.dialects.postgresql import insert

from app.db.models import InternalSquadCatalog
from app.db.session import SessionLocal
from app.services.remnawave import RemnawaveClient


def parse_catalog(payload):
    if isinstance(payload, dict):
        payload = payload.get("response", payload)
    if isinstance(payload, dict):
        rows = payload.get("internalSquads")
        if isinstance(rows, list) and payload.get("total", len(rows)) != len(rows):
            raise ValueError("Incomplete squad list")
    else:
        rows = payload
    if not isinstance(rows, list):
        raise ValueError("Invalid squad list")
    items = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str):
            raise ValueError("Invalid squad")
        identifier = str(UUID(str(row.get("uuid"))))
        items[identifier] = {"uuid": identifier, "name": row["name"], "available": True}
    return items


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
            items = parse_catalog(await RemnawaveClient().request("GET", "/api/internal-squads"))
        except (httpx.HTTPError, RuntimeError, ValueError):
            row.failed = True
        else:
            missing = {key: dict(item, available=False) for key, item in row.items.items() if key not in items}
            row.items = missing | items
            row.last_success = datetime.now(timezone.utc)
            row.failed = False
        return catalog_view(row)
