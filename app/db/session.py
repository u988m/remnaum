"""Database connection and transactional Alembic migrations."""
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings

engine = create_async_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def initialize_schema(connection):
    """Apply every versioned migration under the existing PostgreSQL startup lock."""
    await connection.execute(text("SELECT pg_advisory_xact_lock(hashtext('remnaum-schema'))"))

    def upgrade(sync_connection):
        config = Config(str(Path(__file__).resolve().parents[2] / "migrations" / "alembic.ini"))
        config.attributes["connection"] = sync_connection
        command.upgrade(config, "head")

    await connection.run_sync(upgrade)
