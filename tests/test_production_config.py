"""Production configuration must never silently use demonstration secrets."""
import unittest
from pydantic import ValidationError

from app.config import Settings


class ProductionConfigTests(unittest.TestCase):
    def test_reject_example_secrets(self):
        for value in ("development-only-change-me", "replace-with-a-long-random-secret", "too-short"):
            with self.subTest(value=value), self.assertRaisesRegex(ValidationError, "APP_SECRET_KEY"):
                Settings(_env_file=None, app_env="production", app_secret_key=value)

    def test_allow_generated_production_secret(self):
        config = Settings(_env_file=None, app_env="production", app_secret_key="7c" * 32)
        self.assertEqual(config.app_env, "production")

    def test_webhook_requires_secret_and_https(self):
        base = dict(_env_file=None, app_env="production",
                    app_secret_key="7c" * 32, telegram_use_webhook=True,
                    telegram_webhook_url="https://webhook.example.com/telegram/webhook")
        with self.assertRaisesRegex(ValidationError, "TELEGRAM_WEBHOOK_SECRET"):
            Settings(**base)
        with self.assertRaisesRegex(ValidationError, "HTTPS"):
            Settings(**(base | {"telegram_webhook_secret": "9b" * 32,
                                 "telegram_webhook_url": "http://example.com/telegram/webhook"}))
        config = Settings(**(base | {"telegram_webhook_secret": "9b" * 32}))
        self.assertTrue(config.telegram_use_webhook)

    def test_development_defaults_still_allowed(self):
        config = Settings(_env_file=None, app_env="development",
                          app_secret_key="development-only-change-me")
        self.assertEqual(config.app_env, "development")
