"""Single-store branded appearance and support settings."""
import re
from sqlalchemy import select
from app.db.models import StoreProfile
from app.db.session import SessionLocal
from app.config import settings

DEFAULT_PROFILE = {
    "name": "Remnaum",
    "tagline": "",
    "description": "",
    "connection_instructions": "",
    "accent_color": "#4b8bea",
    "support_contact": settings.support_contact,
    "terms_url": settings.telegram_terms_url,
}


def validate_profile(data: dict) -> dict:
    if set(data) - set(DEFAULT_PROFILE):
        raise ValueError("Unknown store profile field")
    next_values = DEFAULT_PROFILE | data
    for key, maximum in {"name": 80, "tagline": 300, "description": 300, "connection_instructions": 1600,
                         "support_contact": 300, "terms_url": 300}.items():
        value = next_values[key]
        if not isinstance(value, str) or len(value) > maximum:
            raise ValueError(f"Invalid {key}")
    if next_values["tagline"].strip().casefold() == "ваш vpn — ваши правила":
        next_values["tagline"] = ""
    terms_url = next_values["terms_url"]
    if terms_url and (not terms_url.startswith("https://") or len(terms_url) > 300):
        raise ValueError("Terms URL must be a public HTTPS address")
    if not next_values["name"].strip():
        raise ValueError("Store name is required")
    color = next_values["accent_color"]
    if not isinstance(color, str) or not re.fullmatch(r"#[a-fA-F0-9]{6}", color):
        raise ValueError("Accent color must be #RRGGBB")
    return next_values


async def get_store_profile() -> dict:
    async with SessionLocal() as session:
        row = await session.get(StoreProfile, 1)
        return DEFAULT_PROFILE.copy() if row is None else validate_profile(row.data)


async def update_store_profile(values: dict) -> dict:
    current = await get_store_profile()
    updated = validate_profile(current | values)
    async with SessionLocal() as session:
        row = await session.get(StoreProfile, 1)
        if row is None:
            session.add(StoreProfile(id=1, data=updated))
        else:
            row.data = updated
        await session.commit()
    return updated


async def get_terms_url() -> str:
    profile = await get_store_profile()
    return str(profile.get("terms_url") or "")
