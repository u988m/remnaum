"""Fixed price plans: a second purchase mode alongside the flexible tariff builder."""
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from app.db.models import TariffPlan, SelectedPlan, TariffDraft, User
from app.db.session import SessionLocal
from app.services.tariffs import TariffQuote
from app.config import settings


def plan_to_dict(p: TariffPlan) -> dict:
    return {
        "id": p.id, "title": p.title, "description": p.description,
        "duration_days": p.duration_days, "traffic_gb": p.traffic_gb,
        "device_limit": p.device_limit, "price_rub_minor": p.price_rub_minor,
        "price_stars": p.price_stars, "is_active": p.is_active,
        "sort_order": p.sort_order,
    }


def validate_plan(data: dict) -> None:
    title = str(data.get("title", "")).strip()
    if not 1 <= len(title) <= 80:
        raise ValueError("Plan title must have 1-80 characters")
    if len(str(data.get("description", ""))) > 2000:
        raise ValueError("Plan description is too long")
    for key in ("duration_days", "traffic_gb", "device_limit", "price_rub_minor", "price_stars"):
        val = data.get(key)
        if isinstance(val, bool) or not isinstance(val, int) or val < (0 if key == "price_rub_minor" else 1):
            raise ValueError(f"{key} is invalid")
    if data["price_stars"] > 1_000_000 or data["price_rub_minor"] > 100_000_000:
        raise ValueError("Plan price exceeds allowed range")
    if data["duration_days"] > settings.tariff_max_days or data["traffic_gb"] > settings.tariff_max_traffic_gb or data["device_limit"] > settings.tariff_max_devices:
        raise ValueError("Plan exceeds configured tariff limits")
    if data["duration_days"] < settings.tariff_min_days or data["traffic_gb"] < settings.tariff_min_traffic_gb or data["device_limit"] < settings.tariff_min_devices:
        raise ValueError("Plan is below configured tariff limits")


async def list_plans(active_only: bool = True) -> list[dict]:
    async with SessionLocal() as session:
        query = select(TariffPlan)
        if active_only:
            query = query.where(TariffPlan.is_active.is_(True))
        query = query.order_by(TariffPlan.sort_order, TariffPlan.id)
        return [plan_to_dict(p) for p in (await session.scalars(query)).all()]


async def save_plan(data: dict, plan_id: int | None = None) -> dict:
    validate_plan(data)
    async with SessionLocal() as session:
        plan = await session.get(TariffPlan, plan_id) if plan_id else None
        if plan_id and plan is None:
            raise ValueError("Unknown plan")
        if plan is None:
            plan = TariffPlan()
            session.add(plan)
        for key in ("title", "description", "duration_days", "traffic_gb", "device_limit", "price_rub_minor", "price_stars", "is_active", "sort_order"):
            if key in data:
                setattr(plan, key, data[key])
        await session.commit()
        await session.refresh(plan)
        return plan_to_dict(plan)


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
        return TariffQuote(plan.duration_days, plan.traffic_gb, plan.device_limit, plan.price_rub_minor, "RUB")


async def selected_plan_for_user(session, user_id: int) -> TariffPlan | None:
    return await session.scalar(
        select(TariffPlan).join(SelectedPlan, SelectedPlan.plan_id == TariffPlan.id)
        .where(SelectedPlan.user_id == user_id)
    )
