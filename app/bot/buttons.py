"""Stable identifiers and Telegram styles for the bot's inline buttons."""
import asyncio
import logging
import unicodedata

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator

from app.services.store_operations import get_operations

log = logging.getLogger(__name__)

# Section, default label (without its icon), default icon.
BUTTONS = {
    "miniapp": ("Главное меню", "Mini App", "✨"),
    "profile": ("Главное меню", "Профиль", "👤"),
    "subscription": ("Главное меню", "Подписка", "🔐"),
    "plans": ("Главное меню", "Тарифы", "🛒"),
    "bonuses": ("Главное меню", "Бонусы", "🎁"),
    "purchases": ("Главное меню", "Покупки", "🧾"),
    "support": ("Главное меню", "Поддержка", "💬"),
    "information": ("Главное меню", "Информация", "ℹ️"),
    "home": ("Навигация", "Меню", "←"),
    "back": ("Навигация", "Назад", "←"),
    "cancel": ("Навигация", "Отмена", ""),
    "previous": ("Навигация", "Предыдущая", "‹"),
    "next": ("Навигация", "Следующая", ""),
    "document_terms": ("Документы", "Пользовательское соглашение", ""),
    "document_privacy": ("Документы", "Политика конфиденциальности", ""),
    "invite": ("Бонусы", "Пригласить друга", "🤝"),
    "promo": ("Бонусы", "Ввести промокод", "🎟"),
    "promo_again": ("Бонусы", "Ввести ещё код", "🎟"),
    "trial": ("Бонусы", "Пробный доступ", "🧪"),
    "trial_activate": ("Бонусы", "Активировать пробный доступ", "🧪"),
    "copy_invite": ("Бонусы", "Копировать ссылку", ""),
    "share_invite": ("Бонусы", "Поделиться", ""),
    "plan_item": ("Тарифы и оплата", "Карточка тарифа", ""),
    "device_minus": ("Тарифы и оплата", "Устройство (убрать)", "−"),
    "device_plus": ("Тарифы и оплата", "Устройство (добавить)", "+"),
    "checkout": ("Тарифы и оплата", "Выбрать оплату", "💳"),
    "terms": ("Тарифы и оплата", "Условия продажи", "📄"),
    "accept_terms": ("Тарифы и оплата", "Принимаю", "✅"),
    "payment_yookassa": ("Тарифы и оплата", "ЮKassa", "💳"),
    "payment_yoomoney": ("Тарифы и оплата", "ЮMoney", "💳"),
    "payment_cryptopay": ("Тарифы и оплата", "Crypto Pay", "💳"),
    "payment_platega": ("Тарифы и оплата", "Platega", "💳"),
    "payment_stars": ("Тарифы и оплата", "Способ оплаты в Stars", "⭐"),
    "pay": ("Тарифы и оплата", "Оплатить", "💳"),
    "pay_stars": ("Тарифы и оплата", "Оплатить ⭐", ""),
    "renew": ("Подписка и устройства", "Продлить", "🔄"),
    "connect": ("Подписка и устройства", "Подключить VPN", "🔗"),
    "instructions": ("Подписка и устройства", "Инструкция", "📖"),
    "devices": ("Подписка и устройства", "Устройства", "📱"),
    "refresh_devices": ("Подписка и устройства", "Обновить", ""),
    "device_item": ("Подписка и устройства", "Карточка устройства", ""),
    "rename_device": ("Подписка и устройства", "Переименовать", "✏️"),
    "remove_device": ("Подписка и устройства", "Удалить", "🗑"),
    "confirm_device": ("Подписка и устройства", "Удалить устройство", ""),
    "back_devices": ("Подписка и устройства", "К устройствам", "←"),
    "tickets": ("Поддержка и канал", "Тикеты поддержки", "🎫"),
    "support_link": ("Поддержка и канал", "Написать в поддержку", "💬"),
    "open_ticket": ("Поддержка и канал", "Открыть тикет", ""),
    "channel": ("Поддержка и канал", "Открыть канал", ""),
    "check_channel": ("Поддержка и канал", "Проверить подписку", ""),
    "release": ("Уведомления", "Открыть релиз", ""),
    "community": ("Уведомления", "Сообщество", ""),
    "dismiss": ("Уведомления", "Закрыть", ""),
}


class ButtonStyle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    emoji: str = Field(max_length=24)
    style: Literal["", "primary", "success", "danger"] = ""

    @field_validator("emoji")
    @classmethod
    def validate_emoji(cls, value):
        value = value.strip()
        if not value:
            return ""
        symbol = any(unicodedata.category(char) in {"So", "Sk", "Sm", "Pd"} or char == "ℹ" for char in value)
        if not symbol and not value.endswith("\u20e3") and value not in {"‹", "›"}:
            raise ValueError("Укажите эмодзи.")
        if not all(
            unicodedata.category(char) in {"So", "Sk", "Sm", "Pd"}
            or char in "\u200d\ufe0e\ufe0f\u20e30123456789#*‹›ℹ"
            or 0xE0020 <= ord(char) <= 0xE007F
            for char in value
        ):
            raise ValueError("Укажите эмодзи.")
        return value


class MenuButton(InlineKeyboardButton):
    _key: str = PrivateAttr()
    _label: str = PrivateAttr()


def button_text(emoji: str, label: str) -> str:
    return f"{emoji} {label}" if emoji else label


def button(key: str, label: str | None = None, **action) -> InlineKeyboardButton:
    _, default_label, emoji = BUTTONS[key]
    label = label if label is not None else default_label
    result = MenuButton(text=button_text(emoji, label), **action)
    result._key = key
    result._label = label
    return result


async def style_buttons(make_request, bot, method):
    markup = getattr(method, "reply_markup", None)
    if isinstance(markup, InlineKeyboardMarkup) and any(
        isinstance(item, MenuButton) for row in markup.inline_keyboard for item in row
    ):
        try:
            async with asyncio.timeout(2):
                styles = (await get_operations())["bot_buttons"]
        except Exception:
            log.warning("Could not load Telegram button styles")
            styles = {}
        if styles:
            rows = []
            for row in markup.inline_keyboard:
                styled = []
                for item in row:
                    if isinstance(item, MenuButton) and (values := styles.get(item._key)):
                        item = item.model_copy(update={
                            "text": button_text(values["emoji"], item._label),
                            "style": values["style"] or None,
                        })
                    styled.append(item)
                rows.append(styled)
            method = method.model_copy(update={"reply_markup": InlineKeyboardMarkup(inline_keyboard=rows)})
    return await make_request(bot, method)
