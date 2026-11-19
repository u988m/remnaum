"""Persist Telegram photos and payment method presentation."""
from alembic import op
import sqlalchemy as sa

revision = "0004_shop_presentation"
down_revision = "0003_customer_management"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    for table, column in (
        ("users", sa.Column("telegram_photo_url", sa.Text())),
        ("store_payment_settings", sa.Column("presentation", sa.JSON())),
    ):
        if column.name not in {item["name"] for item in inspector.get_columns(table)}:
            op.add_column(table, column)


def downgrade():
    op.drop_column("store_payment_settings", "presentation")
    op.drop_column("users", "telegram_photo_url")
