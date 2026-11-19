import json
import unittest
from datetime import datetime, timezone

import httpx

from app.services.remnawave import RemnawaveClient


class RemnawaveClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_uses_filtered_user_list_when_legacy_telegram_lookup_is_absent(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/users/by-telegram-id/42":
                return httpx.Response(404)
            self.assertEqual(request.url.path, "/api/users")
            self.assertEqual(json.loads(request.url.params["filters"]), [{"id": "telegramId", "value": "42"}])
            return httpx.Response(200, json={"response": {"total": 1, "users": [{"id": 5, "telegramId": 42}]}})

        client = RemnawaveClient("https://panel.example", "token", httpx.MockTransport(handler))
        self.assertEqual((await client.get_users_by_telegram_id(42))["response"]["users"][0]["id"], 5)

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

    async def test_resolves_old_uuid_to_numeric_panel_v3_user_id(self):
        async def unused(): pass
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/users/by-username/tg_12345":
                return httpx.Response(200, json={"response": {"id": 1204, "username": "tg_12345"}})
            return httpx.Response(200, json={"response": {"devices": []}})
        client = RemnawaveClient("https://panel.example", "token", httpx.MockTransport(handler))
        self.assertEqual(await client.resolve_user_id("7e141bf3-cc24-4285-ad98-ac366edacb45",
                                                     username="tg_12345"), 1204)
        self.assertEqual(await client.resolve_user_id("1204", username="tg_12345"), 1204)
        self.assertEqual(await client.resolve_user_id("legacy-uuid"), "legacy-uuid")

    async def test_numeric_user_id_hwid_route_on_panel_3(self):
        seen = []
        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.path)
            return httpx.Response(200, json={"response": {"devices": []}})
        client = RemnawaveClient("https://panel.example", "token", httpx.MockTransport(handler))
        await client.get_hwid_devices(1204)
        self.assertEqual(seen, ["/api/hwid/devices/1204"])

    async def test_remove_device_uses_numeric_and_legacy_account_ids(self):
        seen = []
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual((request.method, request.url.path), ("POST", "/api/hwid/devices/delete"))
            seen.append(json.loads(request.content))
            return httpx.Response(200, json={"response": {"devices": [], "total": 0}})

        client = RemnawaveClient("https://panel.example", "token", httpx.MockTransport(handler))
        await client.delete_hwid_device(1204, "private-hardware-id")
        await client.delete_hwid_device("7e141bf3-cc24-4285-ad98-ac366edacb45", "private-hardware-id")
        self.assertEqual(seen, [
            {"userId": 1204, "hwid": "private-hardware-id"},
            {"userUuid": "7e141bf3-cc24-4285-ad98-ac366edacb45", "hwid": "private-hardware-id"},
        ])

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
