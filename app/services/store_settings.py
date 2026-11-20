import base64
import hashlib
import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.dialects.postgresql import insert

from app.config import settings
from app.db.models import StorePaymentSettings
from app.db.session import SessionLocal

DEFAULT_METHODS = {
    "telegram_stars": True, "yookassa": False, "yoomoney": False, "cryptopay": False, "platega": False,
}
REQUIRED_CREDENTIALS = {
    "yookassa": ("shop_id", "secret_key"),
    "yoomoney": ("receiver", "notification_secret"),
    "cryptopay": ("api_token",),
    "platega": ("merchant_id", "secret_key"),
}
PROVIDER_LABELS = {"yookassa": "ЮKassa", "yoomoney": "ЮMoney", "cryptopay": "Crypto Pay", "platega": "Platega"}
PAYMENT_LABELS = {"telegram_stars": "Telegram Stars", **PROVIDER_LABELS}


def payment_presentation(entries: list[dict] | None = None) -> list[dict[str, str]]:
    labels = {}
    for item in entries or []:
        provider, label = item["provider"], item["label"].strip()
        if provider not in PAYMENT_LABELS or provider in labels or len(label) > 24:
            raise ValueError("Проверьте названия и порядок способов оплаты.")
        labels[provider] = label or PAYMENT_LABELS[provider]
    return [{"provider": key, "label": label} for key, label in (labels | {
        key: label for key, label in PAYMENT_LABELS.items() if key not in labels
    }).items()]


def rubles_first(presentation: list[dict[str, str]]) -> list[dict[str, str]]:
    """Keep the configured order of ruble providers and show Stars after them."""
    return sorted(presentation, key=lambda item: item["provider"] == "telegram_stars")


async def get_payment_presentation() -> list[dict[str, str]]:
    async with SessionLocal() as session:
        row = await session.get(StorePaymentSettings, 1)
        return payment_presentation(row.presentation if row else None)


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


async def update_store_payment_settings(methods: dict[str, bool], credentials: dict[str, dict[str, str]], *,
                                        presentation: list[dict] | None = None):
    if methods.keys() - DEFAULT_METHODS.keys() or credentials.keys() - REQUIRED_CREDENTIALS.keys():
        raise ValueError("Неизвестный способ оплаты")
    if presentation is not None:
        presentation = payment_presentation(presentation)
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
        if presentation is not None:
            row.presentation = presentation
        result["presentation"] = payment_presentation(row.presentation)
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
