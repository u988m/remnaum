from starlette.requests import Request
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
        mini = (await mini_app_page(Request({"type": "http", "headers": []}))).body.decode()
        admin = (await admin_dashboard_page(Request({"type": "http", "headers": []}))).body.decode()
        mini_js = (STATIC / "miniapp.js").read_text(encoding="utf-8")
        admin_js = (STATIC / "admin.js").read_text(encoding="utf-8")
        self.assertIn('loadReadyPlans()', mini_js)
        self.assertIn('loadPurchases()', mini_js)
        self.assertIn('id="renew-plan"', mini)
        self.assertIn('id="repeat-plan"', mini)
        self.assertEqual(mini.count('<button type="button" data-page='), 4)
        for symbol in ("i-home", "i-device", "i-help", "i-gift", "i-user"):
            self.assertIn('href="#' + symbol + '"', mini)
        self.assertEqual(mini.count('data-go-page="guide"'), 1)
        self.assertIn('data-page="vpn" aria-current="page"', mini)
        self.assertIn('id="device-capacity"', mini)
        self.assertIn('id="device-editor"', mini)
        self.assertIn('<dialog class="order-dialog" id="page-order"', mini)
        self.assertIn('aria-label="Добавить одно устройство"', mini)
        self.assertIn('id="order-method-options"', mini)
        self.assertIn('id="admin-sidebar"', admin)
        self.assertIn('id="admin-menu-backdrop"', admin)
        self.assertIn('id="configuration-view"', admin)
        self.assertIn('id="admin-back"', admin)
        self.assertIn('data-back-tab="settings"', admin)
        self.assertIn('id="revenue-count"', admin)
        self.assertIn('id="stars-count"', admin)
        self.assertIn('id="dashboard-payments"', admin)
        self.assertNotIn('class="toolbar dashboard-actions"', admin)
        self.assertIn('async function overview()', admin_js)
        self.assertIn('function setAdminMenu(open)', admin_js)
        self.assertIn('class="admin-icon-sprite"', admin)
        self.assertIn('const sidebarIconNames =', admin_js)
        self.assertIn('id="ai-wallet"', admin)
        for section in ("referrals-view", "trial-view", "payment-settings-view",
                        "payment-telegram_stars-view", "payment-yookassa-view",
                        "payment-yoomoney-view", "payment-cryptopay-view", "payment-platega-view"):
            self.assertIn('id="' + section + '"', admin)
        for tab in ("referrals", "configuration"):
            self.assertIn('data-tab="' + tab + '"', admin)
        for tab in ("trial", "settings"):
            self.assertIn('data-open-tab="' + tab + '"', admin)
        self.assertNotIn('id="payment-diagnostics-refresh"', admin)
        self.assertNotIn('id="payment-terms-state"', admin)
        self.assertNotIn('id="pricing-view"', admin)
        self.assertIn('id="customer-subscription-form"', admin)
        self.assertIn('id="customer-delete-form"', admin)
        self.assertIn('data-provider-form="platega"', admin)
        self.assertIn('"pay-platega"', admin_js)
        self.assertIn('async function savePaymentProvider(provider)', admin_js)
        self.assertIn('methods:{[provider]:toggle.checked}', admin_js)
        self.assertNotIn('id="payment-settings-save"', admin)
        self.assertNotIn('id="custom-plan"', mini)
        self.assertNotIn('id="buy"', mini)
        self.assertNotIn('function recalc()', mini_js)
        self.assertNotIn('id="page-home"', mini)
        self.assertNotIn('async function adminApi(', mini_js)
        self.assertNotIn('id="referral-form"', mini)
        self.assertIn('async function adminApi(', admin_js)
        self.assertIn('id="referral-form"', admin)
        self.assertIn('async function loadReferrals()', admin_js)
        self.assertIn('id="brand-terms"', admin)
        self.assertIn('href="/static/miniapp.css?v=', mini)
        self.assertIn('src="/static/admin.js?v=', admin)
        self.assertRegex(admin, r'<div class="sidebar-actions">\s*<a[^>]+href="/"[^>]*aria-label="Магазин"')
        self.assertIn("telegram-web-app.js", admin)
        self.assertIn('id="brand-photo-upload"', admin)
        self.assertIn('id="bot-identity"', admin)
        for panel in ('shop', 'vpn', 'bonuses', 'account'):
            self.assertIn(f'id="page-{panel}"', mini)
            if panel != 'shop':
                self.assertIn(f'data-page="{panel}"', mini)
        for page in ("order", "devices", "purchases", "support", "guide"):
            self.assertIn(f'id="page-{page}"', mini)
        for html in (mini, admin):
            self.assertIn('href="/static/theme.css"', html)
            self.assertIn('data-theme="monochrome"', html)
            self.assertIn('maximum-scale=1,user-scalable=no', html)
        self.assertNotIn('id="hero-image"', mini)
        self.assertNotIn('profile?.welcome_image_url', mini_js)
        self.assertIn('text-overflow: ellipsis;', (STATIC / "miniapp.css").read_text(encoding="utf-8"))
        self.assertIn('id="copy-referral"', mini)

    async def test_miniapp_javascript_has_no_missing_static_dom_ids(self):
        mini = (await mini_app_page(Request({"type": "http", "headers": []}))).body.decode()
        js = (STATIC / "miniapp.js").read_text(encoding="utf-8")
        html_ids = set(re.findall(r'id="([^"]+)"', mini))
        js_ids = set(re.findall(r'\\$\\("([^"]+)"\\)', js))
        self.assertEqual(js_ids - html_ids, set(), "Mini App JS points to missing elements")
        self.assertNotIn('"terms-link"', js)
        self.assertNotIn('"checkout-method"', js)
        self.assertIn('id="order-terms-link"', mini)

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
        self.assertIn('href="/static/theme.css"', store)
        self.assertEqual(store.count('type="submit"'), 1)
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
