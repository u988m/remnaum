"""Ensure migration files are discoverable and the initial revision exists."""
import unittest
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


class MigrationRevisionTests(unittest.TestCase):
    def test_alembic_has_single_head(self):
        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        script = ScriptDirectory.from_config(config)
        self.assertEqual(script.get_heads(), ["0007_subscription_notifications"])
        self.assertEqual(script.get_revision("0007_subscription_notifications").down_revision, "0006_store_documents")
        self.assertEqual(script.get_revision("0006_store_documents").down_revision, "0005_device_aliases")
        self.assertEqual(script.get_revision("0005_device_aliases").down_revision, "0004_shop_presentation")
        self.assertEqual(script.get_revision("0004_shop_presentation").down_revision, "0003_customer_management")
        self.assertEqual(script.get_revision("0003_customer_management").down_revision, "0002_tariff_plan_options")
        self.assertEqual(script.get_revision("0002_tariff_plan_options").down_revision, "0001_legacy_baseline")
        self.assertEqual(script.get_revision("0001_legacy_baseline").down_revision, None)
