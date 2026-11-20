import asyncio
from datetime import datetime, timedelta, timezone
import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import Payment, User
from app.db.session import initialize_schema
from app.services import payment_expiry, payment_service
from app.services.payment_providers import VerifiedPayment
from app.web import routes


@unittest.skipUnless(os.getenv("TEST_POSTGRES_URL"), "TEST_POSTGRES_URL is required")
class PaymentExpiryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        schema = "expiry_" + uuid4().hex
        engine = create_async_engine(os.environ["TEST_POSTGRES_URL"], connect_args={
            "server_settings": {"search_path": schema},
        })
        self.addAsyncCleanup(engine.dispose)

        async def cleanup():
            async with engine.begin() as connection:
                await connection.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))

        self.addAsyncCleanup(cleanup)
        async with engine.begin() as connection:
            await connection.execute(text(f"CREATE SCHEMA {schema}"))
            await initialize_schema(connection)
        self.sessions = async_sessionmaker(engine, expire_on_commit=False)
        self.bot = SimpleNamespace(send_message=AsyncMock())
        for module in (payment_expiry, payment_service, routes):
            self.enterContext(patch.object(module, "SessionLocal", self.sessions))

    async def add_order(self, age, *, telegram_id=42, status="pending", provider="platega", reference=None):
        async with self.sessions.begin() as session:
            user = User(telegram_id=telegram_id)
            session.add(user)
            await session.flush()
            order = Payment(
                user_id=user.id, provider=provider, provider_payment_id=reference,
                amount=100, currency="XTR" if provider == "telegram_stars" else "RUB", status=status,
                created_at=datetime.now(timezone.utc) - timedelta(minutes=age),
            )
            session.add(order)
            await session.flush()
            return order.id

    async def payment(self, payment_id):
        async with self.sessions() as session:
            return await session.get(Payment, payment_id)

    async def test_expires_only_overdue_pending_and_notifies_only_its_owner(self):
        overdue = await self.add_order(31)
        recent = await self.add_order(29, telegram_id=43)
        paid = await self.add_order(45, telegram_id=44, status="paid")
        finished = await self.add_order(45, telegram_id=45, status="succeeded")
        expired = await asyncio.gather(payment_expiry.expire_unpaid_payments(), payment_expiry.expire_unpaid_payments())
        self.assertEqual(sum(expired), 1)
        cursor, _ = await payment_expiry.deliver_expiry_notices(self.bot)
        self.assertEqual(cursor, overdue)
        self.bot.send_message.assert_awaited_once()
        recipient, message = self.bot.send_message.await_args.args
        self.assertEqual(recipient, 42)
        self.assertIn(f"Заказ №{overdue} отменён", message)
        self.assertIn("30 минут", message)
        markup = self.bot.send_message.await_args.kwargs["reply_markup"]
        self.assertEqual(markup.inline_keyboard[-1][0].callback_data, "notice:close")
        order = await self.payment(overdue)
        self.assertEqual(order.status, "cancelled")
        self.assertIsNotNone(order.expired_at)
        self.assertIsNotNone(order.expiry_notice_completed_at)
        for payment_id, status in [(recent, "pending"), (paid, "paid"), (finished, "succeeded")]:
            self.assertEqual((await self.payment(payment_id)).status, status)
        await payment_expiry.expire_unpaid_payments()
        await payment_expiry.deliver_expiry_notices(self.bot)
        self.bot.send_message.assert_awaited_once()

    async def test_notice_is_retried_and_workers_do_not_send_it_twice(self):
        order_id = await self.add_order(31)
        await payment_expiry.expire_unpaid_payments()
        self.bot.send_message.side_effect = TimeoutError()
        await payment_expiry.deliver_expiry_notices(self.bot)
        self.assertIsNone((await self.payment(order_id)).expiry_notice_completed_at)
        self.bot.send_message.reset_mock(side_effect=True)

        async def send(*args, **kwargs):
            await asyncio.sleep(0.05)

        self.bot.send_message.side_effect = send
        await asyncio.gather(
            payment_expiry.deliver_expiry_notices(self.bot), payment_expiry.deliver_expiry_notices(self.bot),
        )
        self.bot.send_message.assert_awaited_once()
        self.assertIsNotNone((await self.payment(order_id)).expiry_notice_completed_at)

    async def test_late_payment_is_honored_and_suppresses_unsent_expiry_notice(self):
        order_id = await self.add_order(31, reference="provider-id")
        await payment_expiry.expire_unpaid_payments()
        wrong = VerifiedPayment(order_id, "provider-id", 101, "RUB")
        with self.assertRaisesRegex(ValueError, "amount or currency mismatch"):
            await payment_service.settle_payment(wrong, "platega")
        good = VerifiedPayment(order_id, "provider-id", 100, "RUB")
        self.assertTrue(await payment_service.settle_payment(good, "platega"))
        self.assertEqual((await self.payment(order_id)).status, "paid")
        await payment_expiry.deliver_expiry_notices(self.bot)
        self.bot.send_message.assert_not_awaited()

    async def test_new_payment_attempts_are_rejected_at_deadline_even_before_scan(self):
        old_stars = await self.add_order(31, provider="telegram_stars")
        new_stars = await self.add_order(29, telegram_id=43, provider="telegram_stars")
        old_wallet = await self.add_order(31, telegram_id=44, provider="yoomoney")
        with patch.object(payment_service, "require_checkout_terms"), \
             patch.object(payment_service, "validate_checkout_access"):
            self.assertFalse(await payment_service.validate_stars_pre_checkout(
                42, old_stars, 100, "XTR", f"remnaum:{old_stars}",
            ))
            self.assertTrue(await payment_service.validate_stars_pre_checkout(
                43, new_stars, 100, "XTR", f"remnaum:{new_stars}",
            ))
        with self.assertRaises(HTTPException) as exc:
            await routes.yoomoney_payment_form(old_wallet)
        self.assertEqual(exc.exception.status_code, 410)

    async def test_upgrade_closes_old_orders_without_retrospective_notifications(self):
        overdue = await self.add_order(31)
        recent = await self.add_order(29, telegram_id=43)
        async with self.sessions.begin() as session:
            await session.execute(text("ALTER TABLE payments DROP COLUMN expired_at, DROP COLUMN expiry_notice_completed_at"))
            await session.execute(text("UPDATE alembic_version SET version_num = '0009_ticket_activity'"))
        async with self.sessions.begin() as session:
            await initialize_schema(await session.connection())
        self.assertEqual((await self.payment(overdue)).status, "cancelled")
        self.assertIsNotNone((await self.payment(overdue)).expiry_notice_completed_at)
        self.assertEqual((await self.payment(recent)).status, "pending")
        await payment_expiry.deliver_expiry_notices(self.bot)
        self.bot.send_message.assert_not_awaited()
