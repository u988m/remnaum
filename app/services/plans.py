"""Fixed price plans: a second purchase mode alongside the flexible tariff builder."""
from dataclasses import dataclass
from hashlib import sha256
import json

from sqlalchemy import select, delete
from sqlalchemy.dialects.postgresql import insert
from app.db.models import InternalSquadCatalog, TariffPlan, SelectedPlan, TariffDraft, User
from app.db.session import SessionLocal
from app.services.tariffs import TariffQuote
from app.config import settings


EXTRA_PRICE_FIELDS = ("extra_device_price_rub_minor", "extra_device_price_stars")


@dataclass(frozen=True)
class PlanQuote(TariffQuote):
    name: str
    devices_included: int
    extra_devices: int
    unit_minor: int | None
    stars: int | None


def max_extra_devices(plan: TariffPlan) -> int:
    unit_price = plan.extra_device_price_rub_minor
    if unit_price is None:
        return 0
    by_device_limit = min(1000, max(0, settings.tariff_max_devices - plan.device_limit))
    if unit_price == 0:
        return by_device_limit
    return min(by_device_limit, (2_147_483_647 - plan.price_rub_minor) // unit_price)


def plan_revision(plan: TariffPlan) -> str:
    terms = (
        plan.title, plan.duration_days, plan.traffic_gb, plan.device_limit, plan.price_rub_minor,
        plan.price_stars, plan.extra_device_price_rub_minor, plan.extra_device_price_stars,
        sorted(plan.internal_squads or []),
    )
    return sha256(json.dumps(terms).encode()).hexdigest()[:20]


def quote_plan(plan: TariffPlan | None, extra_devices: int = 0, revision: str | None = None) -> PlanQuote:
    if plan is None or not plan.is_active:
        raise ValueError("Тариф недоступен")
    if revision is not None and revision != plan_revision(plan):
        raise ValueError("Условия тарифа изменились. Выберите тариф заново.")
    if type(extra_devices) is not int or not 0 <= extra_devices <= max_extra_devices(plan):
        raise ValueError("Недопустимое количество дополнительных устройств")
    unit_minor = plan.extra_device_price_rub_minor
    stars_unit = plan.extra_device_price_stars
    amount = plan.price_rub_minor + extra_devices * (unit_minor or 0)
    stars = plan.price_stars + extra_devices * (stars_unit or 0)
    return PlanQuote(
        plan.duration_days, plan.traffic_gb, plan.device_limit + extra_devices,
        amount, "RUB", plan.title, plan.device_limit, extra_devices, unit_minor,
        None if (extra_devices and stars_unit is None) or stars > 2_147_483_647 else stars,
    )


def plan_to_dict(p: TariffPlan) -> dict:
    return {
        "id": p.id, "title": p.title, "name": p.title, "description": p.description,
        "duration_days": p.duration_days, "days": p.duration_days, "traffic_gb": p.traffic_gb,
        "device_limit": p.device_limit, "devices_included": p.device_limit,
        "price_rub_minor": p.price_rub_minor, "price_rub": p.price_rub_minor / 100,
        "price_stars": p.price_stars, "is_active": p.is_active, "sort_order": p.sort_order,
        "extra_device_price_rub_minor": p.extra_device_price_rub_minor,
        "extra_device_price_rub": (p.extra_device_price_rub_minor / 100
                                   if p.extra_device_price_rub_minor is not None else None),
        "extra_device_price_stars": p.extra_device_price_stars, "badge": p.badge,
        "is_featured": p.is_featured, "max_extra_devices": max_extra_devices(p),
        "revision": plan_revision(p),
    }


def validate_plan(data: dict) -> None:
    title = str(data.get("title", "")).strip()
    if not 1 <= len(title) <= 80:
        raise ValueError("Укажите название тарифа длиной до 80 символов")
    if len(str(data.get("description", ""))) > 2000:
        raise ValueError("Описание тарифа слишком длинное")
    bounds = {
        "duration_days": (1, settings.tariff_max_days),
        "traffic_gb": (0, settings.tariff_max_traffic_gb),
        "device_limit": (1, settings.tariff_max_devices),
        "price_rub_minor": (0, 100_000_000),
        "price_stars": (1, 1_000_000),
        "extra_device_price_rub_minor": (0, 100_000_000),
        "extra_device_price_stars": (0, 1_000_000),
    }
    for key, (minimum, maximum) in bounds.items():
        value = data.get(key)
        if key in EXTRA_PRICE_FIELDS and value is None:
            continue
        if type(value) is not int or not minimum <= value <= maximum:
            raise ValueError(f"Недопустимое значение {key} (от {minimum} до {maximum})")
    if data.get("badge", "") not in {"", "Популярный", "Выгодно"}:
        raise ValueError("Выберите бейдж из списка")


async def get_plan(plan_id: int) -> TariffPlan | None:
    async with SessionLocal() as session:
        return await session.get(TariffPlan, plan_id)


async def list_plans(active_only: bool = True) -> list[dict]:
    async with SessionLocal() as session:
        query = select(TariffPlan)
        if active_only:
            query = query.where(TariffPlan.is_active.is_(True))
        query = query.order_by(TariffPlan.sort_order, TariffPlan.id)
        plans = (await session.scalars(query)).all()
        return [plan_to_dict(p) if active_only else plan_to_dict(p) | {"internal_squads": p.internal_squads}
                for p in plans]


async def save_plan(data: dict, plan_id: int | None = None) -> dict:
    validate_plan(data)
    async with SessionLocal() as session:
        plan = await session.get(TariffPlan, plan_id, with_for_update=True) if plan_id else None
        if plan_id and plan is None:
            raise ValueError("Unknown plan")
        if plan is None:
            plan = TariffPlan()
        selected = data.get("internal_squads")
        if selected is not None:
            if not selected:
                raise ValueError("Выберите хотя бы один Internal Squad")
            catalog = await session.get(InternalSquadCatalog, 1)
            known = catalog.items if catalog else {}
            for identifier in set(selected) - set(plan.internal_squads or []):
                if not known.get(identifier, {}).get("available"):
                    raise ValueError("Сквад недоступен. Обновите список сквадов.")
            plan.internal_squads = list(dict.fromkeys(selected))
        elif not plan_id:
            raise ValueError("Выберите хотя бы один Internal Squad")
        for key in ("title", "description", "duration_days", "traffic_gb", "device_limit", "price_rub_minor", "price_stars", "is_active", "sort_order", "badge", "is_featured", *EXTRA_PRICE_FIELDS):
            if key in data:
                setattr(plan, key, data[key])
        session.add(plan)
        await session.commit()
        await session.refresh(plan)
        return plan_to_dict(plan) | {"internal_squads": plan.internal_squads}


async def choose_plan(telegram_id: int, plan_id: int) -> TariffQuote:
    async with SessionLocal() as session:
        plan = await session.get(TariffPlan, plan_id)
        if not plan or not plan.is_active:
            raise ValueError("This plan is not available")
        user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
        if user is None:
            raise ValueError("User is not registered")
        draft = await session.scalar(select(TariffDraft).where(TariffDraft.user_id == user.id))
        if draft is None:
            draft = TariffDraft(user_id=user.id)
            session.add(draft)
        draft.duration_days = plan.duration_days
        draft.traffic_gb = plan.traffic_gb
        draft.device_limit = plan.device_limit
        draft.amount_minor = plan.price_rub_minor
        stmt = insert(SelectedPlan).values(user_id=user.id, plan_id=plan.id)
        stmt = stmt.on_conflict_do_update(
            index_elements=[SelectedPlan.user_id], set_={"plan_id": plan.id}
        )
        await session.execute(stmt)
        await session.commit()
        return quote_plan(plan)


async def selected_plan_for_user(session, user_id: int) -> TariffPlan | None:
    return await session.scalar(
        select(TariffPlan).join(SelectedPlan, SelectedPlan.plan_id == TariffPlan.id)
        .where(SelectedPlan.user_id == user_id)
    )


async def clear_selected_plan(telegram_id: int) -> None:
    async with SessionLocal() as session:
        user = await session.scalar(select(User).where(User.telegram_id == telegram_id).with_for_update())
        if user:
            selected = await session.get(SelectedPlan, user.id)
            if selected:
                await session.delete(selected)
                await session.execute(delete(TariffDraft).where(TariffDraft.user_id == user.id))
                await session.commit()


async def selected_stars_price(telegram_id: int, quote: TariffQuote) -> int | None:
    async with SessionLocal() as session:
        user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
        if not user:
            return None
        plan = await selected_plan_for_user(session, user.id)
        if not plan:
            return None
        if not plan.is_active or (
            quote.duration_days, quote.traffic_gb, quote.device_limit
        ) != (plan.duration_days, plan.traffic_gb, plan.device_limit):
            raise ValueError("Selected plan changed; select again")
        return plan.price_stars
