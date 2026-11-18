"""Resolved BotFather identity, cached for menus and referral links.

Always fall back to the configured username during a Telegram API outage.
"""
from app.config import settings

_live_username = ""
_live_display_name = ""


def remember_bot(username: str | None, display_name: str | None = None) -> None:
    global _live_username, _live_display_name
    if username and username.lstrip("@").replace("_", "").isalnum():
        _live_username = username.lstrip("@")
    if display_name:
        _live_display_name = display_name[:80]


def bot_username() -> str:
    return _live_username or settings.telegram_bot_username.lstrip("@")


def bot_display_name() -> str:
    return _live_display_name
