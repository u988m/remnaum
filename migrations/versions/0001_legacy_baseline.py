"""Baseline existing Remnaum schemas without deleting data.

Revision ID: 0001_legacy_baseline
Revises:
"""
from alembic import op
from sqlalchemy import inspect, text, update
from sqlalchemy.dialects.postgresql import insert

from app.config import settings
from app.db.base import Base
from app.db.models import SquadPolicy, TariffPlan
import app.db.models  # Registers all model tables.

revision = "0001_legacy_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    # Equivalent to the previous bootstrap for fresh and already-installed shops.
    Base.metadata.create_all(conn, checkfirst=True)
    conn.execute(text("ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS paid_tariff VARCHAR(48)"))

    columns = inspect(conn).get_columns("tariff_plans")
    if not any(column["name"] == "internal_squads" for column in columns):
        conn.execute(text("ALTER TABLE tariff_plans ADD COLUMN internal_squads JSON"))
        # Preserve squads previously configured through .env during the upgrade.
        if settings.remnawave_internal_squad_ids:
            conn.execute(update(TariffPlan).values(
                internal_squads=settings.remnawave_internal_squad_ids
            ))
    for mode in ("flexible", "trial"):
        conn.execute(insert(SquadPolicy).values(
            mode=mode, squads=settings.remnawave_internal_squad_ids
        ).on_conflict_do_nothing())


def downgrade():
    # Downgrading the baseline would destroy real shops. Restore from a backup.
    raise RuntimeError("The Remnaum baseline migration cannot be downgraded safely")
