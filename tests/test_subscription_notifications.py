import asyncio
from datetime import datetime, timedelta, timezone
import os
import secrets
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import Subscription, SubscriptionNotification, TrialClaim, User
from app.services import notification_settings, subscription_notifications
from app.services.notification_settings import DEFAULT_NOTIFICATION_SETTINGS, NotificationPreferences


@unittest.skipUnless(os.getenv("TEST_POSTGRES_URL"), "TEST_POSTGRES_URL is required")
class SubscriptionNotificationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        url = make_url(os.environ["TEST_POSTGRES_URL"])
        name = "remnaum_notification_test_" + secrets.token_hex(6)
        admin = create_async_engine(url, isolation_level="AUTOCOMMIT")
        self.addAsyncCleanup(admin.dispose)
        async with admin.connect() as conn:
            await conn.execute(text(f'CREATE DATABASE "{name}"'))

        async def drop_database():
            async with admin.connect() as conn:
                await conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))

        self.addAsyncCleanup(drop_database)
        engine = create_async_engine(url.set(database=name))
        self.addAsyncCleanup(engine.dispose)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(engine, expire_on_commit=False)
        self.bot = SimpleNamespace(send_message=AsyncMock())
        self.panel = SimpleNamespace(base_url="", token="", list_users_page=AsyncMock())
        for module in (notification_settings, subscription_notifications):
            mock = patch.object(module, "SessionLocal", self.sessions)
            mock.start()
            self.addCleanup(mock.stop)

    async def add_subscription(self, telegram_id, expiry, *, trial=False, blocked=False):
        async with self.sessions.begin() as session:
            user = User(telegram_id=telegram_id, blocked_at=datetime.now(timezone.utc) if blocked else None)
            session.add(user)
            await session.flush()
            subscription = Subscription(
                user_id=user.id, panel_user_id=str(telegram_id + 1000), status="active",
                expires_at=expiry, paid_tariff=None if trial else "1",
            )
            session.add(subscription)
            if trial:
                session.add(TrialClaim(user_id=user.id, status="completed", panel_user_id=subscription.panel_user_id))
            await session.flush()
            return subscription

    async def scan(self):
        await subscription_notifications.scan_subscription_notifications(self.bot, self.panel)

    def sent(self):
        return {call.args[0]: call.args[1] for call in self.bot.send_message.await_args_list}

    async def test_time_notices_respect_trial_opt_out_and_renewal(self):
        now = datetime.now(timezone.utc)
        reminder = await self.add_subscription(11, now + timedelta(days=2))
        trial = await self.add_subscription(12, now + timedelta(hours=10), trial=True)
        await self.add_subscription(13, now - timedelta(hours=1))
        await self.add_subscription(14, now - timedelta(hours=2), trial=True)
        await self.add_subscription(15, now - timedelta(days=2))
        await self.add_subscription(16, now + timedelta(hours=1), blocked=True)
        await self.add_subscription(17, now + timedelta(hours=1))
        await notification_settings.save_notification_preferences(17, NotificationPreferences(subscription_reminder=False))
        rules = DEFAULT_NOTIFICATION_SETTINGS.model_copy(deep=True)
        rules.trial.expired.text = "Триал завершён"
        await notification_settings.save_notification_settings(rules)

        await asyncio.gather(self.scan(), self.scan())
        self.assertEqual(self.bot.send_message.await_count, 4)
        self.assertEqual(self.sent(), {
            11: rules.subscription.reminder.text,
            12: rules.trial.reminder.text,
            13: rules.subscription.expired.text,
            14: rules.trial.expired.text,
        })
        self.panel.list_users_page.assert_not_awaited()
        self.bot.send_message.reset_mock()
        rules.subscription.expired.enabled = False
        await notification_settings.save_notification_settings(rules)
        await self.add_subscription(18, now - timedelta(hours=1))
        async with self.sessions.begin() as session:
            renewed = await session.get(Subscription, reminder.id)
            renewed.expires_at = now + timedelta(days=20)
            converted = await session.get(Subscription, trial.id)
            converted.paid_tariff = "2"
            converted.expires_at = now + timedelta(days=2)
        await self.scan()
        self.assertEqual(self.sent(), {12: rules.subscription.reminder.text})

    async def test_traffic_notices_retry_and_reset_with_the_quota(self):
        now = datetime.now(timezone.utc)
        subscription = await self.add_subscription(21, now + timedelta(days=10))
        await self.add_subscription(22, now + timedelta(days=10))
        account = {
            "id": int(subscription.panel_user_id), "telegramId": 21, "shortUuid": "shop-account",
            "status": "ACTIVE", "trafficLimitBytes": 1000, "expireAt": subscription.expires_at.isoformat(),
            "userTraffic": {"usedTrafficBytes": 800},
        }
        unlimited = account | {"id": 1022, "telegramId": 22, "trafficLimitBytes": 0}
        self.panel.base_url = "https://example.com"
        self.panel.token = "test-token"

        async def page(*, start, size):
            return {"response": {"total": 2, "users": [account if start == 0 else unlimited]}}

        self.panel.list_users_page.side_effect = page
        self.bot.send_message.side_effect = [RuntimeError("temporary Telegram failure"), None, None, None]
        with self.assertLogs("app.services.subscription_notifications", level="ERROR"):
            await self.scan()
        await self.scan()
        await self.scan()
        self.assertEqual(self.bot.send_message.await_count, 2)
        self.assertEqual(self.sent(), {21: DEFAULT_NOTIFICATION_SETTINGS.traffic.reminder.text})
        self.assertEqual(self.panel.list_users_page.await_args_list[1].kwargs["start"], 1)

        account["status"] = "LIMITED"
        account["userTraffic"]["usedTrafficBytes"] = 1000
        await self.scan()
        self.assertEqual(self.bot.send_message.await_args.args[1], DEFAULT_NOTIFICATION_SETTINGS.traffic.expired.text)

        account["status"] = "ACTIVE"
        account["lastTrafficResetAt"] = now.isoformat()
        account["userTraffic"]["usedTrafficBytes"] = 850
        await self.scan()
        await self.scan()
        self.assertEqual(self.bot.send_message.await_args.args[1], DEFAULT_NOTIFICATION_SETTINGS.traffic.reminder.text)
        self.assertEqual(self.bot.send_message.await_count, 4)
        async with self.sessions() as session:
            delivered = await session.scalars(select(SubscriptionNotification.kind))
            self.assertCountEqual(delivered.all(), ["traffic_reminder", "traffic_expired", "traffic_reminder"])
