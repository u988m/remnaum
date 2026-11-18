"""Track and recover paid orders awaiting subscription activation."""

import sqlalchemy as sa
from alembic import op


revision = "0002_payment_activation"
down_revision = "0001_baseline"


def upgrade() -> None:
    op.create_table(
        "payment_activations",
        sa.Column("payment_id", sa.Integer(), sa.ForeignKey("payments.id"), primary_key=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(32), nullable=True),
        sa.Column("bonus_days", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("payment_activations")
