"""Single-store branded appearance and support settings."""
import re
from sqlalchemy.dialects.postgresql import insert
from app.db.models import StoreProfile
from app.db.session import SessionLocal
from app.config import settings

THEME_COLORS = frozenset({
    "bg", "surface", "surface-raised", "input", "border", "border-strong", "text",
    "muted", "subtle", "primary", "on-primary", "overlay", "glow", "grid", "focus", "success", "danger",
})
COLOR_OPACITY = {"overlay": "eb", "glow": "1f", "grid": "0d"}

DEFAULT_PROFILE = {
    "name": "Remnaum",
    "tagline": "",
    "description": "",
    "connection_instructions": "",
    "bot_connection_instructions": "",
    "accent_color": "#4b8bea",
    "theme_colors": {},
    "support_contact": settings.support_contact,
    "terms_url": settings.telegram_terms_url,
}


def validate_profile(data: dict) -> dict:
    if set(data) - set(DEFAULT_PROFILE):
        raise ValueError("Unknown store profile field")
    next_values = DEFAULT_PROFILE | data
    for key, maximum in {"name": 80, "tagline": 300, "description": 300, "connection_instructions": 1600,
                         "bot_connection_instructions": 1600, "support_contact": 300, "terms_url": 300}.items():
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
    colors = next_values["theme_colors"]
    if not isinstance(colors, dict) or colors.keys() - THEME_COLORS or any(
        not isinstance(value, str) or not re.fullmatch(r"#[a-fA-F0-9]{6}", value) for value in colors.values()
    ):
        raise ValueError("Недопустимые цвета палитры.")
    next_values["theme_colors"] = {key: value.lower() for key, value in colors.items()}
    return next_values


async def get_store_profile() -> dict:
    async with SessionLocal() as session:
        row = await session.get(StoreProfile, 1)
        return DEFAULT_PROFILE.copy() if row is None else validate_profile(row.data)


async def update_store_profile(values: dict) -> dict:
    async with SessionLocal.begin() as session:
        await session.execute(insert(StoreProfile).values(id=1, data={}).on_conflict_do_nothing())
        row = await session.get(StoreProfile, 1, with_for_update=True)
        row.data = validate_profile(row.data | values)
        return row.data


async def get_terms_url() -> str:
    profile = await get_store_profile()
    return str(profile.get("terms_url") or "")
