import unittest

from app.services.store_settings import DEFAULT_METHODS, decrypt_credentials, encrypt_credentials, public_payment_settings


class PaymentSettingsTests(unittest.TestCase):
    def test_credentials_are_encrypted_and_round_trip(self):
        credentials = {"yookassa": {"shop_id": "shop", "secret_key": "private"}}
        token = encrypt_credentials(credentials)
        self.assertNotIn("private", token)
        self.assertEqual(decrypt_credentials(token), credentials)

    def test_stars_are_enabled_by_default(self):
        self.assertTrue(DEFAULT_METHODS["telegram_stars"])
        self.assertFalse(DEFAULT_METHODS["yookassa"])

    def test_public_settings_never_expose_credentials(self):
        result = public_payment_settings(DEFAULT_METHODS, {"yookassa": {"secret_key": "hidden"}})
        self.assertNotIn("credentials", result)
        self.assertTrue(result["configured"]["yookassa"])


if __name__ == "__main__":
    unittest.main()
