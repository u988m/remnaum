"""Remember which subscription expiry has already been announced."""

import sqlalchemy as sa
from alembic import op


revision = "0003_renewal_reminders"
down_revision = "0002_payment_activation"


def upgrade() -> None:
    op.add_column("subscriptions", sa.Column("reminded_expiry", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_subscriptions_active_expiry", "subscriptions", ["expires_at"],
                    postgresql_where=sa.text("status = 'active'"))


def downgrade() -> None:
    op.drop_index("ix_subscriptions_active_expiry", table_name="subscriptions")
    op.drop_column("subscriptions", "reminded_expiry")
