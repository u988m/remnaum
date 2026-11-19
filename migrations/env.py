"""Run Alembic using the app's existing PostgreSQL connection and transaction."""
from alembic import context

from app.db.base import Base
import app.db.models  # Registers all declarative tables with Base.metadata.

config = context.config
target_metadata = Base.metadata

if context.is_offline_mode():
    raise RuntimeError("Offline migrations are not supported for Remnaum")

connection = config.attributes.get("connection")
if connection is None:
    raise RuntimeError("Run migrations through app.db.session.initialize_schema")

context.configure(connection=connection, target_metadata=target_metadata)
with context.begin_transaction():
    context.run_migrations()
