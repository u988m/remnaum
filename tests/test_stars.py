import unittest
from unittest.mock import patch

from app.services.tariffs import calculate_stars_price


class TelegramStarsPricingTests(unittest.TestCase):
    def test_stars_price_uses_tariff_dimensions(self):
        with patch("app.services.tariffs.settings") as config:
            config.tariff_stars_price_per_day = 2
            config.tariff_stars_price_per_gb = 1
            config.tariff_stars_price_per_device = 10
            self.assertEqual(calculate_stars_price(30, 50, 3), 120)

    def test_stars_price_must_be_positive(self):
        with patch("app.services.tariffs.settings") as config:
            config.tariff_stars_price_per_day = 0
            config.tariff_stars_price_per_gb = 0
            config.tariff_stars_price_per_device = 0
            with self.assertRaises(ValueError):
                calculate_stars_price(30, 50, 3)


if __name__ == "__main__":
    unittest.main()
