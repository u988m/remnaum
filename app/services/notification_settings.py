"""Default subscription notices and per-customer Telegram preferences."""
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.db.models import StoreNotificationSettings, User, UserNotificationPreferences
from app.db.session import SessionLocal
from app.services.users import BlockedCustomer


class NotificationMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


class TimeNotifications(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lead: int = Field(ge=1, le=720)
    reminder: NotificationMessage
    expired: NotificationMessage


class TrafficNotifications(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lead: int = Field(ge=1, le=99)
    reminder: NotificationMessage
    expired: NotificationMessage


class NotificationSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subscription: TimeNotifications
    trial: TimeNotifications
    traffic: TrafficNotifications


DEFAULT_NOTIFICATION_SETTINGS = NotificationSettings(
    subscription=TimeNotifications(
        lead=72,
        reminder=NotificationMessage(enabled=True, text="⏳ Срок вашей VPN-подписки скоро истечёт. Продлить подписку можно в боте."),
        expired=NotificationMessage(enabled=True, text="🔔 Срок вашей VPN-подписки истёк. Чтобы восстановить доступ, продлите подписку в боте."),
    ),
    trial=TimeNotifications(
        lead=24,
        reminder=NotificationMessage(enabled=True, text="⏳ Пробный доступ скоро закончится. Выбрать тариф можно в боте."),
        expired=NotificationMessage(enabled=True, text="🔔 Пробный доступ закончился. Оформите подписку в боте, чтобы продолжить пользоваться VPN."),
    ),
    traffic=TrafficNotifications(
        lead=20,
        reminder=NotificationMessage(enabled=True, text="📊 Трафик по вашей подписке скоро закончится. Посмотреть доступные тарифы можно в боте."),
        expired=NotificationMessage(enabled=True, text="🔔 Лимит трафика исчерпан. Посмотреть доступные тарифы можно в боте."),
    ),
)


class NotificationPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subscription_reminder: bool = True
    subscription_expired: bool = True
    trial_reminder: bool = True
    trial_expired: bool = True
    traffic_reminder: bool = True
    traffic_expired: bool = True


async def get_notification_settings() -> NotificationSettings:
    async with SessionLocal() as session:
        row = await session.get(StoreNotificationSettings, 1)
        return NotificationSettings.model_validate(row.data) if row else DEFAULT_NOTIFICATION_SETTINGS


async def save_notification_settings(value: NotificationSettings) -> None:
    data = value.model_dump()
    async with SessionLocal.begin() as session:
        await session.execute(
            insert(StoreNotificationSettings).values(id=1, data=data)
            .on_conflict_do_update(index_elements=[StoreNotificationSettings.id], set_={"data": data})
        )


async def get_notification_preferences(telegram_id: int) -> NotificationPreferences:
    async with SessionLocal() as session:
        data = await session.scalar(
            select(UserNotificationPreferences.data).join(User).where(User.telegram_id == telegram_id)
        )
        return NotificationPreferences.model_validate(data or {})


async def save_notification_preferences(telegram_id: int, value: NotificationPreferences) -> None:
    data = value.model_dump()
    async with SessionLocal.begin() as session:
        user = await session.scalar(select(User).where(User.telegram_id == telegram_id).with_for_update())
        if user is None or user.blocked_at is not None:
            raise BlockedCustomer()
        await session.execute(
            insert(UserNotificationPreferences).values(user_id=user.id, data=data)
            .on_conflict_do_update(index_elements=[UserNotificationPreferences.user_id], set_={"data": data})
        )
