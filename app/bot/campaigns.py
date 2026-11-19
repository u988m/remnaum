"""Send admin broadcasts and refresh tracked private-chat menus."""
import asyncio
import logging

from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import FSInputFile
from redis.exceptions import LockError
from sqlalchemy import select

from app.bot.handlers import _menu_caption, home_text, main_menu
from app.bot.membership import membership_required, send_subscription_prompt
from app.bot.menu import menu_session, redis, remember_menu
from app.db.models import User
from app.db.session import SessionLocal
from app.services.store_documents import list_document_kinds
from app.services.store_media import welcome_image_path
from app.services.store_operations import get_operations
from app.services.store_profile import get_store_profile
from app.services.users import set_bot_blocked

logger = logging.getLogger(__name__)


async def refresh_menu(bot, telegram_id, profile, keyboard, policy):
    async with menu_session(bot, telegram_id, wait=3):
        if await membership_required(bot, telegram_id, policy):
            await send_subscription_prompt(bot, telegram_id, policy)
            return
        text = await home_text(telegram_id, profile)
        if image := welcome_image_path():
            sent = await bot.send_photo(telegram_id, FSInputFile(image), caption=_menu_caption(text), reply_markup=keyboard)
        else:
            sent = await bot.send_message(telegram_id, text, reply_markup=keyboard)
        await remember_menu(sent, keyboard)


async def run_campaign(bot, admin_id, text, lock):
    counts = {"sent": 0, "blocked": 0, "failed": 0}
    try:
        profile = await get_store_profile()
        policy = await get_operations()
        keyboard = main_menu(has_documents=bool(await list_document_kinds()))
        cursor = 0
        while True:
            await lock.reacquire()
            async with SessionLocal() as session:
                recipients = (await session.execute(select(User.id, User.telegram_id).where(
                    User.id > cursor, User.blocked_at.is_(None), User.bot_blocked_at.is_(None),
                ).order_by(User.id).limit(100))).all()
            if not recipients:
                break
            for cursor, telegram_id in recipients:
                for attempt in range(3):
                    try:
                        if text is None:
                            await refresh_menu(bot, telegram_id, profile, keyboard, policy)
                        else:
                            await bot.send_message(telegram_id, text, parse_mode=None)
                    except TelegramRetryAfter as exc:
                        await asyncio.sleep(exc.retry_after)
                        await lock.reacquire()
                        if attempt == 2:
                            counts["failed"] += 1
                        continue
                    except TelegramForbiddenError:
                        await set_bot_blocked(telegram_id, True)
                        counts["blocked"] += 1
                    except (TelegramAPIError, LockError):
                        counts["failed"] += 1
                    else:
                        counts["sent"] += 1
                    break
                await asyncio.sleep(0.05)
    except Exception:
        logger.exception("Telegram campaign interrupted")
        counts["failed"] += 1
    finally:
        try:
            await bot.send_message(admin_id, (
                f"Рассылка завершена. Доставлено: {counts['sent']}. "
                f"Новые блокировки: {counts['blocked']}. Ошибки: {counts['failed']}."
            ))
        except TelegramAPIError:
            logger.info("Could not send Telegram campaign summary")
        await lock.release()


async def campaign_lock():
    lock = redis.lock("remnaum:telegram-campaign", timeout=1800)
    return lock if await lock.acquire(blocking=False) else None
