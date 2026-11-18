import hashlib
import hmac
import json
import unittest

from app.services.remnawave_webhooks import verify_remnawave_webhook


class RemnawaveWebhookTests(unittest.TestCase):
    def test_valid_signature(self):
        body = json.dumps({"event": "user.modified", "scope": "user"}).encode()
        signature = hmac.new(b"my-secret", body, hashlib.sha256).hexdigest()
        self.assertEqual(verify_remnawave_webhook(body, signature, "my-secret")["event"], "user.modified")

    def test_rejects_tampering_and_missing_secret(self):
        body = b'{"event":"user.modified"}'
        with self.assertRaises(ValueError):
            verify_remnawave_webhook(body, "0" * 64, "my-secret")
        with self.assertRaises(ValueError):
            verify_remnawave_webhook(body, None, "my-secret")
        with self.assertRaises(ValueError):
            verify_remnawave_webhook(body, "0" * 64, "")


if __name__ == "__main__":
    unittest.main()
