import logging
from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand, MenuButtonWebApp, WebAppInfo
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from app.config import settings
from app.bot.handlers import router

bot = Bot(settings.telegram_bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML)) if settings.telegram_bot_token else None
dispatcher = Dispatcher()

async def setup_bot() -> None:
    dispatcher.include_router(router)
    if bot is not None:
        try:
            await bot.set_my_commands([
            BotCommand(command="start", description="Главное меню"),
            BotCommand(command="admin", description="Панель владельца"),
            BotCommand(command="help", description="Помощь"),
            BotCommand(command="terms", description="Условия покупки"),
            BotCommand(command="paysupport", description="Поддержка оплаты"),
            ])
        except Exception as exc:
            logging.getLogger(__name__).warning("Telegram command registration failed: %s", exc)
        if settings.webapp_url.startswith("https://"):
            try:
                await bot.set_chat_menu_button(
                    menu_button=MenuButtonWebApp(
                        text="Открыть магазин",
                        web_app=WebAppInfo(url=settings.webapp_url),
                    )
                )
            except Exception as exc:
                logging.getLogger(__name__).warning("Telegram menu registration failed: %s", exc)
