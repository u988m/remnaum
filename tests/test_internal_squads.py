"""Set TEST_POSTGRES_URL to an asyncpg SQLAlchemy URL to run in an isolated schema."""
import json
import os
import unittest
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.db.base import Base
from app.db.models import Payment, User
from app.db.session import initialize_schema
from app.services import payment_service, plans, squads, subscriptions, tariffs

A, B = str(uuid4()), str(uuid4())
PLAN = dict(title="Basic", duration_days=30, traffic_gb=100, device_limit=1,
            price_rub_minor=900, price_stars=10)


@unittest.skipUnless(os.getenv("TEST_POSTGRES_URL"), "requires TEST_POSTGRES_URL")
class SquadLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        schema = "squads_" + uuid4().hex
        engine = create_async_engine(os.environ["TEST_POSTGRES_URL"], connect_args={
            "server_settings": {"search_path": schema},
        })
        self.addAsyncCleanup(engine.dispose)
        async with engine.begin() as conn:
            await conn.execute(text(f"CREATE SCHEMA {schema}"))
        async def cleanup():
            async with engine.begin() as conn:
                await conn.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        self.addAsyncCleanup(cleanup)
        self.engine = engine
        self.sessions = async_sessionmaker(engine, expire_on_commit=False)
        with ExitStack() as stack:
            for module in (payment_service, plans, squads, subscriptions, tariffs):
                stack.enter_context(patch.object(module, "SessionLocal", self.sessions))
            stack.enter_context(patch.object(settings, "remnawave_base_url", "https://panel.test"))
            stack.enter_context(patch.object(settings, "remnawave_api_token", "test"))
            stack.enter_context(patch.object(settings, "remnawave_active_internal_squads", ""))
            self.fetch = stack.enter_context(patch.object(squads.RemnawaveClient, "request"))
            self.addCleanup(stack.pop_all().close)

    async def sync(self, *ids):
        self.fetch.return_value = {"response": {"internalSquads": [
            {"uuid": key, "name": name} for key, name in ids
        ]}}
        return await squads.refresh_catalog()

    async def test_purchase_edit_renew_and_catalog_failures(self):
        async with self.engine.begin() as conn:
            await initialize_schema(conn)
        await self.sync((A, "A"))
        basic = await plans.save_plan(PLAN | {"internal_squads": [A]})
        await self.sync((A, "A"), (B, "B"))
        premium = await plans.save_plan(PLAN | {"title": "Plus", "internal_squads": [A, B]})
        async with self.sessions.begin() as session:
            session.add(User(telegram_id=12))
        await plans.choose_plan(12, basic["id"])
        async def checkout():
            with patch.object(payment_service, "get_terms_url", return_value="terms"), \
                 patch.object(payment_service, "has_accepted_current_terms", return_value=True):
                payment, _ = await payment_service.create_stars_payment_for_user(12)
            async with self.sessions.begin() as session:
                (await session.get(Payment, payment.id)).status = "paid"
            return payment

        order = await checkout()
        self.assertEqual(json.loads(order.payload)["tariff"]["internal_squads"], [A])
        await plans.save_plan(PLAN | {"internal_squads": [B]}, basic["id"])
        panel = AsyncMock()
        panel.get_user_by_username.return_value = {}
        panel.create_user.return_value = {"response": {"id": 7}}
        panel.get_user.return_value = {"response": {"id": 7}}
        with patch.object(subscriptions, "RemnawaveClient", return_value=panel):
            await subscriptions.provision_paid_payment(order.id)
            self.assertEqual(panel.create_user.await_args.kwargs["active_internal_squads"], [A])
            panel.get_user_by_username.return_value = {"response": {"id": 7}}
            renewal = await checkout()
            await subscriptions.provision_paid_payment(renewal.id)
            self.assertIsNone(panel.update_user.await_args.kwargs.get("active_internal_squads"))
            panel.get_user_by_username.return_value = {}
            missing_account = await checkout()
            with self.assertRaisesRegex(RuntimeError, "Panel account missing"):
                await subscriptions.provision_paid_payment(missing_account.id)
            panel.create_user.assert_awaited_once()

        last = await self.sync((A, "Renamed"))
        self.fetch.side_effect = httpx.ConnectError("offline")
        stale = await squads.refresh_catalog()
        self.assertTrue(stale["failed"])
        self.assertEqual(stale["last_success"], last["last_success"])
        self.assertEqual(stale["items"], last["items"])
        self.assertFalse(next(item for item in stale["items"] if item["uuid"] == B)["available"])
        saved = {p["id"]: p for p in await plans.list_plans(active_only=False)}
        self.assertEqual(saved[basic["id"]]["internal_squads"], [B])
        self.assertEqual(saved[premium["id"]]["internal_squads"], [A, B])
        with self.assertRaises(ValueError):
            await plans.save_plan(PLAN | {"internal_squads": [B]})

    async def test_upgrade_preserves_old_plan_and_is_repeatable(self):
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(text("ALTER TABLE tariff_plans DROP COLUMN internal_squads"))
            await conn.execute(text("""
                INSERT INTO tariff_plans (title, description, duration_days, traffic_gb, device_limit,
                    price_rub_minor, price_stars, is_active, sort_order)
                VALUES ('Legacy', '', 30, 100, 1, 900, 10, true, 0)
            """))
            with patch.object(settings, "remnawave_active_internal_squads", B):
                await initialize_schema(conn)
            await initialize_schema(conn)
        await self.sync((A, "New"))
        legacy, = await plans.list_plans(active_only=False)
        self.assertEqual(legacy["title"], "Legacy")
        self.assertEqual(legacy["internal_squads"], [B])
        await plans.save_plan(PLAN, legacy["id"])
        with self.assertRaises(ValueError):
            await plans.save_plan(PLAN)
