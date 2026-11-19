"""Settings for browser sign-in, Telegram membership and store operations."""
from sqlalchemy.dialects.postgresql import insert

from app.config import settings
from app.db.models import StoreOperationsSettings
from app.db.session import SessionLocal
from app.services.store_settings import decrypt_credentials, encrypt_credentials


DEFAULTS = {
    "telegram_login": True,
    "google_login": False,
    "google_client_id": "",
    "required_channel": "",
    "channel_url": "",
    "extra_devices_max": 1000,
    "panel_url": "",
}


async def get_operations(*, session=None, credentials=False):
    if session is None:
        async with SessionLocal() as session:
            return await get_operations(session=session, credentials=credentials)
    row = await session.get(StoreOperationsSettings, 1)
    values = DEFAULTS | (row.data if row else {})
    if credentials:
        values.update(decrypt_credentials(row.encrypted_credentials) if row else {})
    return values


async def save_operations(values, secrets):
    async with SessionLocal.begin() as session:
        await session.execute(insert(StoreOperationsSettings).values(
            id=1, data={}, encrypted_credentials="",
        ).on_conflict_do_nothing(index_elements=[StoreOperationsSettings.id]))
        row = await session.get(StoreOperationsSettings, 1, with_for_update=True)
        stored = decrypt_credentials(row.encrypted_credentials)
        current = DEFAULTS | row.data | values
        if current["panel_url"] != (row.data.get("panel_url") or ""):
            # A token may only follow a URL change when explicitly supplied again.
            if current["panel_url"] and not secrets.get("panel_token"):
                raise ValueError("При смене URL укажите API-токен панели.")
            stored.pop("panel_token", None)
        stored.update({key: value for key, value in secrets.items() if value})
        if current["google_login"] and not (current["google_client_id"] and stored.get("google_client_secret")):
            raise ValueError("Укажите Google Client ID и Client Secret.")
        if stored.get("panel_token") and not (current["panel_url"] or settings.remnawave_base_url):
            raise ValueError("Укажите URL панели.")
        row.data = current
        row.encrypted_credentials = encrypt_credentials(stored)
    return current
