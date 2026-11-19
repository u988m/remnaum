"""Set TEST_POSTGRES_URL to an asyncpg SQLAlchemy URL to run in an isolated schema."""
import asyncio
import json
import os
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
from fastapi import FastAPI
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.services import store_operations
from app.db.base import Base
from app.db.models import DeviceAlias, Payment, Referral, ReferralReward, Subscription, SubscriptionDetails, TariffPlan, TermsAcceptance, User
from app.db.session import initialize_schema
from app.services.payment_providers import PaymentLink, VerifiedPayment
from app.services import customer_admin, devices, payment_service, plans, squads, subscriptions, tariffs, trials, users
from app.web import routes

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
            for module in (customer_admin, devices, payment_service, plans, squads, subscriptions, tariffs, trials, users, store_operations):
                stack.enter_context(patch.object(module, "SessionLocal", self.sessions))
            stack.enter_context(patch.object(settings, "remnawave_base_url", "https://panel.test"))
            stack.enter_context(patch.object(settings, "remnawave_api_token", "test"))
            stack.enter_context(patch.object(settings, "remnawave_active_internal_squads", ""))
            self.fetch = stack.enter_context(patch.object(squads.RemnawaveClient, "request"))
            self.panel = AsyncMock()
            self.panel.get_user_by_username.return_value = {}
            self.panel.get_users_by_telegram_id.return_value = {"response": []}
            stack.enter_context(patch.object(payment_service, "RemnawaveClient", return_value=self.panel))
            stack.enter_context(patch.object(customer_admin, "RemnawaveClient", return_value=self.panel))
            self.addCleanup(stack.pop_all().close)

    async def sync(self, *ids):
        self.fetch.return_value = {"response": {"internalSquads": [
            {"uuid": key, "name": name} for key, name in ids
        ]}}
        return await squads.refresh_catalog()

    async def test_webapp_devices_share_names_and_enforce_customer_ownership(self):
        async with self.engine.begin() as conn:
            await initialize_schema(conn)
        async with self.sessions.begin() as session:
            for telegram_id in (42, 43):
                user = User(telegram_id=telegram_id)
                session.add(user)
                await session.flush()
                subscription = Subscription(user_id=user.id, panel_user_id=str(telegram_id * 10), status="active")
                session.add(subscription)
                await session.flush()
                session.add(SubscriptionDetails(subscription_id=subscription.id, device_limit=2, traffic_gb=0))

        hardware = "shared-private-hwid"
        inventory = {
            420: [{"hwid": hardware, "deviceModel": "iPhone", "platform": "iOS", "osVersion": "18",
                   "userAgent": "Happ/3.1 CFNetwork"}],
            430: [{"hwid": hardware, "deviceModel": "Pixel", "userAgent": "Incy/1.4 Android"}],
        }
        async def named(username):
            telegram_id = int(username.removeprefix("tg_"))
            return {"response": {"id": telegram_id * 10, "telegramId": telegram_id}}
        async def detach(panel_id, hwid):
            self.assertEqual((panel_id, hwid), (420, hardware))
            inventory[panel_id].clear()
            return {"response": {"devices": []}}
        self.panel.get_user_by_username.side_effect = named
        self.panel.get_hwid_devices.side_effect = lambda panel_id: {"response": {"devices": inventory[panel_id]}}
        self.panel.delete_hwid_device.side_effect = detach

        app = FastAPI()
        app.include_router(routes.router)
        path = "/api/v1/webapp/devices"
        owner = {"X-Telegram-Init-Data": "42"}
        stranger = {"X-Telegram-Init-Data": "43"}
        with patch.object(routes, "SessionLocal", self.sessions), \
             patch.object(routes, "validate_telegram_init_data", side_effect=lambda data: {"id": int(data)}), \
             patch.object(devices, "RemnawaveClient", return_value=self.panel):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                listed = (await client.get(path, headers=owner)).json()
                device = listed["items"][0]
                self.assertEqual((listed["total"], listed["limit"], device["client"]), (1, 2, "Happ"))
                self.assertNotIn(hardware, json.dumps(listed))
                target = path + "/" + device["id"]
                self.assertEqual((await client.delete(target)).status_code, 401)
                self.assertEqual((await client.delete(target, headers=stranger)).status_code, 404)
                self.panel.delete_hwid_device.assert_not_awaited()
                other_device = (await client.get(path, headers=stranger)).json()["items"][0]
                self.assertEqual(other_device["client"], "Incy")
                self.assertNotEqual(other_device["id"], device["id"])

                result = await client.patch(target, headers=owner, json={"name": "  Личный телефон  "})
                self.assertEqual(result.status_code, 200)
                self.assertEqual((await devices.get_customer_devices(42))["items"][0]["name"], "Личный телефон")
                self.assertEqual((await client.patch(target, headers=owner, json={"name":""})).json()["name"], "iPhone")
                await client.patch(target, headers=owner, json={"name":"Снова телефон"})
                self.assertEqual((await client.delete(target, headers=owner)).status_code, 200)
                self.assertEqual((await client.get(path, headers=owner)).json()["total"], 0)
                self.assertEqual((await client.get(path, headers=stranger)).json()["total"], 1)
                self.assertEqual((await client.delete(target, headers=owner)).status_code, 404)
                self.panel.delete_hwid_device.assert_awaited_once_with(420, hardware)
                async with self.sessions() as session:
                    self.assertEqual(await session.scalar(select(func.count()).select_from(DeviceAlias)), 0)

    async def test_admin_manages_customer_and_retains_detached_payment_ledger(self):
        async with self.engine.begin() as conn:
            await initialize_schema(conn)
        await self.sync((A, "Basic"))
        plan = await plans.save_plan(PLAN | {"internal_squads": [A]})
        async with self.sessions.begin() as session:
            customer = User(telegram_id=42, username="customer")
            inviter = User(telegram_id=43)
            session.add_all([customer, inviter])
            await session.flush()
            payment = Payment(user_id=customer.id, provider="telegram_stars", provider_payment_id="receipt-42",
                              currency="XTR", amount=10, status="succeeded")
            session.add(payment)
            await session.flush()
            session.add_all([
                Referral(inviter_id=inviter.id, invited_id=customer.id),
                ReferralReward(inviter_id=inviter.id, invited_id=customer.id, source_payment_id=payment.id, days=3),
            ])

        await customer_admin.change_display_name(42, "  Клиент  ")
        panel_account = {"response": {"id": 5, "telegramId": 42, "subscriptionUrl": "https://panel.test/sub"}}
        self.panel.create_user.return_value = panel_account
        await customer_admin.save_customer_subscription(42, plan["id"], ACTIVE_EXPIRY, 20, 3)
        self.panel.create_user.assert_awaited_once_with(
            "tg_42", ACTIVE_EXPIRY, telegram_id=42, traffic_limit_bytes=20 * 1024 ** 3,
            hwid_device_limit=3, active_internal_squads=[A],
        )
        self.panel.get_user_by_username.return_value = panel_account
        self.panel.update_user.return_value = panel_account
        new_expiry = ACTIVE_EXPIRY + timedelta(days=20)
        await customer_admin.save_customer_subscription(42, plan["id"], new_expiry, 30, 4)
        current = await subscriptions.get_user_subscription(42)
        self.assertEqual((current["expires_at"], current["traffic_gb"], current["device_limit"]), (new_expiry, 30, 4))
        async with self.sessions() as session:
            self.assertEqual((await session.get(User, customer.id)).display_name, "Клиент")

        await customer_admin.set_customer_block(42, True)
        self.panel.disable_user.assert_awaited_once_with(5)
        self.assertTrue(await users.customer_is_blocked(42))
        with self.assertRaises(users.BlockedCustomer):
            async with self.sessions.begin() as session:
                await users.upsert_active_customer(session, 42, "customer")
        with patch.object(payment_service, "get_terms_url", return_value=""), self.assertRaises(users.BlockedCustomer):
            await payment_service.create_stars_payment_for_user(42, plan_id=plan["id"], revision=plan["revision"])
        await customer_admin.set_customer_block(42, False)
        self.panel.enable_user.assert_awaited_once_with(5)
        self.assertFalse(await users.customer_is_blocked(42))

        await customer_admin.remove_customer(42, delete_account=False)
        self.panel.request.assert_awaited_once_with("DELETE", "/api/users/5")
        self.panel.get_user_by_username.return_value = {}
        self.assertIsNone(await subscriptions.get_user_subscription(42))
        async with self.sessions() as session:
            self.assertEqual((await session.get(Payment, payment.id)).user_id, customer.id)
        await customer_admin.remove_customer(42, delete_account=True)
        async with self.sessions() as session:
            self.assertIsNone(await session.get(User, customer.id))
            self.assertIsNone(await session.scalar(select(Referral)))
            retained = await session.get(Payment, payment.id)
            self.assertEqual((retained.user_id, retained.amount, retained.status), (None, 10, "succeeded"))
            reward = await session.scalar(select(ReferralReward))
            self.assertEqual((reward.inviter_id, reward.invited_id, reward.days), (inviter.id, None, 3))
        with patch.object(routes, "SessionLocal", self.sessions), patch.object(routes, "_require_telegram_admin"):
            result = await routes.admin_payments(10, 0, "valid")
        self.assertEqual(result["total"], 1)
        self.assertIsNone(result["items"][0]["telegram_id"])

    async def test_repeated_monthly_orders_extend_once_and_charge_stars_per_device(self):
        async with self.engine.begin() as conn:
            await initialize_schema(conn)
        await self.sync((A, "Basic"))
        plan = await plans.save_plan(PLAN | {"extra_device_price_stars": 5, "internal_squads": [A]})
        self.assertEqual(plan["max_extra_devices_rub"], 0)
        self.assertGreaterEqual(plan["max_extra_devices_stars"], 2)
        async with self.sessions.begin() as session:
            user = User(telegram_id=21)
            session.add(user)
            await session.flush()
            session.add(Subscription(user_id=user.id, panel_user_id="9", paid_tariff=str(plan["id"]),
                                     status="active", expires_at=ACTIVE_EXPIRY))

        panel_user = {
            "id": 9, "status": "ACTIVE", "expireAt": ACTIVE_EXPIRY.isoformat(),
            "activeInternalSquads": [A], "description": "Internal note | remnaum-payment:200",
        }
        self.panel.get_user_by_username.return_value = self.panel.get_user.return_value = {"response": panel_user}

        async def apply_panel_update(panel_id, **values):
            self.assertEqual(panel_id, 9)
            self.assertEqual(values["hwid_device_limit"], 3)
            panel_user.update(expireAt=values["expire_at"].isoformat(), description=values["description"])
        self.panel.update_user.side_effect = apply_panel_update

        orders = []
        with patch.object(payment_service, "get_terms_url", return_value=""), \
             patch.object(subscriptions, "RemnawaveClient", return_value=self.panel):
            for index in range(2):
                order, stars = await payment_service.create_stars_payment_for_user(
                    21, plan_id=plan["id"], extra_devices=2, revision=plan["revision"],
                )
                self.assertEqual(stars, 20)
                await payment_service.settle_payment(VerifiedPayment(order.id, f"tg-{index}", stars, "XTR"), "telegram_stars")
                orders.append(order.id)
            applied = await asyncio.gather(*(subscriptions.provision_paid_payment(order) for order in orders))
            self.assertEqual(applied, [True, True])
            self.assertFalse(await subscriptions.provision_paid_payment(orders[0]))

        result = await subscriptions.get_user_subscription(21)
        self.assertEqual(result["expires_at"], ACTIVE_EXPIRY + timedelta(days=60))
        self.assertEqual((result["device_limit"], result["plan_id"]), (3, plan["id"]))
        self.assertEqual(panel_user["description"].count("remnaum-payment:"), 1)
        self.assertTrue(panel_user["description"].startswith("Internal note | "))

        with patch.object(routes, "SessionLocal", self.sessions), \
             patch.object(routes, "_require_telegram_admin", return_value={"first_name": "Owner"}):
            overview = await routes.admin_overview("valid")
        self.assertEqual(overview["active_subscriptions"], 1)
        self.assertEqual(overview["active_plans"], [{"name": "Basic", "count": 1}])
        self.assertEqual(overview["stars_received"], 40)
        self.assertEqual(len(overview["revenue_days"]), 30)

    async def test_reconciles_a_verified_new_panel_id_and_recreates_a_missing_account(self):
        async with self.engine.begin() as conn:
            await initialize_schema(conn)
        await self.sync((A, "Basic"))
        plan = await plans.save_plan(PLAN | {"internal_squads": [A]})
        async with self.sessions.begin() as session:
            user = User(telegram_id=42)
            session.add(user)
            await session.flush()
            session.add(Subscription(user_id=user.id, panel_user_id=str(uuid4()), paid_tariff=str(plan["id"]),
                                     status="active", expires_at=ACTIVE_EXPIRY))

        panel = self.panel
        remote = {"id": 45, "telegramId": "42", "status": "ACTIVE",
                  "expireAt": (ACTIVE_EXPIRY - timedelta(days=2)).isoformat(), "activeInternalSquads": [A]}
        panel.get_users_by_telegram_id.return_value = {"response": {"users": [remote], "total": 1}}

        async def get_account(panel_id):
            if str(panel_id) == str(remote.get("id")):
                return {"response": remote.copy()}
            response = httpx.Response(400 if not str(panel_id).isdigit() else 404)
            raise httpx.HTTPStatusError("missing", request=httpx.Request("GET", "https://panel.test"), response=response)

        panel.get_user.side_effect = get_account
        async def update_account(panel_id, **values):
            self.assertEqual(panel_id, 45)
            remote.update(expireAt=values["expire_at"].isoformat(), description=values["description"])
        panel.update_user.side_effect = update_account

        async def buy(receipt):
            order, amount = await payment_service.create_stars_payment_for_user(42, plan_id=plan["id"], revision=plan["revision"])
            self.assertTrue(await payment_service.validate_stars_pre_checkout(42, order.id, amount, "XTR", f"remnaum:{order.id}"))
            await payment_service.settle_payment(VerifiedPayment(order.id, receipt, amount, "XTR"), "telegram_stars")
            self.assertTrue(await subscriptions.provision_paid_payment(order.id))
            return order

        with patch.object(payment_service, "get_terms_url", return_value=""), \
             patch.object(subscriptions, "RemnawaveClient", return_value=panel):
            panel.get_users_by_telegram_id.return_value["response"] = [remote, {"id": 46, "telegramId": 42}]
            with self.assertRaises(subscriptions.PanelAccountConflict):
                await payment_service.create_stars_payment_for_user(42, plan_id=plan["id"], revision=plan["revision"])
            panel.get_users_by_telegram_id.return_value["response"] = {"users": [remote], "total": 1}
            await buy("verified-replacement")
            rebound = await subscriptions.get_user_subscription(42)
            self.assertEqual((rebound["panel_user_id"], rebound["expires_at"]), ("45", ACTIVE_EXPIRY + timedelta(days=30)))

            remote.clear()
            panel.get_users_by_telegram_id.return_value = {"response": []}
            async def create_account(username, expiry, **values):
                self.assertEqual((username, values["telegram_id"], values["active_internal_squads"]), ("tg_42", 42, [A]))
                remote.update(id=55, telegramId=42, status="ACTIVE", expireAt=expiry.isoformat())
                return {"response": remote.copy()}
            panel.create_user.side_effect = create_account
            restored = await buy("restored-account")
            self.assertTrue(json.loads(restored.payload)["tariff"]["allow_recreate"])
            self.assertFalse(await subscriptions.provision_paid_payment(restored.id))
            rebound = await subscriptions.get_user_subscription(42)
            self.assertEqual((rebound["panel_user_id"], rebound["expires_at"]), ("55", ACTIVE_EXPIRY + timedelta(days=60)))

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
            stored = panel.get_user.return_value
            panel.get_user.return_value = {}
            with self.assertRaisesRegex(RuntimeError, "Panel account missing"):
                await subscriptions.provision_paid_payment(missing_account.id)
            async with self.sessions() as session:
                self.assertEqual((await session.get(Payment, missing_account.id)).status, "paid")
                self.assertEqual(await session.scalar(select(func.count()).select_from(Subscription)), 1)
            panel.create_user.assert_awaited_once()
            panel.get_user.return_value = stored
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
