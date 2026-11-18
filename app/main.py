import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.bot.runtime import bot, dispatcher, setup_bot
from app.config import settings
from app.db.base import Base
from app.db.session import engine
from app.web.routes import router as api_router


@asynccontextmanager
async def lifespan(_: FastAPI):
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    polling_task = None
    if settings.telegram_bot_token:
        await setup_bot()
        if settings.telegram_use_webhook and settings.telegram_webhook_url:
            await bot.set_webhook(
                settings.telegram_webhook_url,
                secret_token=settings.telegram_webhook_secret or None,
                drop_pending_updates=False,
            )
        elif not settings.telegram_use_webhook:
            polling_task = asyncio.create_task(dispatcher.start_polling(bot))
    yield
    if polling_task:
        polling_task.cancel()
        try:
            await polling_task
        except asyncio.CancelledError:
            pass
    if bot:
        await bot.session.close()
    await engine.dispose()


app = FastAPI(title=settings.app_name, version="0.2.0", lifespan=lifespan)
app.include_router(api_router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "remnaum"}
