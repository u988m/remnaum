import re
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import Request

from app.web.routes import admin_dashboard_page, mini_app_page
from app.web.storefront import store_page
from app.db.models import ReferralSettings, ReferralReward, RemnawaveEvent

STATIC = Path(__file__).resolve().parents[1] / "app" / "web" / "static"


class FrontendRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_admin_scripts_do_not_break_miniapp(self):
        mini = (await mini_app_page()).body.decode()
        admin = (await admin_dashboard_page()).body.decode()
        mini_js = (STATIC / "miniapp.js").read_text(encoding="utf-8")
        admin_js = (STATIC / "admin.js").read_text(encoding="utf-8")
        self.assertIn('loadReadyPlans()', mini_js)
        self.assertIn('loadAccountPanels()', mini_js)
        self.assertIn('fillPaymentOptions()', mini_js)
        self.assertNotIn('async function adminApi(', mini_js)
        self.assertNotIn('id="referral-form"', mini)
        self.assertIn('async function adminApi(', admin_js)
        self.assertIn('id="referral-form"', admin)
        self.assertIn('async function loadReferrals()', admin_js)
        self.assertIn('id="brand-terms"', admin)
        self.assertIn('href="/static/miniapp.css"', mini)
        self.assertIn('src="/static/admin.js"', admin)

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
        self.assertIn('src="/static/store.js"', store)
        self.assertIn('id="login"', store)
        for name in ("miniapp", "admin", "store"):
            js = (STATIC / f"{name}.js").read_text(encoding="utf-8")
            check = subprocess.run(["node", "--check"], input=js, text=True, capture_output=True)
            self.assertEqual(check.returncode, 0, f"{name}: {check.stderr}")
            self.assertTrue((STATIC / f"{name}.css").is_file())

    def test_new_tables_start_without_demo_data(self):
        for cls in (ReferralSettings, ReferralReward, RemnawaveEvent):
            self.assertIn(cls.__tablename__, cls.metadata.tables)


if __name__ == "__main__":
    unittest.main()
