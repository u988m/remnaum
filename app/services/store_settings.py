import base64
import hashlib
import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.dialects.postgresql import insert

from app.config import settings
from app.db.models import StorePaymentSettings
from app.db.session import SessionLocal

DEFAULT_METHODS = {"telegram_stars": True, "yookassa": False, "yoomoney": False, "cryptopay": False}
REQUIRED_CREDENTIALS = {
    "yookassa": ("shop_id", "secret_key"),
    "yoomoney": ("receiver", "notification_secret"),
    "cryptopay": ("api_token",),
}
PROVIDER_LABELS = {"yookassa": "ЮKassa", "yoomoney": "ЮMoney", "cryptopay": "Crypto Pay"}


def _cipher() -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(settings.app_secret_key.encode()).digest())
    return Fernet(key)


def encrypt_credentials(credentials: dict[str, Any]) -> str:
    raw = json.dumps(credentials, separators=(",", ":"), sort_keys=True).encode()
    return _cipher().encrypt(raw).decode()


def decrypt_credentials(token: str) -> dict[str, Any]:
    if not token:
        return {}
    try:
        return json.loads(_cipher().decrypt(token.encode()))
    except (InvalidToken, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("Payment credentials cannot be decrypted; check APP_SECRET_KEY") from exc


async def get_store_payment_settings():
    async with SessionLocal() as session:
        row = await session.get(StorePaymentSettings, 1)
        if row is None:
            return DEFAULT_METHODS.copy(), {}
        methods = DEFAULT_METHODS | {k: bool(v) for k, v in (row.methods or {}).items() if k in DEFAULT_METHODS}
        try:
            return methods, decrypt_credentials(row.encrypted_credentials)
        except RuntimeError:
            return methods, {}


async def is_payment_method_enabled(provider: str) -> bool:
    async with SessionLocal() as session:
        row = await session.get(StorePaymentSettings, 1)
        methods = (row.methods or {}) if row is not None else {}
        return bool(methods.get(provider, DEFAULT_METHODS.get(provider, False)))


async def update_store_payment_settings(methods: dict[str, bool], credentials: dict[str, dict[str, str]]):
    if methods.keys() - DEFAULT_METHODS.keys() or credentials.keys() - REQUIRED_CREDENTIALS.keys():
        raise ValueError("Неизвестный способ оплаты")
    affected = (methods.keys() | credentials.keys()) & REQUIRED_CREDENTIALS.keys()
    async with SessionLocal() as session:
        await session.execute(insert(StorePaymentSettings).values(
            id=1, methods=DEFAULT_METHODS, encrypted_credentials="",
        ).on_conflict_do_nothing(index_elements=[StorePaymentSettings.id]))
        row = await session.get(StorePaymentSettings, 1, with_for_update=True)
        current = DEFAULT_METHODS | (row.methods or {}) | methods
        try:
            stored = decrypt_credentials(row.encrypted_credentials)
        except RuntimeError as exc:
            if credentials or any(current[name] for name in affected):
                raise ValueError("Не удалось прочитать сохранённые реквизиты. Проверьте APP_SECRET_KEY на сервере.") from exc
            stored = {}
        for provider, values in credentials.items():
            changes = {field: values[field].strip() for field in REQUIRED_CREDENTIALS[provider]
                       if values.get(field, "").strip()}
            if changes:
                stored[provider] = stored.get(provider, {}) | changes
        result = public_payment_settings(current, stored)
        for provider in affected:
            if current[provider] and not result["configured"][provider]:
                raise ValueError(f"Заполните реквизиты {PROVIDER_LABELS[provider]} перед включением.")
        row.methods = current
        if credentials:
            row.encrypted_credentials = encrypt_credentials(stored)
        await session.commit()
    return result


def public_payment_settings(methods, credentials):
    return {
        "methods": methods,
        "configured": {
            "telegram_stars": True,
            **{provider: all(credentials.get(provider, {}).get(field) for field in fields)
               for provider, fields in REQUIRED_CREDENTIALS.items()},
        },
    }
