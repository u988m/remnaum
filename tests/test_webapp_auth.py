import hashlib
import hmac
import json
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from app.services.telegram_webapp import validate_telegram_init_data


class TelegramWebAppAuthTests(unittest.TestCase):
    def make_init_data(self, token, user, auth_date=None):
        values = {"auth_date": str(auth_date or int(time.time())), "user": json.dumps(user, separators=(",", ":"))}
        check_string = "\n".join(f"{key}={value}" for key, value in sorted(values.items()))
        secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
        values["hash"] = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
        return urlencode(values)

    def test_accepts_valid_signature(self):
        token = "test-token"
        data = self.make_init_data(token, {"id": 12345, "first_name": "Test"})
        with patch("app.services.telegram_webapp.settings.telegram_bot_token", token):
            self.assertEqual(validate_telegram_init_data(data)["id"], 12345)

    def test_rejects_tampered_data(self):
        token = "test-token"
        data = self.make_init_data(token, {"id": 12345}) + "&query_id=changed"
        with patch("app.services.telegram_webapp.settings.telegram_bot_token", token):
            with self.assertRaises(ValueError):
                validate_telegram_init_data(data)

    def test_rejects_expired_data(self):
        token = "test-token"
        data = self.make_init_data(token, {"id": 12345}, int(time.time()) - 90000)
        with patch("app.services.telegram_webapp.settings.telegram_bot_token", token):
            with self.assertRaises(ValueError):
                validate_telegram_init_data(data)


if __name__ == "__main__":
    unittest.main()
