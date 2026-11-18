import hashlib
import hmac
import unittest

from app.services.store_auth import (
    issue_store_session, verify_store_session, verify_telegram_login,
)
from app.services.remnawave_events import STATUS_EVENTS, panel_subscription_id
from app.services.referrals import DEFAULT_REFERRAL_SETTINGS


class ExternalStoreAuthTests(unittest.TestCase):
    def test_signed_widget_identity_and_tampering(self):
        token = "bot-test-token"
        values = {"id": "12345", "first_name": "Ada", "auth_date": "1750000000"}
        message = "\n".join(f"{k}={v}" for k, v in sorted(values.items()))
        key = hashlib.sha256(token.encode()).digest()
        values["hash"] = hmac.new(key, message.encode(), hashlib.sha256).hexdigest()
        self.assertEqual(verify_telegram_login(values, token, now=1750000030)["id"], 12345)
        with self.assertRaises(ValueError):
            verify_telegram_login(values | {"first_name": "Mallory"}, token, now=1750000030)
        with self.assertRaises(ValueError):
            verify_telegram_login(values, token, now=1750001000)

    def test_signed_store_session_cannot_be_forged(self):
        key = "long-enough-session-secret-for-production"
        token = issue_store_session(12345, key, now=1750000000)
        self.assertEqual(verify_store_session(token, key, now=1750000001), 12345)
        with self.assertRaises(ValueError):
            verify_store_session(token, "another-session-secret-with-enough-length", now=1750000001)
        with self.assertRaises(ValueError):
            verify_store_session(token, key, now=1750050000)
        with self.assertRaises(ValueError):
            verify_store_session(None, key, now=1750000001)

    def test_referrals_disabled_until_owner_enables(self):
        self.assertFalse(DEFAULT_REFERRAL_SETTINGS["enabled"])
        self.assertGreater(DEFAULT_REFERRAL_SETTINGS["reward_days"], 0)

    def test_safe_panel_event_mapping_uses_panel_id_not_telegram_id(self):
        self.assertEqual(panel_subscription_id({"data": {"uuid": "abc", "telegramId": 123}}), "abc")
        self.assertIsNone(panel_subscription_id({"data": {"telegramId": 123}}))
        self.assertEqual(STATUS_EVENTS["user.expired"], "expired")
        self.assertNotIn("user.created", STATUS_EVENTS)


if __name__ == "__main__":
    unittest.main()
