from sqlalchemy import inspect, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.db.base import Base
from app.db.models import TariffPlan

engine = create_async_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def initialize_schema(connection):
    await connection.execute(text("SELECT pg_advisory_xact_lock(hashtext('remnaum-schema'))"))
    await connection.run_sync(Base.metadata.create_all)
    columns = await connection.run_sync(lambda conn: inspect(conn).get_columns("tariff_plans"))
    if any(column["name"] == "internal_squads" for column in columns):
        return
    await connection.execute(text("ALTER TABLE tariff_plans ADD COLUMN internal_squads JSON"))
    # Freeze the access already configured before this upgrade, even if .env changes later.
    if settings.remnawave_internal_squad_ids:
        await connection.execute(update(TariffPlan).values(internal_squads=settings.remnawave_internal_squad_ids))
