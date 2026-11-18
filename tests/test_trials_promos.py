import unittest

from app.services.trials import validate_trial_settings, DEFAULT_TRIAL
from app.services.promos import normalize_code, validate_promo
from app.db.models import TrialSettings, TrialClaim, PromoCode, PromoRedemption


class TrialAndPromoTests(unittest.TestCase):
    def test_trial_opt_in_and_limits(self):
        self.assertFalse(DEFAULT_TRIAL["enabled"])
        self.assertEqual(validate_trial_settings({
            "enabled": True, "days": 7, "traffic_gb": 20, "device_limit": 2
        })["days"], 7)
        for invalid in (
            {"enabled": True, "days": 0, "traffic_gb": 10, "device_limit": 1},
            {"enabled": True, "days": 100, "traffic_gb": 10, "device_limit": 1},
            {"enabled": True, "days": 1, "traffic_gb": 0, "device_limit": 1},
            {"enabled": "true", "days": 1, "traffic_gb": 10, "device_limit": 1},
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    validate_trial_settings(invalid)

    def test_promo_code_is_normalized_without_injection(self):
        self.assertEqual(normalize_code("  welcome_2026 "), "WELCOME_2026")
        self.assertEqual(validate_promo("TRIAL7", 7, 100), "TRIAL7")
        for value in ("AB", "SOME CODE", "ТЕСТ1234", "<script>", "foo/bar", "A" * 40):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_code(value)

    def test_promo_bonus_days_are_bounded(self):
        for days, uses in ((0, 1), (91, 1), (1, 0), (1, 100001), (True, 1)):
            with self.subTest(days=days, uses=uses):
                with self.assertRaises(ValueError):
                    validate_promo("HELLO", days, uses)

    def test_new_tables_have_uniqueness_constraints(self):
        self.assertIn("trial_settings", TrialSettings.metadata.tables)
        self.assertTrue(any(c.unique for c in TrialClaim.__table__.columns if c.name == "user_id"))
        self.assertTrue(any(c.unique for c in PromoCode.__table__.columns if c.name == "code"))
        self.assertTrue(any(c.name == "uq_promo_redemption_user_code"
                            for c in PromoRedemption.__table__.constraints))


if __name__ == "__main__":
    unittest.main()
