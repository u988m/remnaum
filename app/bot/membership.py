"""On-demand membership checks and immediate Telegram leave handling."""
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


SUBSCRIBE_TEXT = "Подпишитесь на канал магазина и нажмите «Проверить подписку»."


def active_member(member):
    return member.status in {"creator", "administrator", "member"} or (
        member.status == "restricted" and member.is_member
    )


async def membership_required(bot, telegram_id, policy):
    channel = policy["required_channel"]
    if not channel:
        return False
    if bot is None:
        return True
    target = int(channel) if channel.startswith("-") else channel
    try:
        return not active_member(await bot.get_chat_member(target, telegram_id))
    except TelegramAPIError:
        return True


def subscription_keyboard(policy, inviter=None):
    callback = f"channel:check:{inviter}" if inviter else "channel:check"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Открыть канал", url=policy["channel_url"])],
        [InlineKeyboardButton(text="Проверить подписку", callback_data=callback)],
    ])


async def send_subscription_prompt(bot, telegram_id, policy, *, inviter=None):
    from app.bot.menu import remember_menu

    markup = subscription_keyboard(policy, inviter)
    message = await bot.send_message(telegram_id, SUBSCRIBE_TEXT, reply_markup=markup)
    await remember_menu(message, markup)
