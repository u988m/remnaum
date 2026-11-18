from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.config import settings
from app.db.models import TariffDraft, User
from app.db.session import SessionLocal


@dataclass(frozen=True)
class TariffQuote:
    duration_days: int
    traffic_gb: int
    device_limit: int
    amount_minor: int
    currency: str


FIELDS = {
    "days": ("tariff_min_days", "tariff_max_days", "duration_days"),
    "traffic": ("tariff_min_traffic_gb", "tariff_max_traffic_gb", "traffic_gb"),
    "devices": ("tariff_min_devices", "tariff_max_devices", "device_limit"),
}


def calculate_price(duration_days: int, traffic_gb: int, device_limit: int) -> int:
    if duration_days < 1 or traffic_gb < 1 or device_limit < 1:
        raise ValueError("Tariff values must be positive")
    return (
        duration_days * settings.tariff_price_per_day_minor
        + traffic_gb * settings.tariff_price_per_gb_minor
        + device_limit * settings.tariff_price_per_device_minor
    )


def format_price(amount_minor: int, currency: str | None = None) -> str:
    code = currency or settings.tariff_currency
    return f"{amount_minor / 100:.2f} {code}"


async def update_tariff_draft(telegram_id: int, username: str | None, field: str, delta: int) -> TariffQuote:
    if field not in FIELDS:
        raise ValueError("Unknown tariff field")
    min_setting, max_setting, model_field = FIELDS[field]

    async with SessionLocal() as session:
        user_insert = insert(User).values(telegram_id=telegram_id, username=username)
        user_insert = user_insert.on_conflict_do_update(
            index_elements=[User.telegram_id],
            set_={"username": user_insert.excluded.username},
        )
        await session.execute(user_insert)
        user = await session.scalar(
            select(User).where(User.telegram_id == telegram_id).with_for_update()
        )
        draft = await session.scalar(
            select(TariffDraft).where(TariffDraft.user_id == user.id)
        )
        if draft is None:
            draft = TariffDraft(user_id=user.id)
            session.add(draft)
            await session.flush()

        minimum = getattr(settings, min_setting)
        maximum = getattr(settings, max_setting)
        current = getattr(draft, model_field)
        setattr(draft, model_field, max(minimum, min(maximum, current + delta)))
        draft.amount_minor = calculate_price(
            draft.duration_days, draft.traffic_gb, draft.device_limit
        )
        await session.commit()
        return TariffQuote(
            duration_days=draft.duration_days,
            traffic_gb=draft.traffic_gb,
            device_limit=draft.device_limit,
            amount_minor=draft.amount_minor,
            currency=settings.tariff_currency,
        )


async def get_tariff_draft(telegram_id: int) -> TariffQuote:
    async with SessionLocal() as session:
        draft = await session.scalar(
            select(TariffDraft)
            .join(User, TariffDraft.user_id == User.id)
            .where(User.telegram_id == telegram_id)
        )
        if draft is None:
            return TariffQuote(
                duration_days=settings.tariff_min_days,
                traffic_gb=settings.tariff_min_traffic_gb,
                device_limit=settings.tariff_min_devices,
                amount_minor=calculate_price(
                    settings.tariff_min_days,
                    settings.tariff_min_traffic_gb,
                    settings.tariff_min_devices,
                ),
                currency=settings.tariff_currency,
            )
        return TariffQuote(
            duration_days=draft.duration_days,
            traffic_gb=draft.traffic_gb,
            device_limit=draft.device_limit,
            amount_minor=draft.amount_minor,
            currency=settings.tariff_currency,
        )
