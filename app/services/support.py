"""Store ticket conversations and notify participants in Telegram."""
import logging

from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from sqlalchemy import select

from app.config import settings
from app.db.models import SupportReply, SupportTicket, User
from app.db.session import SessionLocal
from app.services.users import set_bot_blocked

logger = logging.getLogger(__name__)


def ticket_data(ticket, telegram_id):
    return {"id": ticket.id, "subject": ticket.subject, "closed": ticket.closed, "telegram_id": telegram_id,
            "created_at": ticket.created_at}


def ticket_query(user_id):
    query = select(SupportTicket, User.telegram_id).join(User, User.id == SupportTicket.user_id)
    if user_id is not None:
        query = query.where(User.telegram_id == user_id)
    return query


async def list_tickets(user_id, offset):
    async with SessionLocal() as session:
        rows = await session.execute(ticket_query(user_id).order_by(
            SupportTicket.closed, SupportTicket.id.desc(),
        ).limit(51).offset(offset))
        items = [ticket_data(ticket, telegram_id) for ticket, telegram_id in rows]
        return {"items": items[:50], "more": len(items) > 50}


async def get_ticket(ticket_id, user_id):
    async with SessionLocal() as session:
        row = (await session.execute(ticket_query(user_id).where(SupportTicket.id == ticket_id))).one_or_none()
        if row is None:
            return None
        replies = await session.scalars(select(SupportReply).where(
            SupportReply.ticket_id == ticket_id,
        ).order_by(SupportReply.id))
        return ticket_data(*row) | {"replies": [
            {"from_admin": reply.from_admin, "body": reply.body, "created_at": reply.created_at} for reply in replies
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
                raise ValueError("Тикет закрыт.")
            session.add(SupportReply(ticket_id=ticket.id, body=body, from_admin=user_id is None))
        if closed is not None:
            ticket.closed = closed
        return telegram_id


async def notify_ticket(bot, ticket_id, telegram_id=None):
    if bot is None:
        return
    recipients = (telegram_id,) if telegram_id is not None else settings.admin_id_set
    for recipient in recipients:
        try:
            label = "Поддержка ответила" if telegram_id is not None else "Новое сообщение"
            await bot.send_message(recipient, f"{label} в тикете №{ticket_id}. Откройте раздел поддержки в веб-приложении.")
        except TelegramForbiddenError:
            await set_bot_blocked(recipient, True)
        except TelegramAPIError:
            logger.warning("Could not deliver ticket notification %s", ticket_id)
