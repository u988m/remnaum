import hashlib
import hmac
import os
import time
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import SendMessage
from fastapi import FastAPI
import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bot import campaigns
from app.db.models import TariffPlan, User
from app.db.session import initialize_schema
from app.services import store_operations, store_profile, support, users
from app.services.plans import quote_plan
from app.web import browser_auth, routes, storefront


class PlanAddonTests(unittest.TestCase):
    def test_configurable_addons_are_separate_from_devices_included(self):
        plan = TariffPlan(title="VPN", is_active=True, duration_days=30, traffic_gb=0, device_limit=3,
                          price_rub_minor=1000, price_stars=20, extra_device_price_rub_minor=100,
                          extra_device_price_stars=None)
        order = quote_plan(plan, 15, slots=15)
        self.assertEqual((order.device_limit, order.amount_minor, order.stars), (18, 2500, None))
        with self.assertRaisesRegex(ValueError, "количество"):
            quote_plan(plan, 15, slots=14)


@unittest.skipUnless(os.getenv("TEST_POSTGRES_URL"), "requires TEST_POSTGRES_URL")
class BrowserStoreOperationsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        schema = "operations_" + uuid4().hex
        engine = create_async_engine(os.environ["TEST_POSTGRES_URL"], connect_args={
            "server_settings": {"search_path": schema},
        })
        self.addAsyncCleanup(engine.dispose)
        async with engine.begin() as connection:
            await connection.execute(text(f"CREATE SCHEMA {schema}"))
            await initialize_schema(connection)

        async def cleanup():
            async with engine.begin() as connection:
                await connection.execute(text(f"DROP SCHEMA {schema} CASCADE"))

        self.addAsyncCleanup(cleanup)
        self.sessions = async_sessionmaker(engine, expire_on_commit=False)
        self.store = {}
        self.site = "https://miniapp.example.test"
        self.bot = AsyncMock(id=100)
        app = FastAPI()
        app.include_router(routes.router)
        app.include_router(browser_auth.router)
        app.include_router(storefront.router)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=self.site)
        self.addAsyncCleanup(self.client.aclose)
        with ExitStack() as stack:
            for module in (browser_auth, routes, store_operations, store_profile, support, users, campaigns):
                stack.enter_context(patch.object(module, "SessionLocal", self.sessions))
            stack.enter_context(patch.object(routes, "bot", self.bot))
            stack.enter_context(patch.object(routes, "validate_telegram_init_data", side_effect=lambda value: {"id": int(value)}))
            stack.enter_context(patch.object(browser_auth, "redis", SimpleNamespace(
                set=AsyncMock(side_effect=lambda key, value, **kwargs: self.store.__setitem__(key, value)),
                getdel=AsyncMock(side_effect=lambda key: self.store.pop(key, None)),
            )))
            for key, value in {
                "webapp_url": self.site + "/app", "app_secret_key": "store-operations-tests-secret-key",
                "telegram_bot_token": "100:test", "telegram_bot_username": "test_remnaum_bot", "admin_ids": "42",
            }.items():
                stack.enter_context(patch.object(routes.settings, key, value))
            self.addCleanup(stack.pop_all().close)

    async def telegram_sign_in(self, telegram_id, path="/auth"):
        page = await self.client.get(path)
        self.assertEqual(page.status_code, 200)
        callback = page.text.split('data-auth-url="', 1)[1].split('"', 1)[0]
        fields = {"id": str(telegram_id), "auth_date": str(int(time.time())), "username": "owner"}
        message = "\n".join(f"{key}={value}" for key, value in sorted(fields.items())).encode()
        fields["hash"] = hmac.new(hashlib.sha256(b"100:test").digest(), message, hashlib.sha256).hexdigest()
        response = await self.client.get(callback, params=fields)
        self.assertEqual(response.status_code, 303, response.text)
        self.assertEqual((await self.client.get(callback, params=fields)).status_code, 400)
        return response.headers["location"]

    async def test_google_links_existing_profile_and_cannot_access_admin(self):
        await store_operations.save_operations(
            {"google_login": True, "google_client_id": "test-client"}, {"google_client_secret": "test-secret"},
        )
        self.assertEqual((await self.client.get("/")).status_code, 200)
        self.assertEqual(await self.telegram_sign_in(42, "/auth?admin=true&ticket=11"), "/admin?tab=tickets&ticket=11")
        admin = await self.client.get("/admin/api/operations")
        self.assertEqual(admin.status_code, 200, admin.text)
        self.assertNotIn("test-secret", admin.text)
        origin = {"Origin": self.site}
        theme = {"colors": {"bg": "#112233"}}
        self.assertEqual((await self.client.put("/admin/api/theme", json=theme)).status_code, 403)
        self.assertEqual((await self.client.put("/admin/api/theme", json=theme, headers=origin)).status_code, 200)

        async def google_callback(*, link):
            path = "/auth/google?link=true" if link else "/auth/google?ticket=11"
            start = await self.client.get(path)
            self.assertEqual(start.status_code, 307, start.text)
            params = parse_qs(urlsplit(start.headers["location"]).query)
            self.assertEqual(params["code_challenge_method"], ["S256"])
            google = AsyncMock()
            google.post.return_value = httpx.Response(
                200, json={"access_token": "test-token"}, request=httpx.Request("POST", "https://oauth2.googleapis.com/token"),
            )
            google.get.return_value = httpx.Response(
                200, json={"sub": "google-subject"}, request=httpx.Request("GET", "https://openidconnect.googleapis.com/v1/userinfo"),
            )
            context = AsyncMock()
            context.__aenter__.return_value = google
            with patch.object(browser_auth.httpx, "AsyncClient", return_value=context):
                finish = await self.client.get("/auth/google/callback", params={"state": params["state"][0], "code": "test"})
            self.assertEqual(finish.status_code, 303, finish.text)
            self.assertEqual(finish.headers["location"], "/" if link else "/?section=tickets&ticket=11")
            self.assertTrue(google.post.call_args.kwargs["data"]["code_verifier"])

        await google_callback(link=True)
        await self.client.post("/auth/logout", headers=origin)
        await google_callback(link=False)
        self.assertEqual((await self.client.get("/api/v1/webapp/theme")).json(), theme)
        self.assertEqual((await self.client.get("/admin/api/operations")).status_code, 403)
        self.assertEqual((await self.client.put("/admin/api/theme", json=theme, headers=origin)).status_code, 403)

    async def test_browser_domain_separates_login_from_miniapp(self):
        browser = "https://web.example.test"
        with patch.object(routes.settings, "browser_webapp_url", browser), \
             patch.object(routes.settings, "public_base_url", browser):
            self.assertEqual((await self.client.get("/")).status_code, 200)
            response = await self.client.get("/auth?ticket=11")
            self.assertEqual(response.headers["location"], browser + "/auth?ticket=11")
            self.assertEqual((await self.client.get("/auth/google?ticket=11")).status_code, 404)
            self.assertEqual((await self.client.get(browser + "/")).status_code, 200)
            self.assertEqual((await self.client.get("/store")).headers["location"], browser + "/store")
            self.assertEqual((await self.client.get(browser + "/store")).status_code, 200)
            login = await self.client.get(browser + "/auth")
            self.assertEqual(login.status_code, 200)
            self.assertIn(f'data-auth-url="{browser}/auth/telegram/', login.text)
            admin = {"X-Telegram-Init-Data": "42"}
            config = (await self.client.get("/admin/api/operations", headers=admin)).json()
            self.assertEqual(config["browser_origin"], browser)
            self.assertEqual(config["google_callback_url"], browser + "/auth/google/callback")

    async def test_login_settings_are_independent_of_other_integrations(self):
        admin = {"X-Telegram-Init-Data": "42"}
        google = {"enabled": True, "client_id": "test-client", "client_secret": "test-secret"}
        self.assertEqual((await self.client.put("/admin/api/login/google", json=google)).status_code, 401)
        self.assertEqual((await self.client.put("/admin/api/login/google", headers=admin, json=google)).status_code, 200)
        self.assertEqual((await self.client.put("/admin/api/login/telegram", headers=admin, json={"enabled": False})).status_code, 200)
        operations = {"required_channel": "", "channel_url": "", "extra_devices_max": 30, "panel_url": ""}
        self.assertEqual((await self.client.put("/admin/api/operations", headers=admin, json=operations)).status_code, 200)
        result = await store_operations.get_operations(credentials=True)
        self.assertFalse(result["telegram_login"])
        self.assertTrue(result["google_login"])
        self.assertEqual(result["google_client_id"], "test-client")
        self.assertEqual(result["google_client_secret"], "test-secret")
        self.assertEqual(result["extra_devices_max"], 30)

    async def test_palette_is_global_and_has_separate_support_settings(self):
        guest = {"X-Telegram-Init-Data": "110"}
        admin = {"X-Telegram-Init-Data": "42"}
        theme = {"colors": {"primary": "#45AABB", "overlay": "#123456"}}
        self.assertEqual((await self.client.put("/api/v1/webapp/theme", headers=guest, json=theme)).status_code, 405)
        self.assertEqual((await self.client.put("/admin/api/theme", headers=guest, json=theme)).status_code, 403)
        saved = await self.client.put("/admin/api/theme", headers=admin, json=theme)
        self.assertEqual(saved.status_code, 200, saved.text)
        colors = {"primary": "#45aabb", "overlay": "#123456"}
        self.assertEqual((await self.client.get("/api/v1/webapp/theme", headers=guest)).json(), {"colors": colors})
        invalid = await self.client.put("/admin/api/theme", headers=admin, json={"colors": {"unknown": "#000000"}})
        self.assertEqual(invalid.status_code, 422)
        css = await self.client.get("/store-theme.css")
        self.assertIn("--color-primary:#45aabb;", css.text)
        self.assertIn("--color-overlay:#123456eb;", css.text)
        self.assertEqual(css.headers["content-type"], "text/css; charset=utf-8")

        contact = await self.client.put("/admin/api/support-settings", headers=admin, json={"support_contact": " @help "})
        self.assertEqual(contact.json(), {"support_contact": "@help"})
        await self.client.put("/admin/api/store-profile", headers=admin, json={"name": "Store"})
        profile = (await self.client.get("/api/v1/store-profile")).json()
        self.assertEqual(profile["theme_colors"], colors)
        self.assertEqual(profile["support_contact"], "@help")
        await self.client.put("/admin/api/theme", headers=admin, json={"colors": {}})
        self.assertNotIn("--color-primary", (await self.client.get("/store-theme.css")).text)

    async def test_ticket_ownership_replies_and_channel_revocation(self):
        headers = {"X-Telegram-Init-Data": "110"}
        payload = {"subject": "Подписка", "body": "Не открывается"}
        created = await self.client.post("/api/v1/webapp/tickets", headers=headers, json=payload)
        self.assertEqual(created.status_code, 201, created.text)
        ticket_id = created.json()["id"]
        notification = self.bot.send_message.call_args
        self.assertIn(f"№{ticket_id}", notification.args[1])
        button = notification.kwargs["reply_markup"].inline_keyboard[0][0]
        self.assertEqual(button.web_app.url, f"{self.site}/admin?tab=tickets&ticket={ticket_id}")
        ticket = f"/api/v1/webapp/tickets/{ticket_id}"
        other = {"X-Telegram-Init-Data": "111"}
        self.assertEqual((await self.client.get(ticket, headers=other)).status_code, 404)
        self.assertEqual((await self.client.put(ticket, headers=other, json={"closed": True})).status_code, 404)
        admin = {"X-Telegram-Init-Data": "42"}
        endpoint = f"/admin/api/tickets/{ticket_id}"
        answer = await self.client.post(endpoint + "/replies", headers=admin, json={"body": "Проверьте ещё раз"})
        self.assertEqual(answer.status_code, 200, answer.text)
        notification = self.bot.send_message.call_args
        self.assertEqual(notification.args[0], 110)
        button = notification.kwargs["reply_markup"].inline_keyboard[0][0]
        self.assertEqual(button.web_app.url, f"{self.site}/?section=tickets&ticket={ticket_id}")
        messages = (await self.client.get(ticket, headers=headers)).json()["replies"]
        self.assertEqual([message["from_admin"] for message in messages], [False, True])

        closed = await self.client.put(ticket, headers=headers, json={"closed": True})
        self.assertEqual(closed.json(), {"closed": True})
        self.assertIn("закрыто", self.bot.send_message.call_args.args[1])
        notifications = self.bot.send_message.await_count
        await self.client.put(ticket, headers=headers, json={"closed": True})
        self.assertEqual(self.bot.send_message.await_count, notifications)
        self.assertEqual((await self.client.post(endpoint + "/replies", headers=admin, json={"body": "Ответ"})).status_code, 409)
        archive = await self.client.get("/api/v1/webapp/tickets", headers=headers, params={"state": "closed", "search": f"№{ticket_id}"})
        self.assertEqual([item["id"] for item in archive.json()["items"]], [ticket_id])
        self.assertEqual((await self.client.get("/api/v1/webapp/tickets?state=open", headers=headers)).json()["items"], [])
        self.assertEqual((await self.client.get(ticket, headers=headers)).json()["replies"], messages)
        self.assertEqual((await self.client.put(endpoint, headers=admin, json={"closed": False})).json(), {"closed": False})

        await store_operations.save_operations({"required_channel": "@test_channel", "channel_url": "https://t.me/test_channel"}, {})
        self.bot.get_chat_member.return_value = SimpleNamespace(status="member")
        self.assertEqual((await self.client.get(ticket, headers=headers)).status_code, 200)
        self.bot.get_chat_member.return_value = SimpleNamespace(status="left")
        self.assertEqual((await self.client.get(ticket, headers=headers)).status_code, 403)
        self.assertEqual((await self.client.put(endpoint, headers=admin, json={"closed": True})).status_code, 200)

    async def test_migration_preserves_existing_ticket_history(self):
        async with self.sessions.begin() as session:
            await session.execute(text("ALTER TABLE support_tickets DROP COLUMN updated_at"))
            await session.execute(text("UPDATE alembic_version SET version_num = '0008_store_operations'"))
            user_id = await session.scalar(text("INSERT INTO users (telegram_id) VALUES (110) RETURNING id"))
            ticket_id = await session.scalar(text(
                "INSERT INTO support_tickets (user_id, subject, closed, created_at) "
                "VALUES (:user_id, 'История', true, '2026-01-01') RETURNING id"
            ), {"user_id": user_id})
            await session.execute(text(
                "INSERT INTO support_replies (ticket_id, from_admin, body, created_at) "
                "VALUES (:ticket_id, true, 'Ответ', '2026-02-01')"
            ), {"ticket_id": ticket_id})
        async with self.sessions.begin() as session:
            await initialize_schema(await session.connection())
        data = await support.get_ticket(ticket_id, 110)
        self.assertTrue(data["closed"])
        self.assertEqual(data["updated_at"].strftime("%Y-%m-%d"), "2026-02-01")
        self.assertEqual(data["replies"][0]["body"], "Ответ")

    async def test_broadcast_skips_known_blocks_and_records_new_ones(self):
        async with self.sessions.begin() as session:
            session.add_all(User(telegram_id=value) for value in (110, 111, 112))
        await users.set_bot_blocked(110, True)

        async def send(chat_id, message, **kwargs):
            if chat_id == 111:
                raise TelegramForbiddenError(SendMessage(chat_id=chat_id, text=message), "bot was blocked")

        self.bot.send_message.side_effect = send
        lock = AsyncMock()
        with patch.object(campaigns, "get_store_profile", return_value={"name": "Test"}), \
             patch.object(campaigns, "list_document_kinds", return_value=[]):
            await campaigns.run_campaign(self.bot, 42, "Сообщение", lock)
        sent_to = [call.args[0] for call in self.bot.send_message.await_args_list]
        self.assertEqual(sent_to, [111, 112, 42])
        async with self.sessions() as session:
            blocked = await session.scalars(select(User.telegram_id).where(User.bot_blocked_at.is_not(None)))
            self.assertEqual(set(blocked), {110, 111})
        lock.release.assert_awaited_once()
