import asyncio
import re
import shutil
import subprocess
import unittest
from unittest.mock import patch

from fastapi import Request

from app.web.routes import admin_dashboard_page, mini_app_page
from app.web.storefront import store_page
from app.db.models import ReferralSettings, ReferralReward, RemnawaveEvent


class FrontendRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_admin_scripts_do_not_break_miniapp(self):
        mini = (await mini_app_page()).body.decode()
        admin = (await admin_dashboard_page()).body.decode()
        self.assertIn('loadReadyPlans()', mini)
        self.assertIn('loadAccountPanels()', mini)
        self.assertNotIn('async function adminApi(', mini)
        self.assertNotIn('id="referral-form"', mini)
        self.assertIn('async function adminApi(', admin)
        self.assertIn('id="referral-form"', admin)
        self.assertIn('async function loadReferrals()', admin)
        self.assertIn('id="brand-terms"', admin)

    async def test_storefront_and_miniapp_have_valid_javascript(self):
        if not shutil.which("node"):
            self.skipTest("node is not available")
        scope = {
            "type": "http", "method": "GET", "path": "/store",
            "headers": [(b"host", b"miniapp.example.com")],
            "scheme": "https", "server": ("miniapp.example.com", 443),
            "query_string": b"",
        }
        with patch("app.web.storefront.settings.public_base_url", "https://miniapp.example.com"):
            store = (await store_page(Request(scope))).body.decode()
        pages = {
            "Mini App": (await mini_app_page()).body.decode(),
            "Admin": (await admin_dashboard_page()).body.decode(),
            "External store": store,
        }
        for name, html in pages.items():
            snippets = re.findall(r"<script>(.*?)</script>", html, flags=re.S)
            self.assertTrue(snippets, f"No inline JS for {name}")
            for snippet in snippets:
                check = subprocess.run(["node", "--check"], input=snippet, text=True, capture_output=True)
                self.assertEqual(check.returncode, 0, f"{name}: {check.stderr}")

    def test_new_tables_start_without_demo_data(self):
        for cls in (ReferralSettings, ReferralReward, RemnawaveEvent):
            self.assertIn(cls.__tablename__, cls.metadata.tables)


if __name__ == "__main__":
    unittest.main()
