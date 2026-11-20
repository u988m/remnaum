import asyncio
import hashlib
import hmac
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
from fastapi import FastAPI
from redis.asyncio import Redis

from app.services import release_notifications as notices
from app.web import routes

SECRET = "test-release-webhook-secret"
RELEASE = {
    "id": 197,
    "tag_name": "v0.0.9",
    "draft": False,
    "prerelease": False,
    "published_at": "2026-11-20T12:00:00Z",
}
EVENT = {
    "action": "published",
    "repository": {"full_name": notices.RELEASE_REPOSITORY},
    "release": RELEASE,
}


class ReleaseWebhookTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        app = FastAPI()
        app.include_router(routes.router)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://webhook.example.com")
        self.addAsyncCleanup(self.client.aclose)
        self.enterContext(patch.object(routes, "bot", SimpleNamespace(id=10)))
        self.enterContext(patch.object(routes.settings, "admin_ids", "42"))
        self.enterContext(patch.object(routes.settings, "github_release_webhook_secret", SECRET))
        self.enqueue = self.enterContext(patch.object(routes, "enqueue_release", new_callable=AsyncMock))
        self.enqueue.return_value = True

    async def send(self, event, *, key=SECRET, event_type="release"):
        body = json.dumps(event).encode()
        return await self.client.post("/webhooks/github/releases", content=body, headers={
            "X-GitHub-Event": event_type,
            "X-Hub-Signature-256": "sha256=" + hmac.new(key.encode(), body, hashlib.sha256).hexdigest(),
        })

    async def test_signed_publication_is_queued(self):
        response = await self.send(EVENT)
        self.assertEqual(response.json(), {"status": "queued"})
        self.enqueue.assert_awaited_once_with(10, notices.parse_release(RELEASE))

    async def test_unsigned_or_unrelated_events_do_not_queue(self):
        self.assertEqual((await self.send(EVENT, key="wrong")).status_code, 403)
        for event in (
            EVENT | {"action": "edited"},
            EVENT | {"repository": {"full_name": "other/remnaum"}},
            EVENT | {"release": RELEASE | {"draft": True}},
            EVENT | {"release": RELEASE | {"prerelease": True}},
            EVENT | {"release": RELEASE | {"tag_name": "another/path"}},
        ):
            self.assertEqual((await self.send(event)).json(), {"status": "ignored"})
        self.assertEqual((await self.send({}, event_type="ping")).status_code, 200)
        self.enqueue.assert_not_awaited()


@unittest.skipUnless(os.getenv("TEST_REDIS_URL"), "requires TEST_REDIS_URL")
class ReleaseDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        client = Redis.from_url(os.environ["TEST_REDIS_URL"], decode_responses=True)
        self.bot = SimpleNamespace(id=uuid4().int, send_message=AsyncMock())
        self.release = notices.parse_release(RELEASE)
        self.enterContext(patch.object(notices, "redis", client))
        self.enterContext(patch.object(notices, "_release_ready", asyncio.Event()))
        self.enterContext(patch.object(notices.settings, "admin_ids", "23,42"))

        async def clean():
            keys = [key async for key in client.scan_iter(f"remnaum:releases:{self.bot.id}:*")]
            if keys:
                await client.delete(*keys)
            await client.aclose()

        self.addAsyncCleanup(clean)

    async def test_webhook_wakes_delivery_worker_and_deduplicates_retries(self):
        delivered = asyncio.Event()

        async def send(admin_id, text, **kwargs):
            if admin_id == 42:
                delivered.set()

        self.bot.send_message.side_effect = send
        task = asyncio.create_task(notices._send_queued_releases(self.bot))

        async def stop():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        self.addAsyncCleanup(stop)
        await asyncio.sleep(0)
        self.assertTrue(await notices.enqueue_release(self.bot.id, self.release))
        await asyncio.wait_for(delivered.wait(), timeout=2)
        self.assertFalse(await notices.enqueue_release(self.bot.id, self.release))
        await notices.deliver_releases(self.bot)
        self.assertCountEqual([call.args[0] for call in self.bot.send_message.await_args_list], [23, 42])
        for call in self.bot.send_message.await_args_list:
            self.assertIn("Вышел релиз Remnaum v0.0.9", call.args[1])
            self.assertTrue(call.kwargs["reply_markup"].inline_keyboard[0][0].url.endswith("/tag/v0.0.9"))

    async def test_retry_keeps_delivery_progress(self):
        async def send(admin_id, *args, **kwargs):
            if admin_id == 42:
                raise TimeoutError

        self.bot.send_message.side_effect = send
        await notices.enqueue_release(self.bot.id, self.release)
        await notices.deliver_releases(self.bot)
        self.bot.send_message.reset_mock(side_effect=True)
        await asyncio.gather(notices.deliver_releases(self.bot), notices.deliver_releases(self.bot))
        await notices.deliver_releases(self.bot)
        self.bot.send_message.assert_awaited_once()
        self.assertEqual(self.bot.send_message.await_args.args[0], 42)

    async def test_fallback_notifies_only_a_release_published_after_monitoring_started(self):
        for published_at, expected in (
            (datetime.now(timezone.utc) - timedelta(days=1), 0),
            (datetime.now(timezone.utc) + timedelta(seconds=1), 2),
        ):
            fetched = asyncio.Event()

            def fetch(request):
                fetched.set()
                return httpx.Response(200, json=RELEASE | {"published_at": published_at.isoformat()})

            client = httpx.AsyncClient(transport=httpx.MockTransport(fetch))
            with patch.object(notices.httpx, "AsyncClient", return_value=client):
                task = asyncio.create_task(notices._poll_releases(self.bot.id))
                try:
                    await asyncio.wait_for(fetched.wait(), timeout=2)
                    if expected:
                        await asyncio.wait_for(notices._release_ready.wait(), timeout=2)
                    await notices.deliver_releases(self.bot)
                    self.assertEqual(self.bot.send_message.await_count, expected)
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
