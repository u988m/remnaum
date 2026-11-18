"""Verify outgoing Remnawave events against the raw request body."""
import hashlib
import hmac
import json


def verify_remnawave_webhook(body: bytes, signature: str | None, secret: str) -> dict:
    if not secret:
        raise ValueError("Remnawave webhook is not configured")
    if not signature or len(body) > 1024 * 1024:
        raise ValueError("Missing signature or oversized payload")
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature.lower(), expected):
        raise ValueError("Invalid Remnawave webhook signature")
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid Remnawave webhook JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("event"), str):
        raise ValueError("Malformed Remnawave webhook payload")
    return payload
