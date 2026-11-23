import unittest

from app.services.payment_service import payment_amount_matches


class PlategaPayerCommissionTests(unittest.TestCase):
    def test_accepts_exact_and_up_to_fifteen_percent_extra(self):
        for actual in (10000, 10001, 11499, 11500):
            self.assertTrue(payment_amount_matches('platega', 10000, actual))

    def test_rejects_underpayments_and_more_than_fifteen_percent(self):
        for actual in (0, 9999, 11501, 15000):
            self.assertFalse(payment_amount_matches('platega', 10000, actual))

    def test_other_providers_require_exact_amount(self):
        for provider in ('yookassa', 'yoomoney', 'cryptopay'):
            self.assertTrue(payment_amount_matches(provider, 10000, 10000))
            self.assertFalse(payment_amount_matches(provider, 10000, 10001))
