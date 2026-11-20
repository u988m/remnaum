"""Inline actions for release announcements."""
from aiogram.types import InlineKeyboardMarkup

from app.bot.buttons import button
from app.version import RELEASE_REPOSITORY

CLOSE_RELEASE = "release:close"


def release_keyboard(tag: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            button("release", url=f"https://github.com/{RELEASE_REPOSITORY}/releases/tag/{tag}"),
            button("community", url="https://t.me/remnaum"),
        ],
        [button("dismiss", callback_data=CLOSE_RELEASE)],
    ])
