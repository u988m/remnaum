import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

from app.config import settings


def validate_telegram_init_data(init_data: str, max_age_seconds: int = 86400) -> dict:
    if not settings.telegram_bot_token:
        raise ValueError("Telegram bot is not configured")
    if not init_data or len(init_data) > 16_384:
        raise ValueError("Invalid Telegram init data")
    try:
        values = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
    except ValueError as exc:
        raise ValueError("Invalid Telegram init data") from exc
    received_hash = values.pop("hash", None)
    if not received_hash:
        raise ValueError("Telegram init data signature is missing")
    check_string = "\n".join(f"{key}={value}" for key, value in sorted(values.items()))
    secret_key = hmac.new(b"WebAppData", settings.telegram_bot_token.encode(), hashlib.sha256).digest()
    calculated_hash = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated_hash, received_hash):
        raise ValueError("Telegram init data signature is invalid")
    try:
        auth_date = int(values["auth_date"])
        user = json.loads(values["user"])
        telegram_id = int(user["id"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("Telegram init data is incomplete") from exc
    now = int(time.time())
    if auth_date > now + 60 or now - auth_date > max_age_seconds:
        raise ValueError("Telegram session has expired; reopen the Mini App")
    if telegram_id < 1:
        raise ValueError("Invalid Telegram user")
    return user
