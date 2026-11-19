"""Keep customer-defined device names in Remnaum."""
from alembic import op
import sqlalchemy as sa

revision = "0005_device_aliases"
down_revision = "0004_shop_presentation"
branch_labels = None
depends_on = None


def upgrade():
    if not sa.inspect(op.get_bind()).has_table("device_aliases"):
        op.create_table(
            "device_aliases",
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("device_id", sa.String(32), primary_key=True),
            sa.Column("name", sa.String(48), nullable=False),
        )


def downgrade():
    op.drop_table("device_aliases")
