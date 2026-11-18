import base64
import hashlib
import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from app.config import settings
from app.db.models import StorePaymentSettings
from app.db.session import SessionLocal

PROVIDERS = {"telegram_stars", "yookassa", "yoomoney", "cryptopay"}
DEFAULT_METHODS = {"telegram_stars": True, "yookassa": False, "yoomoney": False, "cryptopay": False}


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
    from sqlalchemy import select
    async with SessionLocal() as session:
        row = await session.get(StorePaymentSettings, 1)
        if row is None:
            return DEFAULT_METHODS.copy(), {}
        methods = DEFAULT_METHODS | {k: bool(v) for k, v in (row.methods or {}).items() if k in PROVIDERS}
        return methods, decrypt_credentials(row.encrypted_credentials)


async def is_payment_method_enabled(provider: str) -> bool:
    methods, _ = await get_store_payment_settings()
    return bool(methods.get(provider, False))


async def update_store_payment_settings(methods: dict[str, bool], credentials: dict[str, dict[str, str]]):
    from sqlalchemy import select
    if set(methods) - PROVIDERS or set(credentials) - (PROVIDERS - {"telegram_stars"}):
        raise ValueError("Unknown payment provider")
    async with SessionLocal() as session:
        row = await session.get(StorePaymentSettings, 1)
        stored = decrypt_credentials(row.encrypted_credentials) if row else {}
        for provider, values in credentials.items():
            if values:
                stored[provider] = {str(k): str(v) for k, v in values.items() if v}
        current = (row.methods if row else DEFAULT_METHODS.copy()) | {k: bool(v) for k, v in methods.items()}
        for provider in PROVIDERS - {"telegram_stars"}:
            if current.get(provider) and not stored.get(provider):
                raise ValueError(f"Credentials are required before enabling {provider}")
        if row is None:
            row = StorePaymentSettings(id=1)
            session.add(row)
        row.methods = current
        row.encrypted_credentials = encrypt_credentials(stored)
        await session.commit()
    return public_payment_settings(current, stored)


def public_payment_settings(methods, credentials):
    return {
        "methods": methods,
        "configured": {p: (p == "telegram_stars" or bool(credentials.get(p))) for p in PROVIDERS},
    }
