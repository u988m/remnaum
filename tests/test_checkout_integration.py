"""Checkout and schema upgrade tests against PostgreSQL with a simulated Panel."""

import asyncio
import hashlib
import hmac
import json
import os
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.parse import urlencode
from uuid import uuid4

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.db.migrate import upgrade_database
from app.db.models import Payment, PaymentActivation, Subscription, TariffPlan, User
from app.main import app
from app.services import payment_activation, payment_service, renewal_reminders, store_settings, subscriptions, tariffs
from app.services.remnawave import RemnawaveClient
from app.services.store_auth import issue_store_session
from app.web import routes, storefront


@unittest.skipUnless(os.getenv("RUN_POSTGRES_INTEGRATION") == "1", "requires PostgreSQL")
class CheckoutIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.schema = "test_" + uuid4().hex
        admin = create_async_engine(settings.database_url)
        self.addAsyncCleanup(admin.dispose)
        async with admin.begin() as connection:
            await connection.execute(text(f"CREATE SCHEMA {self.schema}"))

        async def drop_schema():
            async with admin.begin() as connection:
                await connection.execute(text(f"DROP SCHEMA {self.schema} CASCADE"))

        self.addAsyncCleanup(drop_schema)
        self.engine = create_async_engine(settings.database_url, connect_args={"server_settings": {"search_path": self.schema}})
        self.addAsyncCleanup(self.engine.dispose)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        patches = ExitStack()
        self.addCleanup(patches.close)
        for module in (payment_activation, payment_service, renewal_reminders, store_settings,
                       subscriptions, tariffs, routes, storefront):
            patches.enter_context(patch.object(module, "SessionLocal", self.sessions))
        for name, value in (
            ("telegram_bot_token", "integration-bot-token"),
            ("admin_ids", "900"),
            ("public_base_url", "https://shop.example"),
            ("remnawave_base_url", "https://panel.example"),
            ("remnawave_api_token", "integration-panel-token"),
            ("remnawave_active_internal_squads", "squad-test"),
        ):
            patches.enter_context(patch.object(settings, name, value))
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=settings.public_base_url)
        self.addAsyncCleanup(self.client.aclose)

    def telegram_header(self, user_id):
        fields = {
            "user": json.dumps({"id": user_id, "first_name": "Test"}),
            "auth_date": str(int(datetime.now(timezone.utc).timestamp())),
        }
        key = hmac.digest(b"WebAppData", settings.telegram_bot_token.encode(), "sha256")
        message = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
        fields["hash"] = hmac.digest(key, message.encode(), "sha256").hex()
        return {"X-Telegram-Init-Data": urlencode(fields)}

    async def test_previous_schema_and_paid_data_survive_migration(self):
        baseline = Path(__file__).resolve().parents[1] / "app/db/migrations/versions/0001_baseline.sql"
        async with self.engine.begin() as connection:
            for statement in baseline.read_text().split(";"):
                if statement.strip():
                    await connection.execute(text(statement.strip()))
            await connection.execute(text("INSERT INTO users (id, telegram_id) VALUES (5, 500)"))
            await connection.execute(text(
                "INSERT INTO payments (id, user_id, provider, amount, currency, status) "
                "VALUES (11, 5, 'telegram_stars', 40, 'XTR', 'paid')"
            ))
            await connection.execute(text("INSERT INTO subscriptions (id, user_id, status) VALUES (20, 5, 'active')"))
            await connection.run_sync(upgrade_database)
        async with self.engine.begin() as connection:
            await connection.run_sync(upgrade_database)
        async with self.sessions() as session:
            self.assertEqual((await session.get(Payment, 11)).status, "paid")
            self.assertIsNone((await session.get(Subscription, 20)).reminded_expiry)

    async def test_concurrent_web_checkouts_keep_their_selected_tariffs(self):
        async with self.engine.begin() as connection:
            await connection.run_sync(upgrade_database)
        async with self.sessions.begin() as session:
            session.add(User(id=1, telegram_id=501))
            session.add(TariffPlan(
                id=1, title="Basic", duration_days=45, traffic_gb=200, device_limit=3,
                price_rub_minor=10000, price_stars=120,
            ))
        terms = "https://shop.example/terms"
        bot = AsyncMock()
        bot.create_invoice_link.return_value = "https://t.me/payment"
        with patch.object(payment_service, "get_terms_url", new=AsyncMock(return_value=terms)), \
                patch.object(routes, "bot", new=bot):
            await asyncio.gather(payment_service.accept_current_terms(501),
                                 payment_service.accept_current_terms(501))
            fixed, flexible = await asyncio.gather(
                self.client.post("/api/v1/webapp/plans/checkout", headers=self.telegram_header(501),
                                 json={"plan_id": 1}),
                self.client.post("/api/v1/webapp/checkout", headers=self.telegram_header(501),
                                 json={"days": 30, "traffic": 90, "devices": 2}),
            )
        self.assertEqual(fixed.status_code, 200, fixed.text)
        self.assertEqual(flexible.status_code, 200, flexible.text)
        self.assertEqual(fixed.json()["stars"], 120)
        async with self.sessions() as session:
            fixed_payment = await session.get(Payment, fixed.json()["payment_id"])
            flexible_payment = await session.get(Payment, flexible.json()["payment_id"])
            self.assertEqual(json.loads(fixed_payment.payload)["tariff"]["duration_days"], 45)
            self.assertEqual(json.loads(flexible_payment.payload)["tariff"],
                             {"duration_days": 30, "traffic_gb": 90, "device_limit": 2})

    async def test_signed_checkout_recovery_and_customer_status(self):
        async with self.engine.begin() as connection:
            await connection.run_sync(upgrade_database)
        async with self.sessions.begin() as session:
            session.add(User(id=1, telegram_id=501))
            session.add(TariffPlan(
                id=1, title="Basic", duration_days=30, traffic_gb=100, device_limit=2,
                price_rub_minor=10000, price_stars=120,
            ))
        await store_settings.update_store_payment_settings(
            {"cryptopay": True}, {"cryptopay": {"api_token": "checkout-test-token"}},
        )
        receipt = AsyncMock()
        with patch.object(payment_service, "get_terms_url", new=AsyncMock(return_value="https://shop.example/terms")), \
                patch.object(storefront, "get_terms_url", new=AsyncMock(return_value="https://shop.example/terms")), \
                patch.object(payment_service, "get_store_tariff_pricing", new=AsyncMock(return_value={"currency": "RUB"})), \
                patch("app.services.payment_providers.CryptoPayProvider._request", new=AsyncMock(return_value={
                    "invoice_id": 441, "bot_invoice_url": "https://pay.example/invoice",
                })), \
                patch.object(payment_activation, "notify_payment_activated", new=receipt):
            self.client.cookies.set(storefront.COOKIE_NAME, issue_store_session(501, settings.app_secret_key))
            response = await self.client.post("/api/v1/store/checkout", headers={"Origin": settings.public_base_url},
                                              json={"plan_id": 1, "provider": "cryptopay", "accepted_terms": True})
            self.assertEqual(response.status_code, 200, response.text)
            order_id = response.json()["payment_id"]

            # An existing invoice may be paid after the provider is disabled in the shop.
            await store_settings.update_store_payment_settings({"cryptopay": False}, {})
            body = json.dumps({"update_type": "invoice_paid", "payload": {
                "payload": str(order_id), "invoice_id": 441, "status": "paid", "fiat": "RUB", "amount": "100.00",
            }}).encode()
            key = hashlib.sha256(b"checkout-test-token").digest()
            headers = {"crypto-pay-api-signature": hmac.digest(key, body, "sha256").hex()}
            for _ in range(2):
                response = await self.client.post("/webhooks/payments/cryptopay", content=body, headers=headers)
                self.assertEqual(response.status_code, 200, response.text)

            url = f"/api/v1/webapp/purchases/{order_id}"
            self.assertEqual((await self.client.get(url, headers=self.telegram_header(501))).json()["status"], "paid")
            self.assertEqual((await self.client.get(url, headers=self.telegram_header(502))).status_code, 404)

            panel_user = {}
            writes = []
            fail_after_write = True

            def remnawave(request):
                nonlocal fail_after_write
                if fail_after_write and request.method == "GET" and request.url.path.endswith("/users/22"):
                    fail_after_write = False
                    raise httpx.ReadTimeout("Response lost after successful write")
                if request.method == "GET" and not panel_user:
                    return httpx.Response(404)
                if request.method == "GET":
                    return httpx.Response(200, json={"response": panel_user})
                writes.append(request.method)
                panel_user.update(json.loads(request.content), id=22, subscriptionUrl="https://panel.example/sub/example")
                return httpx.Response(200, json={"response": panel_user})

            transport = httpx.MockTransport(remnawave)
            with patch.object(subscriptions, "RemnawaveClient", side_effect=lambda: RemnawaveClient(transport=transport)):
                await payment_activation.activate_payment(order_id)
                admin = self.telegram_header(900)
                response = await self.client.get("/admin/api/payments?status=paid&search=441", headers=admin)
                self.assertEqual(response.status_code, 200, response.text)
                entry, = response.json()["items"]
                self.assertEqual(entry["last_error"], "panel_timeout")
                self.assertEqual(entry["attempts"], 1)
                retry_url = f"/admin/api/payments/{order_id}/retry"
                self.assertEqual((await self.client.post(retry_url, headers=self.telegram_header(501))).status_code, 403)
                self.assertEqual((await self.client.post(retry_url, headers=admin)).status_code, 202)
                workers = [asyncio.create_task(payment_activation.run_activation_worker()) for _ in range(2)]
                try:
                    async with asyncio.timeout(10):
                        while not receipt.await_count:
                            await asyncio.sleep(0.05)
                finally:
                    for worker in workers:
                        worker.cancel()
                    await asyncio.gather(*workers, return_exceptions=True)
                self.assertEqual(writes, ["POST"])
                receipt.assert_awaited_once_with(order_id)
                self.assertEqual((await self.client.post(retry_url, headers=admin)).status_code, 409)

            order = (await self.client.get(url, headers=self.telegram_header(501))).json()
            self.assertEqual(order["status"], "succeeded")
            purchases = (await self.client.get("/api/v1/store/purchases")).json()
            self.assertEqual(purchases["items"][0]["status"], "succeeded")
            self.assertEqual(purchases["subscription"]["subscription_url"], "https://panel.example/sub/example")
            expiry = datetime.fromisoformat(panel_user["expireAt"])
            async with self.sessions.begin() as session:
                subscription = await session.scalar(select(Subscription))
                self.assertEqual(subscription.expires_at, expiry)
                self.assertEqual((await session.get(PaymentActivation, order_id)).attempts, 2)
                subscription.expires_at = datetime.now(timezone.utc) + timedelta(days=1)
            reminder = AsyncMock()
            await asyncio.gather(renewal_reminders.send_renewal_reminders(reminder, 3),
                                 renewal_reminders.send_renewal_reminders(reminder, 3))
            await renewal_reminders.send_renewal_reminders(reminder, 3)
            reminder.send_message.assert_awaited_once()
