import hashlib
import hmac
import json
import unittest
from urllib.parse import quote, urlencode
from unittest.mock import patch

import httpx

from app.services.payment_providers import CryptoPayProvider, PlategaProvider, YooKassaProvider, YooMoneyProvider


class PaymentProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_providers_never_consume_legacy_env_credentials(self):
        with patch("app.services.payment_providers.settings") as config:
            config.yookassa_shop_id = "old-shop"
            config.yookassa_secret_key = "old-secret"
            config.yoomoney_receiver = "old-wallet"
            config.yoomoney_notification_secret = "old-wallet-secret"
            config.cryptopay_api_token = "old-crypto-secret"
            self.assertFalse(YooKassaProvider().secret)
            self.assertFalse(YooMoneyProvider().secret)
            self.assertFalse(CryptoPayProvider().token)

    async def test_yoomoney_notification_signature_and_reference(self):
        with patch("app.services.payment_providers.settings") as config:
            config.yoomoney_receiver = "410000000000"
            config.yoomoney_notification_secret = "wallet-secret"
            provider = YooMoneyProvider(credentials={"receiver": "410000000000", "notification_secret": "wallet-secret"})
            fields = {
                "notification_type": "card-incoming",
                "operation_id": "operation-123",
                "amount": "98.00",
                "withdraw_amount": "100.00",
                "currency": "643",
                "datetime": "2026-11-18T12:00:00Z",
                "sender": "",
                "codepro": "false",
                "label": "remnaum-42",
                "unaccepted": "false",
            }
            signed = "&".join(
                f"{quote(key, safe='~')}={quote(value, safe='~')}"
                for key, value in sorted(fields.items())
            )
            fields["sign"] = hmac.new(b"wallet-secret", signed.encode(), hashlib.sha256).hexdigest()
            result = await provider.verify_webhook(urlencode(fields).encode(), {})
            self.assertEqual(result.local_payment_id, 42)
            self.assertEqual(result.provider_payment_id, "operation-123")
            self.assertEqual(result.amount_minor, 10000)
            self.assertEqual(result.currency, "RUB")

    async def test_cryptopay_webhook_signature(self):
        token = "test-token"
        body = json.dumps({
            "update_type": "invoice_paid",
            "payload": {
                "invoice_id": 991,
                "status": "paid",
                "payload": "7",
                "currency_type": "fiat",
                "fiat": "RUB",
                "amount": "125.50",
            },
        }, separators=(",", ":")).encode()
        secret = hashlib.sha256(token.encode()).digest()
        signature = hmac.new(secret, body, hashlib.sha256).hexdigest()
        with patch("app.services.payment_providers.settings") as config:
            config.cryptopay_api_token = token
            config.cryptopay_accepted_assets = "USDT,TON"
            provider = CryptoPayProvider(credentials={"api_token": token})
            result = await provider.verify_webhook(body, {"crypto-pay-api-signature": signature})
        self.assertEqual(result.local_payment_id, 7)
        self.assertEqual(result.provider_payment_id, "991")
        self.assertEqual(result.amount_minor, 12550)

    async def test_yookassa_create_payment_uses_idempotency_and_redirect(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["auth"] = request.headers.get("Authorization")
            seen["key"] = request.headers.get("Idempotence-Key")
            seen["body"] = json.loads(request.read())
            return httpx.Response(200, json={
                "id": "yk-1",
                "confirmation": {"confirmation_url": "https://pay.example/1"},
            })

        with patch("app.services.payment_providers.settings") as config:
            config.yookassa_shop_id = "shop"
            config.yookassa_secret_key = "secret"
            config.yookassa_currency = "RUB"
            config.public_base_url = "https://remnaum.example"
            provider = YooKassaProvider(httpx.MockTransport(handler), credentials={"shop_id": "shop", "secret_key": "secret"})
            link = await provider.create_payment(
                local_payment_id=9,
                amount_minor=19900,
                currency="RUB",
                description="Remnaum test",
            )
        self.assertEqual(link.payment_id, "yk-1")
        self.assertEqual(link.url, "https://pay.example/1")
        self.assertTrue(seen["key"])
        self.assertEqual(seen["body"]["metadata"]["payment_id"], "9")
        self.assertEqual(seen["body"]["amount"]["value"], "199.00")

    async def test_crypto_pay_uses_legacy_link_and_keeps_fiat_total(self):
        def handle(request):
            body = json.loads(request.content)
            self.assertEqual((body["currency_type"], body["fiat"], body["amount"]), ("fiat", "RUB", "100.50"))
            return httpx.Response(200, json={"ok": True, "result": {
                "invoice_id": 123, "pay_url": "https://t.me/CryptoBot?start=invoice",
            }})

        provider = CryptoPayProvider(httpx.MockTransport(handle), credentials={"api_token": "test-token"})
        invoice = await provider.create_payment(local_payment_id=2, amount_minor=10050, currency="RUB", description="VPN")
        self.assertEqual(invoice.url, "https://t.me/CryptoBot?start=invoice")

    async def test_platega_checks_confirmed_payment_details_at_provider(self):
        reference = "3fa85f64-5717-4562-b3fc-2c963f66afa6"
        headers = {"x-merchantid": "test-merchant", "x-secret": "test-secret"}
        seen = []

        def handle(request):
            self.assertEqual(request.headers["x-merchantid"], headers["x-merchantid"])
            self.assertEqual(request.headers["x-secret"], headers["x-secret"])
            seen.append(request.url.path)
            if request.method == "POST":
                order = json.loads(request.content)
                self.assertEqual(order["paymentDetails"], {"amount": 99.50, "currency": "RUB"})
                self.assertEqual(order["metadata"], {"user_email": "123@telegram.org", "userId": "123"})
                self.assertEqual(order["payload"], "remnaum-7")
                return httpx.Response(200, json={"transactionId": reference, "url": "https://pay.platega.io/?id=test"})
            return httpx.Response(200, json={
                "id": reference, "status": "CONFIRMED", "payload": "remnaum-7",
                "paymentDetails": {"amount": 99.50, "currency": "RUB"},
            })

        provider = PlategaProvider(httpx.MockTransport(handle), credentials={
            "merchant_id": headers["x-merchantid"], "secret_key": headers["x-secret"],
        })
        invoice = await provider.create_payment(
            local_payment_id=7, amount_minor=9950, currency="RUB", description="VPN", telegram_id=123,
        )
        self.assertEqual(invoice.payment_id, reference)
        event = {"status": "CONFIRMED", "id": reference, "amount": 1, "payload": "remnaum-8"}
        with self.assertRaisesRegex(ValueError, "callback credentials"):
            await provider.verify_webhook(json.dumps(event).encode(), headers | {"x-secret": "wrong"})
        self.assertIsNone(await provider.verify_webhook(json.dumps(event | {"status": "CANCELED"}).encode(), headers))
        verified = await provider.verify_webhook(json.dumps(event).encode(), headers)
        self.assertEqual((verified.local_payment_id, verified.amount_minor, verified.currency), (7, 9950, "RUB"))
        self.assertEqual(seen, ["/v2/transaction/process", f"/transaction/{reference}"])


if __name__ == "__main__":
    unittest.main()
