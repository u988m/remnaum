import unittest
from app.services.referrals import referral_code, parse_referral_start

class ReferralCodeTests(unittest.TestCase):
    def test_safe_code(self):
        self.assertEqual(referral_code(123), "ref_123")
        self.assertEqual(parse_referral_start("/start ref_123"), 123)

    def test_reject_bad_codes(self):
        for text in ("/start ref_-1", "/start ref_0", "/start ref_1 extra", "/start ref_１２３", "/start no"):
            self.assertIsNone(parse_referral_start(text))
