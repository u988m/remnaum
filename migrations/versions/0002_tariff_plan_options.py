"""Add optional device pricing and merchandising fields to ready-made tariffs."""
from alembic import op
import sqlalchemy as sa

revision = "0002_tariff_plan_options"
down_revision = "0001_legacy_baseline"
branch_labels = None
depends_on = None


def upgrade():
    # The baseline uses current metadata for fresh installations.
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("tariff_plans")}
    columns = (
        sa.Column("extra_device_price_rub_minor", sa.Integer()),
        sa.Column("extra_device_price_stars", sa.Integer()),
        sa.Column("badge", sa.String(32), nullable=False, server_default=""),
        sa.Column("is_featured", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    for column in columns:
        if column.name not in existing:
            op.add_column("tariff_plans", column)


def downgrade():
    for name in ("is_featured", "badge", "extra_device_price_stars", "extra_device_price_rub_minor"):
        op.drop_column("tariff_plans", name)
