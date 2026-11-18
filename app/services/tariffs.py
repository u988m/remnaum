from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.config import settings
from app.db.models import StoreTariffSettings, TariffDraft, User
from app.db.session import SessionLocal


@dataclass(frozen=True)
class TariffQuote:
    duration_days: int
    traffic_gb: int
    device_limit: int
    amount_minor: int
    currency: str


DEFAULT_PRICING = {
    "rub_per_day_minor": settings.tariff_price_per_day_minor,
    "rub_per_gb_minor": settings.tariff_price_per_gb_minor,
    "rub_per_device_minor": settings.tariff_price_per_device_minor,
    "stars_per_day": settings.tariff_stars_price_per_day,
    "stars_per_gb": settings.tariff_stars_price_per_gb,
    "stars_per_device": settings.tariff_stars_price_per_device,
    "currency": settings.tariff_currency.upper(),
}
PRICING_FIELDS = set(DEFAULT_PRICING)
FIELDS = {
    "days": ("tariff_min_days", "tariff_max_days", "duration_days"),
    "traffic": ("tariff_min_traffic_gb", "tariff_max_traffic_gb", "traffic_gb"),
    "devices": ("tariff_min_devices", "tariff_max_devices", "device_limit"),
}


async def get_store_tariff_pricing() -> dict:
    async with SessionLocal() as session:
        row = await session.get(StoreTariffSettings, 1)
        return DEFAULT_PRICING.copy() if row is None else DEFAULT_PRICING | {
            key: row.pricing.get(key, value) for key, value in DEFAULT_PRICING.items()
        }


async def update_store_tariff_pricing(pricing: dict) -> dict:
    unknown = set(pricing) - PRICING_FIELDS
    if unknown:
        raise ValueError("Unknown tariff pricing field")
    current = await get_store_tariff_pricing()
    for key, value in pricing.items():
        if key == "currency":
            code = str(value).upper()
            if len(code) < 3 or len(code) > 8 or not code.isalpha():
                raise ValueError("Currency must be a 3-8 letter code")
            current[key] = code
        else:
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{key} must be a non-negative integer")
            current[key] = value
    async with SessionLocal() as session:
        row = await session.get(StoreTariffSettings, 1)
        if row is None:
            row = StoreTariffSettings(id=1, pricing=current)
            session.add(row)
        else:
            row.pricing = current
        await session.commit()
    return current


def calculate_price(duration_days: int, traffic_gb: int, device_limit: int) -> int:
    if min(duration_days, traffic_gb, device_limit) < 1:
        raise ValueError("Tariff values must be positive")
    return (
        duration_days * settings.tariff_price_per_day_minor
        + traffic_gb * settings.tariff_price_per_gb_minor
        + device_limit * settings.tariff_price_per_device_minor
    )


async def calculate_store_price(duration_days: int, traffic_gb: int, device_limit: int) -> int:
    pricing = await get_store_tariff_pricing()
    if min(duration_days, traffic_gb, device_limit) < 1:
        raise ValueError("Tariff values must be positive")
    return duration_days * pricing["rub_per_day_minor"] + traffic_gb * pricing["rub_per_gb_minor"] + device_limit * pricing["rub_per_device_minor"]


def calculate_stars_price(duration_days: int, traffic_gb: int, device_limit: int) -> int:
    if min(duration_days, traffic_gb, device_limit) < 1:
        raise ValueError("Tariff values must be positive")
    amount = (
        duration_days * settings.tariff_stars_price_per_day
        + traffic_gb * settings.tariff_stars_price_per_gb
        + device_limit * settings.tariff_stars_price_per_device
    )
    if amount < 1:
        raise ValueError("Telegram Stars price must be greater than zero")
    return amount


async def calculate_store_stars_price(duration_days: int, traffic_gb: int, device_limit: int) -> int:
    pricing = await get_store_tariff_pricing()
    if min(duration_days, traffic_gb, device_limit) < 1:
        raise ValueError("Tariff values must be positive")
    amount = duration_days * pricing["stars_per_day"] + traffic_gb * pricing["stars_per_gb"] + device_limit * pricing["stars_per_device"]
    if amount < 1:
        raise ValueError("Telegram Stars price must be greater than zero")
    return amount


def format_price(amount_minor: int, currency: str | None = None) -> str:
    code = currency or settings.tariff_currency
    return f"{amount_minor / 100:.2f} {code}"


async def update_tariff_draft(telegram_id: int, username: str | None, field: str, delta: int) -> TariffQuote:
    if field not in FIELDS:
        raise ValueError("Unknown tariff field")
    min_setting, max_setting, model_field = FIELDS[field]
    async with SessionLocal() as session:
        user_insert = insert(User).values(telegram_id=telegram_id, username=username)
        user_insert = user_insert.on_conflict_do_update(index_elements=[User.telegram_id], set_={"username": user_insert.excluded.username})
        await session.execute(user_insert)
        user = await session.scalar(select(User).where(User.telegram_id == telegram_id).with_for_update())
        draft = await session.scalar(select(TariffDraft).where(TariffDraft.user_id == user.id))
        if draft is None:
            days, traffic, devices = settings.tariff_min_days, settings.tariff_min_traffic_gb, settings.tariff_min_devices
            draft = TariffDraft(user_id=user.id, duration_days=days, traffic_gb=traffic, device_limit=devices, amount_minor=0)
            session.add(draft)
            await session.flush()
        minimum, maximum = getattr(settings, min_setting), getattr(settings, max_setting)
        setattr(draft, model_field, max(minimum, min(maximum, getattr(draft, model_field) + delta)))
        draft.amount_minor = await calculate_store_price(draft.duration_days, draft.traffic_gb, draft.device_limit)
        await session.commit()
        pricing = await get_store_tariff_pricing()
        return TariffQuote(draft.duration_days, draft.traffic_gb, draft.device_limit, draft.amount_minor, pricing["currency"])


async def set_tariff_draft(
    telegram_id: int,
    username: str | None,
    duration_days: int,
    traffic_gb: int,
    device_limit: int,
) -> TariffQuote:
    values = (duration_days, traffic_gb, device_limit)
    bounds = (
        (settings.tariff_min_days, settings.tariff_max_days),
        (settings.tariff_min_traffic_gb, settings.tariff_max_traffic_gb),
        (settings.tariff_min_devices, settings.tariff_max_devices),
    )
    if any(value < minimum or value > maximum for value, (minimum, maximum) in zip(values, bounds)):
        raise ValueError("Tariff values are outside configured limits")
    async with SessionLocal() as session:
        statement = insert(User).values(telegram_id=telegram_id, username=username)
        statement = statement.on_conflict_do_update(
            index_elements=[User.telegram_id],
            set_={"username": statement.excluded.username},
        )
        await session.execute(statement)
        user = await session.scalar(select(User).where(User.telegram_id == telegram_id).with_for_update())
        draft = await session.scalar(select(TariffDraft).where(TariffDraft.user_id == user.id).with_for_update())
        amount = await calculate_store_price(duration_days, traffic_gb, device_limit)
        if draft is None:
            draft = TariffDraft(
                user_id=user.id,
                duration_days=duration_days,
                traffic_gb=traffic_gb,
                device_limit=device_limit,
                amount_minor=amount,
            )
            session.add(draft)
        else:
            draft.duration_days = duration_days
            draft.traffic_gb = traffic_gb
            draft.device_limit = device_limit
            draft.amount_minor = amount
        await session.commit()
    pricing = await get_store_tariff_pricing()
    return TariffQuote(duration_days, traffic_gb, device_limit, amount, pricing["currency"])


async def get_tariff_draft(telegram_id: int) -> TariffQuote:
    async with SessionLocal() as session:
        draft = await session.scalar(select(TariffDraft).join(User, TariffDraft.user_id == User.id).where(User.telegram_id == telegram_id))
        if draft is None:
            days, traffic, devices = settings.tariff_min_days, settings.tariff_min_traffic_gb, settings.tariff_min_devices
            amount = await calculate_store_price(days, traffic, devices)
        else:
            days, traffic, devices = draft.duration_days, draft.traffic_gb, draft.device_limit
            amount = await calculate_store_price(days, traffic, devices)
        pricing = await get_store_tariff_pricing()
        return TariffQuote(days, traffic, devices, amount, pricing["currency"])


async def ensure_tariff_draft(telegram_id: int) -> TariffQuote:
    async with SessionLocal() as session:
        user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
        if user is None:
            raise ValueError("Telegram user is not registered")
        draft = await session.scalar(select(TariffDraft).where(TariffDraft.user_id == user.id).with_for_update())
        if draft is None:
            days, traffic, devices = settings.tariff_min_days, settings.tariff_min_traffic_gb, settings.tariff_min_devices
            amount = await calculate_store_price(days, traffic, devices)
            draft = TariffDraft(user_id=user.id, duration_days=days, traffic_gb=traffic, device_limit=devices, amount_minor=amount)
            session.add(draft)
            await session.commit()
        else:
            amount = await calculate_store_price(draft.duration_days, draft.traffic_gb, draft.device_limit)
        pricing = await get_store_tariff_pricing()
        return TariffQuote(draft.duration_days, draft.traffic_gb, draft.device_limit, amount, pricing["currency"])
