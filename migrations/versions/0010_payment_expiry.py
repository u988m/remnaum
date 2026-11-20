"""Track payment expirations and Telegram notices."""
from alembic import op
import sqlalchemy as sa

revision = "0010_payment_expiry"
down_revision = "0009_ticket_activity"
branch_labels = None
depends_on = None


def upgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("payments")}
    for name in ("expired_at", "expiry_notice_completed_at"):
        if name not in columns:
            op.add_column("payments", sa.Column(name, sa.DateTime(timezone=True)))
    # Orders already past the new deadline predate the notification policy.
    op.execute("""
        UPDATE payments
        SET status = 'cancelled', expired_at = now(), expiry_notice_completed_at = now()
        WHERE status = 'pending' AND created_at <= now() - interval '30 minutes'
    """)


def downgrade():
    op.drop_column("payments", "expiry_notice_completed_at")
    op.drop_column("payments", "expired_at")
