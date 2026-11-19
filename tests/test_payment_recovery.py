"""Offline tests for the verified-payment recovery scheduler."""
import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.payment_recovery import recover_paid_payments_batch


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    def _session(self, *pages):
        session = AsyncMock()
        session.__aenter__.return_value = session
        session.scalars.side_effect = [MagicMock(all=MagicMock(return_value=ids)) for ids in pages]
        return session

    async def test_failed_payment_does_not_block_later_paid_orders(self):
        session = self._session([10, 11, 12])
        async def provision(payment_id):
            if payment_id == 10:
                raise RuntimeError("Remnawave temporarily unavailable")
            return payment_id == 11
        with patch("app.services.payment_recovery.SessionLocal", return_value=session), \
             patch("app.services.payment_recovery.provision_paid_payment", side_effect=provision) as provision_mock, \
             patch("app.services.payment_recovery.notify_external_payment_success", new_callable=AsyncMock) as notify:
            cursor = await recover_paid_payments_batch()
        self.assertEqual(cursor, 12)
        self.assertEqual(provision_mock.await_count, 3)
        notify.assert_awaited_once_with(11)

    async def test_scan_wraps_around_when_cursor_reaches_end(self):
        session = self._session([], [2, 3])
        with patch("app.services.payment_recovery.SessionLocal", return_value=session), \
             patch("app.services.payment_recovery.provision_paid_payment", new_callable=AsyncMock, return_value=False) as provision, \
             patch("app.services.payment_recovery.notify_external_payment_success", new_callable=AsyncMock) as notify:
            cursor = await recover_paid_payments_batch(after_id=99, limit=2)
        self.assertEqual(cursor, 3)
        self.assertEqual(provision.await_count, 2)
        notify.assert_not_awaited()
        self.assertEqual(session.scalars.await_count, 2)

    async def test_no_paid_orders(self):
        session = self._session([])
        with patch("app.services.payment_recovery.SessionLocal", return_value=session), \
             patch("app.services.payment_recovery.provision_paid_payment", new_callable=AsyncMock) as provision:
            cursor = await recover_paid_payments_batch()
        self.assertEqual(cursor, 0)
        provision.assert_not_awaited()

    async def test_invalid_limit_is_rejected(self):
        with self.assertRaises(ValueError):
            await recover_paid_payments_batch(limit=0)

    async def test_cancellation_propagates(self):
        session = self._session([1])
        with patch("app.services.payment_recovery.SessionLocal", return_value=session), \
             patch("app.services.payment_recovery.provision_paid_payment", new_callable=AsyncMock, side_effect=asyncio.CancelledError):
            with self.assertRaises(asyncio.CancelledError):
                await recover_paid_payments_batch()
