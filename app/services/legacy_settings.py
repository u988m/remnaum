"""One-time import for users upgrading from old .env-based payment settings.

Import only if the encrypted DB row has never been configured. Never print
secrets or overwrite anything saved through the admin dashboard.
"""
from app.config import settings
from app.db.models import StorePaymentSettings
from app.db.session import SessionLocal
from app.services.store_settings import update_store_payment_settings


def legacy_credentials() -> dict[str, dict[str, str]]:
    data = {}
    if settings.yookassa_shop_id or settings.yookassa_secret_key:
        data["yookassa"] = {
            "shop_id": settings.yookassa_shop_id,
            "secret_key": settings.yookassa_secret_key,
        }
    if settings.yoomoney_receiver or settings.yoomoney_notification_secret:
        data["yoomoney"] = {
            "receiver": settings.yoomoney_receiver,
            "notification_secret": settings.yoomoney_notification_secret,
        }
    if settings.cryptopay_api_token:
        data["cryptopay"] = {"api_token": settings.cryptopay_api_token}
    return data


async def migrate_legacy_payment_settings() -> bool:
    credentials = legacy_credentials()
    if not credentials:
        return False
    async with SessionLocal() as session:
        if await session.get(StorePaymentSettings, 1) is not None:
            return False
    # Enabled-method flags are not inferred from old .env: owner opts in via admin.
    await update_store_payment_settings({}, credentials)
    return True
