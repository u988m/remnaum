"""Signed Telegram Login Widget callback and short-lived browser session.

This is for a separate HTTPS website, never for the in-Telegram Mini App.
"""
from __future__ import annotations
import base64
import binascii
import hashlib
import hmac
import time


def verify_telegram_login(data: dict[str, str], bot_token: str, now: int | None = None) -> dict:
    if not bot_token:
        raise ValueError("Telegram login is not configured")
    values = {str(k): str(v) for k, v in data.items()}
    signature = values.pop("hash", "")
    if len(signature) != 64:
        raise ValueError("Missing Telegram login signature")
    try:
        user_id = int(values.get("id", ""))
        auth_date = int(values.get("auth_date", ""))
    except ValueError as exc:
        raise ValueError("Invalid Telegram user identity") from exc
    if user_id < 1 or auth_date < 1:
        raise ValueError("Invalid Telegram user identity")
    current = int(now if now is not None else time.time())
    if not 0 <= current - auth_date <= 600:
        raise ValueError("Telegram login has expired")
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(values.items()))
    key = hashlib.sha256(bot_token.encode()).digest()
    expected = hmac.new(key, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise ValueError("Invalid Telegram login signature")
    return {"id": user_id, "username": values.get("username", ""), "first_name": values.get("first_name", "")}


def issue_store_session(telegram_id: int, secret: str, now: int | None = None) -> str:
    if telegram_id < 1 or len(secret) < 24:
        raise ValueError("Invalid session configuration")
    expires = int(now if now is not None else time.time()) + 12 * 3600
    payload = f"{telegram_id}:{expires}"
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{payload}:{signature}".encode()).decode().rstrip("=")


def verify_store_session(cookie: str | None, secret: str, now: int | None = None) -> int:
    if not cookie or len(cookie) > 256 or len(secret) < 24:
        raise ValueError("Unauthenticated")
    try:
        raw = base64.urlsafe_b64decode(cookie + "=" * (-len(cookie) % 4)).decode()
        telegram_id_raw, expiry_raw, signature = raw.split(":")
        telegram_id = int(telegram_id_raw)
        expiry = int(expiry_raw)
    except (ValueError, UnicodeError, binascii.Error) as exc:
        raise ValueError("Invalid session") from exc
    current = int(now if now is not None else time.time())
    if telegram_id < 1 or not current <= expiry <= current + 12 * 3600:
        raise ValueError("Session expired")
    payload = f"{telegram_id}:{expiry}"
    expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise ValueError("Invalid session signature")
    return telegram_id
