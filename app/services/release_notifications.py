"""Forward published Remnaum releases to the configured Telegram administrators."""
import asyncio
import hashlib
import hmac
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError
from redis.exceptions import LockError

from app.bot.menu import redis
from app.bot.release_notices import release_keyboard
from app.config import settings
from app.version import RELEASE_NUMBER, RELEASE_URL

log = logging.getLogger(__name__)
POLL_INTERVAL = 90
DELIVERY_RETRY_INTERVAL = 15
_release_ready = asyncio.Event()


@dataclass(frozen=True)
class Release:
    id: int
    tag: str
    published_at: datetime


def verify_signature(body: bytes, signature: str, secret: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return signature.isascii() and hmac.compare_digest(expected, signature)


def parse_release(data: object) -> Release | None:
    if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
        return None
    release_id, tag, published_at = data.get("id"), data.get("tag_name"), data.get("published_at")
    if type(release_id) is not int or release_id <= 0:
        return None
    if not isinstance(tag, str) or len(tag) > 64 or not re.fullmatch(rf"v?{RELEASE_NUMBER}", tag, re.ASCII):
        return None
    if not isinstance(published_at, str):
        return None
    try:
        timestamp = datetime.fromisoformat(published_at)
    except ValueError:
        return None
    if timestamp.tzinfo is None:
        return None
    return Release(release_id, tag, timestamp)


async def enqueue_release(bot_id: int, release: Release) -> bool:
    prefix = f"remnaum:releases:{bot_id}"
    async with redis.lock(prefix + ":enqueue", timeout=10, blocking_timeout=2):
        if await redis.sismember(prefix + ":known", release.id):
            return False
        async with redis.pipeline() as pipe:
            await pipe.sadd(prefix + ":known", release.id).hset(prefix + ":pending", release.id, release.tag).execute()
    _release_ready.set()
    return True


async def _notify(bot: Bot, admin_id: int, tag: str, sent_key: str) -> None:
    try:
        async with asyncio.timeout(8):
            await bot.send_message(
                admin_id, f"🚀 Вышел релиз Remnaum {tag}\n\nДля обновления запустите sudo remnaum и выберите «Обновить».",
                parse_mode=None,
                reply_markup=release_keyboard(tag),
            )
    except (TelegramForbiddenError, TelegramBadRequest):
        log.info("Release %s cannot be delivered to administrator %s", tag, admin_id)
    except (TelegramAPIError, TimeoutError):
        return
    await redis.sadd(sent_key, admin_id)


async def deliver_releases(bot: Bot) -> None:
    prefix = f"remnaum:releases:{bot.id}"
    pending = prefix + ":pending"
    for release_id in await redis.hkeys(pending):
        key = f"{prefix}:{release_id}"
        try:
            async with redis.lock(key + ":delivery", timeout=20, blocking_timeout=0):
                tag = await redis.hget(pending, release_id)
                if tag is None:
                    continue
                sent_key = key + ":sent"
                sent = {int(admin_id) for admin_id in await redis.smembers(sent_key)}
                admins = settings.admin_id_set
                results = await asyncio.gather(
                    *(_notify(bot, admin_id, tag, sent_key) for admin_id in admins - sent),
                    return_exceptions=True,
                )
                for result in results:
                    if isinstance(result, Exception):
                        log.error("Release %s notification failed", tag, exc_info=result)
                if admins <= {int(admin_id) for admin_id in await redis.smembers(sent_key)}:
                    async with redis.pipeline() as pipe:
                        await pipe.hdel(pending, release_id).delete(sent_key).execute()
        except LockError:
            continue


async def _poll_releases(bot_id: int) -> None:
    monitor_key = f"remnaum:releases:{bot_id}:monitor-since"
    started_at = datetime.now(timezone.utc).replace(microsecond=0)
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "Remnaum"}
    async with httpx.AsyncClient(timeout=8) as client:
        while True:
            try:
                await redis.set(monitor_key, started_at.isoformat(), nx=True)
                monitor_since = datetime.fromisoformat(await redis.get(monitor_key))
                response = await client.get(RELEASE_URL, headers=headers)
                if response.status_code != 304:
                    response.raise_for_status()
                    release = parse_release(response.json())
                    if release and release.published_at >= monitor_since:
                        await enqueue_release(bot_id, release)
                    if etag := response.headers.get("ETag"):
                        headers["If-None-Match"] = etag
            except Exception:
                log.warning("Could not check for a published Remnaum release", exc_info=True)
            await asyncio.sleep(POLL_INTERVAL)


async def _send_queued_releases(bot: Bot) -> None:
    while True:
        _release_ready.clear()
        try:
            await deliver_releases(bot)
        except Exception:
            log.exception("Could not deliver queued Remnaum releases")
        try:
            await asyncio.wait_for(_release_ready.wait(), timeout=DELIVERY_RETRY_INTERVAL)
        except TimeoutError:
            pass


async def run_release_notifications(bot: Bot) -> None:
    async with asyncio.TaskGroup() as tasks:
        tasks.create_task(_poll_releases(bot.id))
        tasks.create_task(_send_queued_releases(bot))
