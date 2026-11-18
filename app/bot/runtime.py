from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from app.config import settings
from app.bot.handlers import router

bot = Bot(settings.telegram_bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML)) if settings.telegram_bot_token else None
dispatcher = Dispatcher()

async def setup_bot() -> None:
    dispatcher.include_router(router)
