"""Referral attribution and one-time bonus days issued after a fulfilled payment.

Rewards are credited to the inviter's balance and applied to the next successfully
provisioned subscription purchase. They never change a live Remnawave user
independently of a verified payment, avoiding duplicate remote extensions.
"""
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from app.db.models import Referral, ReferralReward, ReferralSettings, User
from app.db.session import SessionLocal

DEFAULT_REFERRAL_SETTINGS = {"enabled": False, "reward_days": 3}


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
            select(func.count(Referral.id)).join(User, Referral.inviter_id == User.id)
            .where(User.telegram_id == telegram_id)
        ) or 0


async def get_referral_settings() -> dict:
    async with SessionLocal() as session:
        row = await session.get(ReferralSettings, 1)
        return ({"enabled": row.enabled, "reward_days": row.reward_days}
                if row else DEFAULT_REFERRAL_SETTINGS.copy())


async def update_referral_settings(enabled: bool, reward_days: int) -> dict:
    if not isinstance(enabled, bool) or isinstance(reward_days, bool) or not isinstance(reward_days, int):
        raise ValueError("Invalid referral configuration")
    if reward_days < 1 or reward_days > 90:
        raise ValueError("Reward must be between 1 and 90 days")
    async with SessionLocal() as session:
        row = await session.get(ReferralSettings, 1)
        if row is None:
            row = ReferralSettings(id=1, enabled=enabled, reward_days=reward_days)
            session.add(row)
        else:
            row.enabled = enabled
            row.reward_days = reward_days
        await session.commit()
    return {"enabled": enabled, "reward_days": reward_days}


async def reward_balance(telegram_id: int) -> dict:
    async with SessionLocal() as session:
        inviter = await session.scalar(select(User).where(User.telegram_id == telegram_id))
        if not inviter:
            return {"days": 0, "earned": 0}
        earned = await session.scalar(select(func.count(ReferralReward.id))
                                     .where(ReferralReward.inviter_id == inviter.id)) or 0
        days = await session.scalar(select(func.coalesce(func.sum(ReferralReward.days), 0))
                                    .where(ReferralReward.inviter_id == inviter.id,
                                           ReferralReward.redeemed_payment_id.is_(None))) or 0
        return {"days": int(days), "earned": int(earned)}


async def pending_rewards_for_payment(session, user_id: int) -> list[ReferralReward]:
    """Lock all pending credits for this user for the duration of provisioning."""
    return list((await session.scalars(
        select(ReferralReward)
        .where(ReferralReward.inviter_id == user_id,
               ReferralReward.redeemed_payment_id.is_(None))
        .order_by(ReferralReward.id)
        .with_for_update()
    )).all())


async def grant_referral_reward(session, invited_id: int, payment_id: int) -> None:
    """Called inside the same transaction that marks payment as fulfilled."""
    config = await session.get(ReferralSettings, 1)
    if not config or not config.enabled or not (1 <= config.reward_days <= 90):
        return
    referral = await session.scalar(select(Referral).where(Referral.invited_id == invited_id))
    if not referral or referral.inviter_id == invited_id:
        return
    stmt = insert(ReferralReward).values(
        inviter_id=referral.inviter_id,
        invited_id=invited_id,
        source_payment_id=payment_id,
        days=config.reward_days,
    ).on_conflict_do_nothing(index_elements=[ReferralReward.invited_id])
    await session.execute(stmt)
