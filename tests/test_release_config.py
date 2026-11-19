import unittest
from app.main import app
from app.services.plans import validate_plan
from app.services.store_profile import validate_profile


class ReleaseConfigTests(unittest.TestCase):
    def test_first_release_version(self):
        self.assertEqual(app.version, "0.0.1")

    def test_ready_plan_validation(self):
        plan = {
            "title": "Starter", "description": "", "duration_days": 30,
            "traffic_gb": 100, "device_limit": 1, "price_rub_minor": 9900,
            "price_stars": 100, "is_active": True, "sort_order": 0,
        }
        validate_plan(plan)
        with self.assertRaises(ValueError):
            validate_plan(plan | {"price_stars": 0})
        with self.assertRaises(ValueError):
            validate_plan(plan | {"duration_days": -1})

    def test_safe_branding_colors(self):
        self.assertEqual(validate_profile({"name": "My VPN", "accent_color": "#4B8BEA"})["name"], "My VPN")
        with self.assertRaises(ValueError):
            validate_profile({"accent_color": "red; background:url(https://example.com)"})
        with self.assertRaises(ValueError):
            validate_profile({"name": ""})


if __name__ == "__main__":
    unittest.main()
