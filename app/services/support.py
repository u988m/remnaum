"""Store ticket conversations and notify participants in Telegram."""
import logging
from urllib.parse import urlencode, urljoin, urlsplit

from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from sqlalchemy import func, or_, select

from app.config import settings
from app.db.models import SupportReply, SupportTicket, User
from app.db.session import SessionLocal
from app.services.users import set_bot_blocked

logger = logging.getLogger(__name__)
PAGE_SIZE = 20


def ticket_data(ticket, telegram_id):
    return {
        "id": ticket.id, "subject": ticket.subject, "closed": ticket.closed, "telegram_id": telegram_id,
        "created_at": ticket.created_at, "updated_at": ticket.updated_at,
    }


def ticket_query(user_id):
    query = select(SupportTicket, User.telegram_id).join(User, User.id == SupportTicket.user_id)
    if user_id is not None:
        query = query.where(User.telegram_id == user_id)
    return query


async def list_tickets(user_id, offset, state="all", search=""):
    query = ticket_query(user_id)
    if state != "all":
        query = query.where(SupportTicket.closed.is_(state == "closed"))
    if search:
        terms = [SupportTicket.subject.icontains(search, autoescape=True)]
        number = search.removeprefix("№").strip()
        if number.isascii() and number.isdigit() and len(number) <= 18:
            identifier = int(number)
            if identifier <= 2_147_483_647:
                terms.append(SupportTicket.id == identifier)
            if user_id is None:
                terms.append(User.telegram_id == identifier)
        query = query.where(or_(*terms))
    query = query.order_by(SupportTicket.updated_at.desc(), SupportTicket.id.desc()).limit(PAGE_SIZE + 1).offset(offset)
    async with SessionLocal() as session:
        rows = await session.execute(query)
        items = [ticket_data(ticket, telegram_id) for ticket, telegram_id in rows]
    return {"items": items[:PAGE_SIZE], "more": len(items) > PAGE_SIZE, "page_size": PAGE_SIZE}


async def get_ticket(ticket_id, user_id):
    async with SessionLocal() as session:
        row = (await session.execute(ticket_query(user_id).where(SupportTicket.id == ticket_id))).one_or_none()
        if row is None:
            return None
        replies = await session.scalars(select(SupportReply).where(
            SupportReply.ticket_id == ticket_id,
        ).order_by(SupportReply.id))
        return ticket_data(*row) | {"replies": [
            {"id": reply.id, "from_admin": reply.from_admin, "body": reply.body, "created_at": reply.created_at}
            for reply in replies
        ]}


async def create_ticket(telegram_id, subject, body):
    async with SessionLocal.begin() as session:
        user_id = await session.scalar(select(User.id).where(User.telegram_id == telegram_id))
        ticket = SupportTicket(user_id=user_id, subject=subject)
        session.add(ticket)
        await session.flush()
        session.add(SupportReply(ticket_id=ticket.id, body=body, from_admin=False))
        return ticket.id


async def update_ticket(ticket_id, user_id, *, body=None, closed=None):
    async with SessionLocal.begin() as session:
        row = (await session.execute(ticket_query(user_id).where(
            SupportTicket.id == ticket_id,
        ).with_for_update(of=SupportTicket))).one_or_none()
        if row is None:
            return None
        ticket, telegram_id = row
        if body is not None:
            if ticket.closed:
                raise ValueError("Обращение закрыто. Откройте его снова, чтобы ответить.")
            session.add(SupportReply(ticket_id=ticket.id, body=body, from_admin=user_id is None))
        changed = body is not None or (closed is not None and ticket.closed != closed)
        if closed is not None:
            ticket.closed = closed
        if changed:
            ticket.updated_at = func.clock_timestamp()
        return telegram_id, changed


def ticket_path(ticket_id, *, admin=False):
    path, query = ("/admin", {"tab": "tickets"}) if admin else ("/", {"section": "tickets"})
    return path + "?" + urlencode(query | {"ticket": ticket_id})


def ticket_button(ticket_id, *, admin):
    root = urlsplit(settings.webapp_root_url or settings.public_base_url)
    if root.scheme != "https" or not root.hostname:
        return None
    url = urljoin(root.geturl(), ticket_path(ticket_id, admin=admin))
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=f"Открыть тикет №{ticket_id}", web_app=WebAppInfo(url=url)),
    ]])


async def notify_ticket(bot, ticket_id, telegram_id=None, *, event="reply"):
    if bot is None:
        return
    admin = telegram_id is None
    recipients = settings.admin_id_set if admin else (telegram_id,)
    label = {
        "created": "Новое обращение",
        "reply": "Пользователь ответил" if admin else "Поддержка ответила",
        "closed": "Обращение закрыто",
        "reopened": "Обращение открыто снова",
    }[event]
    markup = ticket_button(ticket_id, admin=admin)
    for recipient in recipients:
        try:
            await bot.send_message(recipient, f"{label} · тикет №{ticket_id}", reply_markup=markup)
        except TelegramForbiddenError:
            await set_bot_blocked(recipient, True)
        except TelegramAPIError:
            logger.warning("Could not deliver ticket notification %s", ticket_id)
