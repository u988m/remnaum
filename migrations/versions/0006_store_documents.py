"""Store the documents published in the Telegram bot."""
from alembic import op
import sqlalchemy as sa

revision = "0006_store_documents"
down_revision = "0005_device_aliases"
branch_labels = None
depends_on = None


def upgrade():
    if not sa.inspect(op.get_bind()).has_table("store_documents"):
        op.create_table(
            "store_documents",
            sa.Column("kind", sa.String(16), primary_key=True),
            sa.Column("body", sa.Text(), nullable=False),
        )


def downgrade():
    op.drop_table("store_documents")
