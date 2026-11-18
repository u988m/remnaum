"""Adopt the schema shipped before versioned migrations."""

from pathlib import Path

from alembic import op


revision = "0001_baseline"
down_revision = None


def upgrade() -> None:
    # The first migration also runs on installations created with metadata.create_all.
    schema = Path(__file__).with_suffix(".sql").read_text(encoding="utf-8")
    for statement in schema.split(";"):
        if statement.strip():
            op.execute(statement.strip())
