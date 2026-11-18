import unittest
from datetime import datetime, timezone

import httpx

from app.services.remnawave import RemnawaveClient


class RemnawaveClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_user_uses_bearer_auth_and_documented_payload(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["path"] = request.url.path
            seen["authorization"] = request.headers.get("Authorization")
            seen["payload"] = request.read().decode()
            return httpx.Response(201, json={"response": {"id": 42}})

        client = RemnawaveClient(
            "https://panel.example",
            "test-token",
            httpx.MockTransport(handler),
        )
        result = await client.create_user(
            "tg_12345",
            datetime(2026, 12, 1, tzinfo=timezone.utc),
            telegram_id=12345,
        )

        self.assertEqual(result["response"]["id"], 42)
        self.assertEqual(seen["method"], "POST")
        self.assertEqual(seen["path"], "/api/users")
        self.assertEqual(seen["authorization"], "Bearer test-token")
        self.assertIn('"username":"tg_12345"', seen["payload"])
        self.assertIn('"telegramId":12345', seen["payload"])

    async def test_legacy_uuid_user_updates(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.path
            seen["payload"] = request.read().decode()
            return httpx.Response(200, json={"response": {"uuid": "abcd-1234"}})

        client = RemnawaveClient("https://panel.example", "token", httpx.MockTransport(handler))
        await client.update_user("abcd-1234", traffic_limit_bytes=1024, hwid_device_limit=2)
        self.assertIn('"uuid":"abcd-1234"', seen["payload"])
        self.assertNotIn('"id":', seen["payload"])
        await client.get_subscription("abcd-1234")
        self.assertEqual(seen["path"], "/api/subscriptions/by-uuid/abcd-1234")

    async def test_extend_user_uses_days_body(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["path"] = request.url.path
            seen["payload"] = request.read().decode()
            return httpx.Response(200, json={"response": {"ok": True}})

        client = RemnawaveClient(
            "https://panel.example",
            "test-token",
            httpx.MockTransport(handler),
        )
        await client.extend_user(42, 30)

        self.assertEqual(seen["method"], "POST")
        self.assertEqual(seen["path"], "/api/users/42/actions/extend")
        self.assertEqual(seen["payload"], '{"days":30}')

    async def test_extend_user_rejects_non_positive_days(self):
        client = RemnawaveClient("https://panel.example", "test-token")
        with self.assertRaises(ValueError):
            await client.extend_user(42, 0)


    async def test_update_user_sends_numeric_id_and_limits(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["path"] = request.url.path
            seen["payload"] = request.read().decode()
            return httpx.Response(200, json={"response": {"id": 42}})

        client = RemnawaveClient(
            "https://panel.example",
            "test-token",
            httpx.MockTransport(handler),
        )
        await client.update_user(
            42,
            traffic_limit_bytes=1073741824,
            hwid_device_limit=3,
            expire_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
            description="remnaum-payment:12",
        )
        self.assertEqual(seen["method"], "PATCH")
        self.assertEqual(seen["path"], "/api/users")
        self.assertIn('"id":42', seen["payload"])
        self.assertIn('"trafficLimitBytes":1073741824', seen["payload"])
        self.assertIn('"hwidDeviceLimit":3', seen["payload"])
        self.assertIn('"status":"ACTIVE"', seen["payload"])


if __name__ == "__main__":
    unittest.main()
