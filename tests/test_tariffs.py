import unittest
from unittest.mock import patch

from app.services.tariffs import calculate_price, format_price


class TariffPricingTests(unittest.TestCase):
    def test_price_uses_each_selected_parameter(self):
        with patch("app.services.tariffs.settings") as config:
            config.tariff_price_per_day_minor = 10
            config.tariff_price_per_gb_minor = 20
            config.tariff_price_per_device_minor = 30
            self.assertEqual(calculate_price(30, 100, 3), 5300)

    def test_rejects_non_positive_values(self):
        with self.assertRaises(ValueError):
            calculate_price(0, 10, 1)

    def test_formats_minor_units(self):
        self.assertEqual(format_price(1250, "EUR"), "12.50 EUR")


if __name__ == "__main__":
    unittest.main()
