"""Set TEST_POSTGRES_URL to an asyncpg SQLAlchemy URL to run in an isolated schema."""
import json
import os
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.db.base import Base
from app.db.models import Payment, Subscription, TariffPlan, TermsAcceptance, User
from app.db.session import initialize_schema
from app.services.payment_providers import PaymentLink, VerifiedPayment
from app.services import payment_service, plans, squads, subscriptions, tariffs, trials

A, B = str(uuid4()), str(uuid4())
ACTIVE_EXPIRY = datetime.now(timezone.utc) + timedelta(days=10)
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
            for module in (payment_service, plans, squads, subscriptions, tariffs, trials):
                stack.enter_context(patch.object(module, "SessionLocal", self.sessions))
            stack.enter_context(patch.object(settings, "remnawave_base_url", "https://panel.test"))
            stack.enter_context(patch.object(settings, "remnawave_api_token", "test"))
            stack.enter_context(patch.object(settings, "remnawave_active_internal_squads", ""))
            self.fetch = stack.enter_context(patch.object(squads.RemnawaveClient, "request"))
            self.panel = AsyncMock()
            self.panel.get_user_by_username.return_value = {}
            stack.enter_context(patch.object(payment_service, "RemnawaveClient", return_value=self.panel))
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
        panel = self.panel
        panel.get_user_by_username.return_value = {}
        panel.create_user.return_value = {"response": {"id": 7}}
        panel.get_user.return_value = {"response": {
            "id": 7, "status": "ACTIVE", "expireAt": ACTIVE_EXPIRY.isoformat(), "activeInternalSquads": [A],
        }}
        with patch.object(subscriptions, "RemnawaveClient", return_value=panel):
            await subscriptions.provision_paid_payment(order.id)
            self.assertEqual(panel.create_user.await_args.kwargs["active_internal_squads"], [A])
            panel.get_user_by_username.return_value = panel.get_user.return_value
            async with self.sessions.begin() as session:
                (await session.get(TariffPlan, basic["id"])).internal_squads = []
            renewal = await checkout()
            self.assertTrue(json.loads(renewal.payload)["tariff"]["preserve_squads"])
            panel.get_user.side_effect = httpx.ConnectError("read failed after PATCH")
            with self.assertRaises(httpx.ConnectError):
                await subscriptions.provision_paid_payment(renewal.id)
            self.assertIsNone(panel.update_user.await_args.kwargs.get("active_internal_squads"))
            self.assertEqual(panel.update_user.await_args.kwargs["expire_at"], ACTIVE_EXPIRY + timedelta(days=30))
            panel.get_user.return_value["response"]["description"] = f"remnaum-payment:{renewal.id}"
            panel.get_user.side_effect = None
            await subscriptions.provision_paid_payment(renewal.id)
            panel.update_user.assert_awaited_once()
            self.assertFalse(await subscriptions.provision_paid_payment(renewal.id))
            panel.get_user.return_value["response"].pop("description")
            async with self.sessions.begin() as session:
                (await session.get(TariffPlan, basic["id"])).internal_squads = [B]
            for target, expected in [(premium, [A, B]), (basic, [B])]:
                await plans.choose_plan(12, target["id"])
                switched = await checkout()
                panel.get_user_by_username.return_value = panel.get_user.return_value
                self.fetch.side_effect = httpx.ConnectError("offline")
                with self.assertRaises(httpx.ConnectError):
                    await subscriptions.provision_paid_payment(switched.id)
                async with self.sessions() as session:
                    self.assertEqual((await session.get(Payment, switched.id)).status, "paid")
                self.fetch.side_effect = None
                await subscriptions.provision_paid_payment(switched.id)
                self.assertEqual(panel.update_user.await_args.kwargs["active_internal_squads"], expected)
                self.assertFalse(await subscriptions.provision_paid_payment(switched.id))
            missing_account = await checkout()
            panel.get_user_by_username.return_value = {}
            with self.assertRaisesRegex(ValueError, "отсутствует"):
                await checkout()
            with self.assertRaisesRegex(RuntimeError, "Panel account missing"):
                await subscriptions.provision_paid_payment(missing_account.id)
            async with self.sessions() as session:
                self.assertEqual((await session.get(Payment, missing_account.id)).status, "paid")
                self.assertEqual(await session.scalar(select(func.count()).select_from(Subscription)), 1)
            panel.create_user.assert_awaited_once()
            panel.get_user_by_username.return_value = panel.get_user.return_value
            self.assertTrue(await subscriptions.provision_paid_payment(missing_account.id))

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

    async def test_unlimited_plan_charges_addons_and_provisions_the_confirmed_snapshot(self):
        async with self.engine.begin() as conn:
            await initialize_schema(conn)
        await self.sync((A, "A"))
        config = PLAN | {
            "traffic_gb": 0, "device_limit": 2, "price_rub_minor": 19990,
            "extra_device_price_rub_minor": 4905, "extra_device_price_stars": 5,
            "badge": "Популярный", "is_featured": True, "internal_squads": [A],
        }
        plan = await plans.save_plan(config)
        async with self.sessions.begin() as session:
            session.add(User(telegram_id=31))
        provider = AsyncMock()
        provider.create_payment.return_value = PaymentLink("yookassa", "external-1", "https://payment.example")
        order = {"plan_id": plan["id"], "extra_devices": 2, "revision": plan["revision"]}
        credentials = {"yookassa": {"shop_id": "shop", "secret_key": "test-key"}}
        with patch.object(payment_service, "get_terms_url", return_value=""), \
             patch.object(payment_service, "get_store_payment_settings", return_value=({"yookassa": True}, credentials)), \
             patch.object(payment_service, "get_provider", return_value=provider):
            with self.assertRaisesRegex(ValueError, "количество"):
                await payment_service.create_stars_payment_for_user(31, **(order | {"extra_devices": -1}))
            payment, _ = await payment_service.create_payment_for_user(31, "yookassa", **order)
            stars_payment, stars = await payment_service.create_stars_payment_for_user(31, **order)
            self.assertEqual(payment.amount, 29800)
            self.assertEqual(provider.create_payment.call_args.kwargs["amount_minor"], 29800)
            self.assertEqual((stars_payment.amount, stars), (20, 20))

            await plans.save_plan(config | {
                "traffic_gb": 50, "price_rub_minor": 50000, "extra_device_price_stars": None,
            }, plan["id"])
            with self.assertRaisesRegex(ValueError, "изменились"):
                await payment_service.create_payment_for_user(31, "yookassa", **order)
            with self.assertRaisesRegex(ValueError, "Stars недоступна"):
                await payment_service.create_stars_payment_for_user(31, plan_id=plan["id"], extra_devices=2)
            current, = await plans.list_plans()
            self.assertEqual(current["extra_device_price_rub"], 49.05)

        order = json.loads(payment.payload)
        self.assertEqual(order["tariff"]["traffic_gb"], 0)
        self.assertEqual(order["tariff"]["device_limit"], 4)
        self.assertEqual(order["tariff"]["extra_devices"], 2)
        self.assertTrue(await payment_service.settle_payment(
            VerifiedPayment(payment.id, "external-1", 29800, "RUB"), "yookassa"
        ))

        self.panel.create_user.return_value = self.panel.get_user.return_value = {"response": {"id": 18}}
        with patch.object(subscriptions, "RemnawaveClient", return_value=self.panel):
            self.assertTrue(await subscriptions.provision_paid_payment(payment.id))
            self.assertFalse(await subscriptions.provision_paid_payment(payment.id))
        self.assertEqual(self.panel.create_user.call_args.kwargs["traffic_limit_bytes"], 0)
        self.assertEqual(self.panel.create_user.call_args.kwargs["hwid_device_limit"], 4)
        self.assertEqual(self.panel.create_user.call_args.kwargs["active_internal_squads"], [A])
        subscription = await subscriptions.get_user_subscription(31)
        self.assertEqual((subscription["traffic_gb"], subscription["device_limit"]), (0, 4))

    async def test_trial_to_flexible_paid_access(self):
        async with self.engine.begin() as conn:
            await initialize_schema(conn)
        await self.sync((A, "Trial"), (B, "Flexible"))
        async with self.sessions.begin() as session:
            session.add(User(telegram_id=20))
            (await session.get(squads.SquadPolicy, "trial")).squads = [A]
            (await session.get(squads.SquadPolicy, "flexible")).squads = [B]
        await trials.update_trial_settings(trials.DEFAULT_TRIAL | {"enabled": True})
        panel = self.panel
        panel.get_user_by_username.return_value = {}
        panel.create_user.return_value = panel.get_user.return_value = {"response": {"id": 9}}
        with patch.object(trials, "RemnawaveClient", return_value=panel):
            await trials.claim_trial(20)
        self.assertEqual(panel.create_user.await_args.kwargs["active_internal_squads"], [A])
        panel.get_user_by_username.return_value = panel.get_user.return_value
        with patch.object(payment_service, "get_terms_url", return_value="terms"), \
             patch.object(payment_service, "has_accepted_current_terms", return_value=True):
            order, _ = await payment_service.create_stars_payment_for_user(20)
        async with self.sessions.begin() as session:
            payment = await session.get(Payment, order.id)
            payment.status = "paid"
        panel.get_user_by_username.return_value = panel.get_user.return_value
        with patch.object(subscriptions, "RemnawaveClient", return_value=panel):
            await subscriptions.provision_paid_payment(order.id)
        self.assertEqual(panel.update_user.await_args.kwargs["active_internal_squads"], [B])

    async def test_checkout_rejects_unproven_access_and_rechecks_stars_invoice(self):
        async with self.engine.begin() as conn:
            await initialize_schema(conn)
        await self.sync((A, "Basic"))
        plan = await plans.save_plan(PLAN | {"internal_squads": [A]})
        async with self.sessions.begin() as session:
            user = User(telegram_id=42)
            session.add(user)
            await session.flush()
            user_id = user.id
            session.add(TermsAcceptance(user_id=user_id, terms_version=settings.telegram_terms_version))
        await plans.choose_plan(42, plan["id"])

        async def checkout():
            with patch.object(payment_service, "get_terms_url", return_value="terms"):
                return await payment_service.create_stars_payment_for_user(42)

        async with self.sessions.begin() as session:
            (await session.get(TariffPlan, plan["id"])).internal_squads = []
        with self.assertRaisesRegex(ValueError, "Сквады"):
            await checkout()
        async with self.sessions.begin() as session:
            (await session.get(TariffPlan, plan["id"])).internal_squads = [B]
        with self.assertRaisesRegex(ValueError, "Сквады"):
            await checkout()
        async with self.sessions.begin() as session:
            (await session.get(TariffPlan, plan["id"])).internal_squads = [A]
        order, amount = await checkout()
        async with self.sessions.begin() as session:
            subscription = Subscription(user_id=user_id, panel_user_id="9", status="active",
                                        expires_at=ACTIVE_EXPIRY)
            session.add(subscription)
            session.add(Payment(user_id=user_id, amount=10, currency="XTR",
                                provider="telegram_stars", status="succeeded", payload="{}"))
            (await session.get(TariffPlan, plan["id"])).internal_squads = []

        async def approve_invoice():
            with patch.object(payment_service, "get_terms_url", return_value="https://store.test/terms"):
                return await payment_service.validate_stars_pre_checkout(42, order.id, amount, "XTR", f"remnaum:{order.id}")

        self.assertFalse(await approve_invoice())
        self.panel.get_user_by_username.return_value = {"response": {
            "id": 9, "status": "ACTIVE", "expireAt": ACTIVE_EXPIRY.isoformat(), "activeInternalSquads": [A],
        }}
        self.assertTrue(await approve_invoice())
        with self.assertRaisesRegex(ValueError, "Сквады"):
            await checkout()
        async with self.sessions.begin() as session:
            subscription = await session.scalar(select(Subscription).where(Subscription.user_id == user_id))
            subscription.paid_tariff = str(plan["id"])
            subscription.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
        with self.assertRaisesRegex(ValueError, "Сквады"):
            await checkout()
        self.assertEqual(self.panel.create_user.await_count, 0)

    async def test_pagination_outage_and_mode_defaults(self):
        async with self.engine.begin() as conn:
            await initialize_schema(conn)
        self.fetch.side_effect = httpx.ConnectError("startup outage")
        self.assertTrue((await squads.refresh_catalog())["failed"])
        self.fetch.side_effect = [
            {"response": {"total": 2, "internalSquads": [{"uuid": A, "name": "First"}]}},
            {"response": {"total": 2, "internalSquads": [{"uuid": B, "name": "Second", "isActive": False}]}},
        ]
        items = await squads.fetch_catalog()
        self.assertEqual(self.fetch.await_args.kwargs["params"], {"start": 1, "size": 1})
        with self.assertRaises(ValueError):
            squads.require_available([B], items)
        async with self.sessions.begin() as session:
            self.assertEqual(await squads.mode_squads(session, "trial"), [])
            self.assertEqual(await squads.mode_squads(session, "flexible"), [])
            (await session.get(squads.SquadPolicy, "trial")).squads = [A]
        async with self.sessions() as session:
            self.assertEqual(await squads.mode_squads(session, "trial"), [A])
            self.assertEqual(await squads.mode_squads(session, "flexible"), [])

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
