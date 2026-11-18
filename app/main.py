from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.config import settings
from app.web.routes import router as api_router
from app.bot.runtime import bot, dispatcher, setup_bot

@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.telegram_bot_token:
        await setup_bot()
        if settings.telegram_use_webhook and settings.telegram_webhook_url:
            await bot.set_webhook(
                settings.telegram_webhook_url,
                secret_token=settings.telegram_webhook_secret or None,
                drop_pending_updates=False,
            )
        elif not settings.telegram_use_webhook:
            await dispatcher.start_polling(bot)
    yield
    if bot:
        await bot.session.close()

app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.include_router(api_router)

@app.get("/health")
async def health():
    return {"status": "ok", "service": "remnaum"}
