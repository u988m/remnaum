from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, text


def upgrade_database(connection: Connection) -> None:
    # Serialize startup migrations when two application instances start together.
    connection.execute(text("SELECT pg_advisory_xact_lock(4122891125199001)"))
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).with_name("migrations")))
    config.attributes["connection"] = connection
    command.upgrade(config, "head")
