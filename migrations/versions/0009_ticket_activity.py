"""Order ticket history by its latest activity."""
from alembic import op
import sqlalchemy as sa

revision = "0009_ticket_activity"
down_revision = "0008_store_operations"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if "updated_at" not in {column["name"] for column in sa.inspect(bind).get_columns("support_tickets")}:
        op.add_column("support_tickets", sa.Column("updated_at", sa.DateTime(timezone=True)))
        op.execute("""
            UPDATE support_tickets AS ticket
            SET updated_at = COALESCE(
                (SELECT MAX(reply.created_at) FROM support_replies AS reply WHERE reply.ticket_id = ticket.id),
                ticket.created_at
            )
        """)
        op.alter_column("support_tickets", "updated_at", nullable=False, server_default=sa.func.now())


def downgrade():
    op.drop_column("support_tickets", "updated_at")
