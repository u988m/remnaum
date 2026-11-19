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
        self.assertEqual(mini.count('<button type="button" data-page='), 5)
        for symbol in ("i-home", "i-shop", "i-shield", "i-gift", "i-user"):
            self.assertIn('href="#' + symbol + '"', mini)
        for page in ("devices", "guide"):
            self.assertIn('class="quick-action" type="button" data-go-page="' + page + '"', mini)
        self.assertIn('id="admin-sidebar"', admin)
        self.assertIn('id="admin-menu-backdrop"', admin)
        self.assertIn('id="settings-accordion"', admin)
        self.assertIn('id="revenue-count"', admin)
        self.assertIn('id="stars-count"', admin)
        self.assertIn('id="dashboard-payments"', admin)
        self.assertIn('async function overview()', admin_js)
        self.assertIn('function setAdminMenu(open)', admin_js)
        self.assertIn('class="admin-icon-sprite"', admin)
        self.assertIn('const sidebarIconNames =', admin_js)
        self.assertIn('id="ai-wallet"', admin)
        for section in ("referrals-view", "trial-view", "pricing-view", "payment-settings-view",
                        "payment-telegram_stars-view", "payment-yookassa-view",
                        "payment-yoomoney-view", "payment-cryptopay-view"):
            self.assertIn('id="' + section + '"', admin)
        for tab in ("referrals", "trial", "pricing", "pay-stars",
                    "pay-yookassa", "pay-yoomoney", "pay-cryptopay"):
            self.assertIn('data-tab="' + tab + '"', admin)
        self.assertIn('async function savePaymentProvider(provider)', admin_js)
        self.assertIn('methods:{[provider]:$("method-" + provider).checked}', admin_js)
        self.assertNotIn('id="payment-settings-save"', admin)
        self.assertNotIn('id="custom-plan"', mini)
        self.assertNotIn('id="buy"', mini)
        self.assertNotIn('function recalc()', mini_js)
        self.assertIn('id="hero-action"', mini)
        self.assertIn('id="hero-eyebrow"', mini)
        self.assertNotIn('async function adminApi(', mini_js)
        self.assertNotIn('id="referral-form"', mini)
        self.assertIn('async function adminApi(', admin_js)
        self.assertIn('id="referral-form"', admin)
        self.assertIn('async function loadReferrals()', admin_js)
        self.assertIn('id="brand-terms"', admin)
        self.assertIn('href="/static/miniapp.css"', mini)
        self.assertIn('src="/static/admin.js"', admin)
        self.assertIn("telegram-web-app.js", admin)
        self.assertIn('id="brand-photo-upload"', admin)
        self.assertIn('id="bot-identity"', admin)
        for panel in ('shop', 'vpn', 'bonuses', 'account'):
            self.assertIn(f'id="page-{panel}"', mini)
            self.assertIn(f'data-page="{panel}"', mini)
        for page in ("home", "order", "devices", "purchases", "support", "guide"):
            self.assertIn(f'id="page-{page}"', mini)
        for html in (mini, admin):
            self.assertIn('href="/static/theme.css"', html)
            self.assertIn('data-theme="monochrome"', html)
        self.assertNotIn('id="hero-image"', mini)
        self.assertNotIn('profile?.welcome_image_url', mini_js)
        self.assertIn('overflow:hidden; text-overflow:ellipsis;', (STATIC / "miniapp.css").read_text(encoding="utf-8"))
        for icon in ("i-device", "i-book", "i-support"):
            self.assertIn('id="' + icon + '"', mini)
        self.assertIn('id="copy-referral"', mini)

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

    def test_plan_payload_converts_rubles_without_rounding_and_accepts_legacy_keys(self):
        from pydantic import ValidationError
        from app.web.routes import PlanPayload

        current = {"name": "Test", "days": 7, "traffic_gb": 0, "devices_included": 2,
                   "price_rub": "199.90", "extra_device_price_rub": "49.05", "price_stars": 10}
        plan = PlanPayload.model_validate(current)
        self.assertEqual((plan.price_rub_minor, plan.extra_device_price_rub_minor), (19990, 4905))
        with self.assertRaises(ValidationError):
            PlanPayload.model_validate(current | {"extra_device_price_rub": "49.051"})
        legacy = PlanPayload.model_validate({
            "title": "Legacy", "duration_days": 30, "traffic_gb": 50, "device_limit": 1,
            "price_rub_minor": 19990, "price_stars": 10,
        })
        self.assertNotIn("extra_device_price_rub_minor", legacy.model_dump(exclude_unset=True))

    def test_new_tables_start_without_demo_data(self):
        for cls in (ReferralSettings, ReferralReward, RemnawaveEvent):
            self.assertIn(cls.__tablename__, cls.metadata.tables)


if __name__ == "__main__":
    unittest.main()
