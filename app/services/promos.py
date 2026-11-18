"""Bonus-day vouchers managed from the owner dashboard.

Codes provide additional subscription days at the *next verified purchase*,
not arbitrary free access; the claim ledger is unique per account and code.
"""
import re
from sqlalchemy import func, select
from app.db.models import PromoCode, PromoRedemption, User
from app.db.session import SessionLocal

CODE_PATTERN = re.compile(r"[A-Z0-9][A-Z0-9_-]{3,31}")


def normalize_code(value: str) -> str:
    code = str(value).strip().upper()
    if not CODE_PATTERN.fullmatch(code):
        raise ValueError("Promo code must contain 4–32 Latin letters, digits, _ or -")
    return code


def validate_promo(code: str, bonus_days: int, max_uses: int) -> str:
    result = normalize_code(code)
    if type(bonus_days) is not int or not 1 <= bonus_days <= 90:
        raise ValueError("Bonus must be 1–90 days")
    if type(max_uses) is not int or not 1 <= max_uses <= 100000:
        raise ValueError("Redemption limit must be 1–100000")
    return result


async def create_promo(code: str, bonus_days: int, max_uses: int) -> dict:
    normalized = validate_promo(code, bonus_days, max_uses)
    async with SessionLocal() as session:
        if await session.scalar(select(PromoCode.id).where(PromoCode.code == normalized)):
            raise ValueError("This promo code already exists")
        row = PromoCode(code=normalized, bonus_days=bonus_days, max_uses=max_uses, active=True)
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return promo_to_dict(row)


def promo_to_dict(row: PromoCode) -> dict:
    return {
        "id": row.id, "code": row.code,
        "bonus_days": row.bonus_days, "max_uses": row.max_uses,
        "used_count": row.used_count, "active": row.active,
    }


async def list_promos() -> list[dict]:
    async with SessionLocal() as session:
        rows = (await session.scalars(
            select(PromoCode).order_by(PromoCode.id.desc()).limit(100)
        )).all()
        return [promo_to_dict(p) for p in rows]


async def set_promo_active(promo_id: int, enabled: bool) -> dict:
    if type(enabled) is not bool:
        raise ValueError("Invalid promo status")
    async with SessionLocal() as session:
        row = await session.get(PromoCode, promo_id, with_for_update=True)
        if not row:
            raise ValueError("Promo code not found")
        row.active = enabled
        await session.commit()
        return promo_to_dict(row)


async def redeem_promo(telegram_id: int, code: str) -> dict:
    normalized = normalize_code(code)
    async with SessionLocal() as session:
        user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
        if not user:
            raise ValueError("Register with /start first")
        promo = await session.scalar(
            select(PromoCode).where(PromoCode.code == normalized).with_for_update()
        )
        if not promo or not promo.active:
            raise ValueError("Promo code not found or disabled")
        if await session.scalar(
            select(PromoRedemption.id).where(
                PromoRedemption.user_id == user.id,
                PromoRedemption.promo_id == promo.id,
            )
        ):
            raise ValueError("You have already used this promo code")
        if promo.used_count >= promo.max_uses:
            raise ValueError("Promo code redemption limit reached")
        promo.used_count += 1
        session.add(PromoRedemption(
            user_id=user.id, promo_id=promo.id, bonus_days=promo.bonus_days,
        ))
        await session.commit()
        return {"code": promo.code, "bonus_days": promo.bonus_days}


async def promo_balance(telegram_id: int) -> dict:
    async with SessionLocal() as session:
        user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
        if not user:
            return {"days": 0, "claimed": 0}
        days = await session.scalar(select(func.coalesce(func.sum(PromoRedemption.bonus_days), 0)).where(
            PromoRedemption.user_id == user.id,
            PromoRedemption.applied_payment_id.is_(None),
        )) or 0
        count = await session.scalar(select(func.count(PromoRedemption.id)).where(
            PromoRedemption.user_id == user.id
        )) or 0
        return {"days": int(days), "claimed": int(count)}


async def pending_promo_redemptions(session, user_id: int) -> list[PromoRedemption]:
    return list((await session.scalars(
        select(PromoRedemption)
        .where(PromoRedemption.user_id == user_id,
               PromoRedemption.applied_payment_id.is_(None))
        .order_by(PromoRedemption.id)
        .with_for_update()
    )).all())
