from aiogram.types import User as TelegramUser
from sqlalchemy.dialects.postgresql import insert

from app.db.models import User
from app.db.session import SessionLocal


async def get_or_create_user(telegram_user: TelegramUser) -> None:
    statement = insert(User).values(
        telegram_id=telegram_user.id,
        username=telegram_user.username,
        first_name=telegram_user.first_name,
        language_code=telegram_user.language_code,
    )
    statement = statement.on_conflict_do_update(
        index_elements=[User.telegram_id],
        set_={
            "username": statement.excluded.username,
            "first_name": statement.excluded.first_name,
            "language_code": statement.excluded.language_code,
        },
    )
    async with SessionLocal() as session:
        await session.execute(statement)
        await session.commit()
