"""Referral attribution. No awards or payout until separately implemented."""
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from app.db.models import Referral, User
from app.db.session import SessionLocal


def referral_code(telegram_id: int) -> str:
    if telegram_id < 1:
        raise ValueError("Invalid Telegram ID")
    return f"ref_{telegram_id}"


def parse_referral_start(text: str | None) -> int | None:
    if not text:
        return None
    parts = text.strip().split()
    if len(parts) != 2 or parts[0] != "/start" or not parts[1].startswith("ref_"):
        return None
    raw = parts[1][4:]
    if not raw.isascii() or not raw.isdigit():
        return None
    value = int(raw)
    return value if 0 < value < 10 ** 16 else None


async def register_referral(invited_telegram_id: int, inviter_telegram_id: int) -> bool:
    if invited_telegram_id < 1 or inviter_telegram_id < 1 or invited_telegram_id == inviter_telegram_id:
        return False
    async with SessionLocal() as session:
        inviter = await session.scalar(select(User).where(User.telegram_id == inviter_telegram_id))
        invited = await session.scalar(select(User).where(User.telegram_id == invited_telegram_id))
        if inviter is None or invited is None:
            return False
        statement = insert(Referral).values(inviter_id=inviter.id, invited_id=invited.id)
        statement = statement.on_conflict_do_nothing(index_elements=[Referral.invited_id])
        result = await session.execute(statement)
        await session.commit()
        return result.rowcount > 0


async def referral_count(telegram_id: int) -> int:
    async with SessionLocal() as session:
        return await session.scalar(
            select(func.count(Referral.id)).join(User, Referral.inviter_id == User.id).where(User.telegram_id == telegram_id)
        ) or 0
