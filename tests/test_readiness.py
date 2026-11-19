"""Readiness endpoint reports DB/Redis failures without exposing secrets."""
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from app.main import readiness


class ReadinessTests(unittest.IsolatedAsyncioTestCase):
    def _connection(self):
        connection = AsyncMock()
        connection.__aenter__.return_value = connection
        return connection

    async def test_healthy_dependencies(self):
        connection = self._connection()
        redis = AsyncMock()
        with patch("app.main.engine.connect", return_value=connection), \
             patch("app.main.aioredis.from_url", return_value=redis):
            self.assertEqual(await readiness(), {"status": "ready", "service": "remnaum"})
        redis.ping.assert_awaited_once()
        redis.aclose.assert_awaited_once()

    async def test_database_failure_is_503(self):
        connection = self._connection()
        connection.execute.side_effect = OSError("database offline")
        with patch("app.main.engine.connect", return_value=connection):
            with self.assertRaises(HTTPException) as context:
                await readiness()
        self.assertEqual(context.exception.status_code, 503)
        self.assertEqual(context.exception.detail, "Dependencies unavailable")

    async def test_redis_failure_is_503_and_closes_client(self):
        connection = self._connection()
        redis = AsyncMock()
        redis.ping.side_effect = OSError("redis offline")
        with patch("app.main.engine.connect", return_value=connection), \
             patch("app.main.aioredis.from_url", return_value=redis):
            with self.assertRaises(HTTPException) as context:
                await readiness()
        self.assertEqual(context.exception.status_code, 503)
        redis.aclose.assert_awaited_once()
