import asyncio
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import StorePaymentSettings
from app.services import store_settings
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
        result = public_payment_settings(DEFAULT_METHODS, {"yookassa": {"shop_id": "shop", "secret_key": "hidden"}})
        self.assertNotIn("credentials", result)
        self.assertTrue(result["configured"]["yookassa"])


@unittest.skipUnless(os.getenv("TEST_POSTGRES_URL"), "requires TEST_POSTGRES_URL")
class PaymentSettingsPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        schema = "payment_settings_" + uuid4().hex
        engine = create_async_engine(os.environ["TEST_POSTGRES_URL"], connect_args={
            "server_settings": {"search_path": schema},
        })
        self.addAsyncCleanup(engine.dispose)
        async with engine.begin() as connection:
            await connection.execute(text(f"CREATE SCHEMA {schema}"))
            await connection.run_sync(StorePaymentSettings.__table__.create)

        async def cleanup():
            async with engine.begin() as connection:
                await connection.execute(text(f"DROP SCHEMA {schema} CASCADE"))

        self.addAsyncCleanup(cleanup)
        self.sessions = async_sessionmaker(engine)
        self.enterContext(patch.object(store_settings, "SessionLocal", self.sessions))

    async def test_saving_stars_preserves_unreadable_external_credentials(self):
        async with self.sessions.begin() as session:
            session.add(StorePaymentSettings(
                id=1, methods={"telegram_stars": False, "yookassa": True},
                encrypted_credentials="encrypted-with-a-different-key",
            ))

        saved = await store_settings.update_store_payment_settings({"telegram_stars": True}, {})
        self.assertTrue(saved["methods"]["telegram_stars"])
        self.assertFalse(saved["configured"]["yookassa"])
        self.assertTrue(await store_settings.is_payment_method_enabled("telegram_stars"))
        async with self.sessions() as session:
            row = await session.get(StorePaymentSettings, 1)
            self.assertEqual(row.encrypted_credentials, "encrypted-with-a-different-key")

    async def test_saving_providers_concurrently_preserves_each_update(self):
        credentials = {"yookassa": {"shop_id": "shop", "secret_key": "test-key"}}
        await asyncio.gather(
            store_settings.update_store_payment_settings({"yookassa": True}, credentials),
            store_settings.update_store_payment_settings({"telegram_stars": False}, {}),
        )
        methods, stored = await store_settings.get_store_payment_settings()
        self.assertTrue(methods["yookassa"])
        self.assertFalse(methods["telegram_stars"])
        self.assertEqual(stored, credentials)

    async def test_editing_display_names_and_order_preserves_flags_and_credentials(self):
        credentials = {"yookassa": {"shop_id": "shop", "secret_key": "private"}}
        await store_settings.update_store_payment_settings({"yookassa": True}, credentials)
        presentation = [{"provider": "yookassa", "label": "Банковская карта"},
                        {"provider": "telegram_stars", "label": ""}]
        saved = await store_settings.update_store_payment_settings({}, {}, presentation=presentation)
        self.assertEqual(saved["presentation"][:2], [presentation[0], {"provider": "telegram_stars", "label": "Telegram Stars"}])
        self.assertEqual(await store_settings.get_payment_presentation(), saved["presentation"])
        methods, stored = await store_settings.get_store_payment_settings()
        self.assertTrue(methods["yookassa"])
        self.assertEqual(stored, credentials)
        with self.assertRaises(ValueError):
            await store_settings.update_store_payment_settings({}, {}, presentation=[presentation[0], presentation[0]])


if __name__ == "__main__":
    unittest.main()
