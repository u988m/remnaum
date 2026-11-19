"""PostgreSQL regression test: upgrade a populated pre-Alembic Remnaum schema.

Requires TEST_POSTGRES_URL; creates a disposable database and never touches an
existing shop or the CI 'postgres' database tables.
"""
import os
import secrets
import unittest

from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.base import Base
import app.db.models
from app.db.session import initialize_schema


@unittest.skipUnless(os.getenv("TEST_POSTGRES_URL"), "TEST_POSTGRES_URL is required")
class LegacyMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_rows_survive_migration_and_restart(self):
        base = make_url(os.environ["TEST_POSTGRES_URL"])
        name = "remnaum_migration_test_" + secrets.token_hex(6)
        admin_engine = create_async_engine(base, isolation_level="AUTOCOMMIT")
        test_engine = None
        try:
            async with admin_engine.connect() as admin:
                await admin.execute(text(f'CREATE DATABASE "{name}"'))
            test_engine = create_async_engine(base.set(database=name))
            async with test_engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
                await conn.execute(text("ALTER TABLE subscriptions DROP COLUMN paid_tariff"))
                await conn.execute(text(
                    "ALTER TABLE tariff_plans DROP COLUMN internal_squads, "
                    "DROP COLUMN extra_device_price_rub_minor, DROP COLUMN extra_device_price_stars, "
                    "DROP COLUMN badge, DROP COLUMN is_featured"
                ))
                await conn.execute(text(
                    "INSERT INTO tariff_plans "
                    "(title, description, duration_days, traffic_gb, device_limit, price_rub_minor, price_stars, is_active, sort_order) "
                    "VALUES ('Legacy', '', 30, 10, 1, 19000, 100, true, 0)"
                ))
                await conn.execute(text(
                    "INSERT INTO users (telegram_id, username) VALUES (12345670001, 'legacy')"
                ))

            for _ in range(2):  # upgrade, then simulate an app restart
                async with test_engine.begin() as conn:
                    await initialize_schema(conn)
                    user = (await conn.execute(text(
                        "SELECT username FROM users WHERE telegram_id = 12345670001"
                    ))).scalar_one()
                    revision = (await conn.execute(text(
                        "SELECT version_num FROM alembic_version"
                    ))).scalar_one()
                    subscription_columns = await conn.run_sync(
                        lambda c: {x["name"] for x in inspect(c).get_columns("subscriptions")}
                    )
                    plan_columns = await conn.run_sync(
                        lambda c: {x["name"] for x in inspect(c).get_columns("tariff_plans")}
                    )
                    self.assertEqual(user, "legacy")
                    self.assertEqual(revision, "0005_device_aliases")
                    legacy_plan = (await conn.execute(text(
                        "SELECT title, price_rub_minor, extra_device_price_rub_minor, badge, is_featured FROM tariff_plans"
                    ))).one()
                    self.assertEqual(tuple(legacy_plan), ("Legacy", 19000, None, "", False))
                    self.assertIn("paid_tariff", subscription_columns)
                    self.assertIn("internal_squads", plan_columns)
        finally:
            if test_engine is not None:
                await test_engine.dispose()
            try:
                async with admin_engine.connect() as admin:
                    await admin.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            finally:
                await admin_engine.dispose()
