import asyncio
import logging
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.bot.runtime import bot, dispatcher, setup_bot
from app.config import settings
from app.db.migrate import upgrade_database
from app.db.session import engine
from app.services.legacy_settings import migrate_legacy_payment_settings
from app.services.payment_activation import run_activation_worker
from app.services.renewal_reminders import run_renewal_reminders
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


@asynccontextmanager
async def lifespan(_: FastAPI):
    tasks = []
    try:
        async with engine.begin() as connection:
            await connection.run_sync(upgrade_database)
        await migrate_legacy_payment_settings()

        if settings.telegram_bot_token:
            await setup_bot()
            if settings.telegram_use_webhook and settings.telegram_webhook_url:
                if not settings.telegram_webhook_secret:
                    raise RuntimeError("TELEGRAM_WEBHOOK_SECRET is required in webhook mode")
                tasks.append(asyncio.create_task(_register_telegram_webhook()))
            elif not settings.telegram_use_webhook:
                tasks.append(asyncio.create_task(dispatcher.start_polling(bot)))
        tasks.append(asyncio.create_task(run_activation_worker()))
        if bot is not None and settings.renewal_reminder_days:
            tasks.append(asyncio.create_task(run_renewal_reminders(bot, settings.renewal_reminder_days)))
        yield
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if bot:
            await bot.session.close()
        await engine.dispose()


app = FastAPI(title=settings.app_name, version="1.0.0-rc.8", lifespan=lifespan)
app.include_router(api_router)
app.include_router(store_router)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "web" / "static"), name="static")


@app.get("/health")
async def health():
    try:
        async with asyncio.timeout(3), engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except (SQLAlchemyError, OSError, TimeoutError):
        return JSONResponse({"status": "unavailable", "service": "remnaum"}, status_code=503)
    return {"status": "ok", "service": "remnaum"}
