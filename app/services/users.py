from aiogram.types import User as TelegramUser
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from app.db.models import User
from app.db.session import SessionLocal

BLOCKED_MESSAGE = "Доступ к магазину заблокирован администратором."


class BlockedCustomer(ValueError):
    def __init__(self):
        super().__init__(BLOCKED_MESSAGE)


async def customer_is_blocked(telegram_id: int) -> bool:
    async with SessionLocal() as session:
        return await session.scalar(select(User.blocked_at).where(User.telegram_id == telegram_id)) is not None


async def upsert_active_customer(session, telegram_id: int, username: str | None) -> None:
    statement = insert(User).values(telegram_id=telegram_id, username=username).on_conflict_do_update(
        index_elements=[User.telegram_id], set_={"username": username}, where=User.blocked_at.is_(None),
    ).returning(User.id)
    if await session.scalar(statement) is None:
        raise BlockedCustomer()


async def get_or_create_user(telegram_user: TelegramUser) -> bool:
    """Upsert user and return True only when registered for the first time."""
    async with SessionLocal() as session:
        statement = insert(User).values(
            telegram_id=telegram_user.id,
            username=telegram_user.username,
        ).on_conflict_do_nothing(index_elements=[User.telegram_id]).returning(User.id)
        inserted_id = await session.scalar(statement)
        if inserted_id is None:
            await session.execute(
                update(User).where(User.telegram_id == telegram_user.id).values(username=telegram_user.username)
            )
        await session.commit()
    return inserted_id is not None
