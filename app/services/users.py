from aiogram.types import User as TelegramUser
from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert

from app.db.models import User
from app.db.session import SessionLocal


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
