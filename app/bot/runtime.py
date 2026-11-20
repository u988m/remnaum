import logging
from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand, BotCommandScopeDefault, BotCommandScopeChat, MenuButtonWebApp, WebAppInfo
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from app.config import settings
from app.services.bot_identity import remember_bot
from app.bot.handlers import router
from app.bot.buttons import style_buttons
from app.bot.notifications import router as notice_router

bot = Bot(settings.telegram_bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML)) if settings.telegram_bot_token else None
if bot is not None:
    bot.session.middleware(style_buttons)
dispatcher = Dispatcher()
dispatcher.include_routers(notice_router, router)

async def setup_bot() -> None:
    if bot is not None:
        try:
            identity = await bot.get_me()
            remember_bot(identity.username, identity.first_name)
            logging.getLogger(__name__).info("BotFather identity discovered: @%s", identity.username)
        except Exception as exc:
            logging.getLogger(__name__).warning("Bot identity lookup failed: %s", type(exc).__name__)
        public_commands = [
            BotCommand(command="start", description="Главное меню"),
            BotCommand(command="help", description="Помощь"),
            BotCommand(command="promo", description="Активировать промокод"),
            BotCommand(command="terms", description="Условия покупки"),
            BotCommand(command="paysupport", description="Поддержка оплаты"),
        ]
        try:
            # Ordinary users never see an admin command in their Telegram menu.
            await bot.set_my_commands(public_commands, scope=BotCommandScopeDefault())
            for admin_id in sorted(settings.admin_id_set):
                try:
                    await bot.delete_my_commands(scope=BotCommandScopeChat(chat_id=admin_id))
                except Exception as exc:
                    logging.getLogger(__name__).warning(
                        "Could not register owner commands for %s: %s", admin_id, type(exc).__name__,
                    )
        except Exception as exc:
            logging.getLogger(__name__).warning("Telegram command registration failed: %s", type(exc).__name__)
        if settings.webapp_root_url.startswith("https://"):
            try:
                await bot.set_chat_menu_button(
                    menu_button=MenuButtonWebApp(
                        text="Mini App",
                        web_app=WebAppInfo(url=settings.webapp_root_url),
                    )
                )
            except Exception as exc:
                logging.getLogger(__name__).warning("Telegram menu registration failed: %s", type(exc).__name__)
