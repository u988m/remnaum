import asyncio
import logging
from contextlib import suppress
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from redis import asyncio as aioredis
from sqlalchemy import text
from fastapi.staticfiles import StaticFiles

from app.bot.runtime import bot, dispatcher, setup_bot
from app.config import settings
from app.version import __version__
from app.db.session import engine, initialize_schema
from app.services.squads import refresh_catalog
from app.services.payment_recovery import run_payment_recovery_forever
from app.services.subscription_notifications import run_subscription_notifications
from app.services.legacy_settings import migrate_legacy_payment_settings
from app.web.routes import router as api_router
from app.web.storefront import router as store_router

log = logging.getLogger(__name__)


async def _register_telegram_webhook() -> None:
    """Retry registration while DNS / Caddy TLS is being provisioned."""
    while True:
        try:
            await bot.set_webhook(
                settings.telegram_webhook_url,
                secret_token=settings.telegram_webhook_secret,
                drop_pending_updates=False,
                allowed_updates=dispatcher.resolve_used_update_types(),
            )
            log.info("Telegram webhook registered")
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("Could not register Telegram webhook yet: %s", type(exc).__name__)
            await asyncio.sleep(30)


async def _run_telegram() -> None:
    await setup_bot()
    if settings.telegram_use_webhook:
        await _register_telegram_webhook()
        return
    while True:
        try:
            await bot.delete_webhook(drop_pending_updates=False)
            await dispatcher.start_polling(
                bot, handle_signals=False, close_bot_session=False, tasks_shutdown_timeout=5,
            )
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("Telegram polling will retry: %s", type(exc).__name__)
            await asyncio.sleep(30)


async def _sync_squads_periodically():
    while True:
        await asyncio.sleep(900)
        try:
            await refresh_catalog()
        except Exception:
            log.exception("Could not persist the Internal Squads catalogue")


@asynccontextmanager
async def lifespan(_: FastAPI):
    async with engine.begin() as connection:
        await initialize_schema(connection)
    await refresh_catalog()
    await migrate_legacy_payment_settings()

    telegram_task = None
    notification_task = None
    if bot is not None:
        if settings.telegram_use_webhook and not (settings.telegram_webhook_url and settings.telegram_webhook_secret):
            raise RuntimeError("TELEGRAM_WEBHOOK_URL and TELEGRAM_WEBHOOK_SECRET are required in webhook mode")
        telegram_task = asyncio.create_task(_run_telegram())
        notification_task = asyncio.create_task(run_subscription_notifications(bot))
    sync_task = asyncio.create_task(_sync_squads_periodically())
    payment_recovery_task = asyncio.create_task(run_payment_recovery_forever())
    try:
        yield
    finally:
        if telegram_task and not settings.telegram_use_webhook:
            with suppress(RuntimeError, TimeoutError):
                await asyncio.wait_for(dispatcher.stop_polling(), timeout=10)
        tasks = [task for task in (telegram_task, notification_task, sync_task, payment_recovery_task) if task]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if bot:
            await bot.session.close()
        await engine.dispose()


app = FastAPI(title=settings.app_name, version=__version__, lifespan=lifespan)
app.include_router(api_router)
app.include_router(store_router)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "web" / "static"), name="static")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "remnaum"}


@app.get("/ready")
async def readiness():
    """Readiness is separate from liveness: do not restart on transient DB outages."""
    try:
        async with asyncio.timeout(3):
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
            redis_client = aioredis.from_url(settings.redis_url)
            try:
                await redis_client.ping()
            finally:
                await redis_client.aclose()
    except Exception as exc:
        log.warning("Readiness check failed (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="Dependencies unavailable") from exc
    return {"status": "ready", "service": "remnaum"}
