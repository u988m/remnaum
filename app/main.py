import asyncio
import logging
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.bot.runtime import bot, dispatcher, setup_bot
from app.config import settings
from app.db.session import engine, initialize_schema
from app.services.squads import refresh_catalog
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
            )
            log.info("Telegram webhook registered")
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("Could not register Telegram webhook yet: %s", exc)
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

    polling_task = None
    webhook_task = None
    if settings.telegram_bot_token:
        await setup_bot()
        if settings.telegram_use_webhook and settings.telegram_webhook_url:
            if not settings.telegram_webhook_secret:
                raise RuntimeError("TELEGRAM_WEBHOOK_SECRET is required in webhook mode")
            webhook_task = asyncio.create_task(_register_telegram_webhook())
        elif not settings.telegram_use_webhook:
            polling_task = asyncio.create_task(dispatcher.start_polling(bot))
    sync_task = asyncio.create_task(_sync_squads_periodically())
    try:
        yield
    finally:
        for task in (polling_task, webhook_task, sync_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        if bot:
            await bot.session.close()
        await engine.dispose()


app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)
app.include_router(api_router)
app.include_router(store_router)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "web" / "static"), name="static")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "remnaum"}
