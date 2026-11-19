"""Store notification rules, customer preferences and delivery state."""
from alembic import op
import sqlalchemy as sa

revision = "0007_subscription_notifications"
down_revision = "0006_store_documents"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("store_notification_settings"):
        op.create_table(
            "store_notification_settings",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("data", sa.JSON(), nullable=False),
        )
    if not inspector.has_table("user_notification_preferences"):
        op.create_table(
            "user_notification_preferences",
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("data", sa.JSON(), nullable=False),
        )
    if not inspector.has_table("subscription_notifications"):
        op.create_table(
            "subscription_notifications",
            sa.Column("subscription_id", sa.Integer(),
                      sa.ForeignKey("subscriptions.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("kind", sa.String(24), primary_key=True),
            sa.Column("period", sa.String(64), primary_key=True),
        )


def downgrade():
    op.drop_table("subscription_notifications")
    op.drop_table("user_notification_preferences")
    op.drop_table("store_notification_settings")
