"""Add owner-managed account state and retain ledgers after account deletion."""
from alembic import op
import sqlalchemy as sa

revision = "0003_customer_management"
down_revision = "0002_tariff_plan_options"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    names = {col["name"] for col in inspector.get_columns("users")}
    for column in (
        sa.Column("display_name", sa.String(80)),
        sa.Column("blocked_at", sa.DateTime(timezone=True)),
    ):
        if column.name not in names:
            op.add_column("users", column)
    op.alter_column("payments", "user_id", nullable=True)
    op.alter_column("referral_rewards", "invited_id", nullable=True)


def downgrade():
    # A downgrade cannot restore ownership that an administrator erased.
    connection = op.get_bind()
    for table, column in (("payments", "user_id"), ("referral_rewards", "invited_id")):
        if connection.execute(sa.text(f"SELECT 1 FROM {table} WHERE {column} IS NULL LIMIT 1")).first():
            raise RuntimeError("Cannot downgrade a database with anonymized ledger entries")
    op.alter_column("referral_rewards", "invited_id", nullable=False)
    op.alter_column("payments", "user_id", nullable=False)
    op.drop_column("users", "blocked_at")
    op.drop_column("users", "display_name")
