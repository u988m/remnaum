"""Web sign-in, support, theme and Telegram delivery settings."""
from alembic import op
import sqlalchemy as sa

revision = "0008_store_operations"
down_revision = "0007_subscription_notifications"
branch_labels = None
depends_on = None


def upgrade():
    from app.db.models import GoogleIdentity, StoreOperationsSettings, SupportReply, SupportTicket, UserTheme

    bind = op.get_bind()
    if "bot_blocked_at" not in {column["name"] for column in sa.inspect(bind).get_columns("users")}:
        op.add_column("users", sa.Column("bot_blocked_at", sa.DateTime(timezone=True)))
    for model in (StoreOperationsSettings, GoogleIdentity, SupportTicket, SupportReply, UserTheme):
        model.__table__.create(bind, checkfirst=True)


def downgrade():
    for table in ("user_themes", "support_replies", "support_tickets", "google_identities", "store_operations_settings"):
        op.drop_table(table)
    op.drop_column("users", "bot_blocked_at")
