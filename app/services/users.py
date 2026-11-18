from aiogram.types import User as TelegramUser
from sqlalchemy.dialects.postgresql import insert

from app.db.models import User
from app.db.session import SessionLocal


async def get_or_create_user(telegram_user: TelegramUser) -> None:
    statement = insert(User).values(
        telegram_id=telegram_user.id,
        username=telegram_user.username,
    )
    statement = statement.on_conflict_do_update(
        index_elements=[User.telegram_id],
        set_={"username": statement.excluded.username},
    )
    async with SessionLocal() as session:
        await session.execute(statement)
        await session.commit()
