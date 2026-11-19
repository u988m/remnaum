"""Contract tests: all payment providers, Stars flow, and disabled custom checkout."""
import hashlib
import hmac
import json
import unittest
from types import SimpleNamespace
from urllib.parse import quote, urlencode
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException

from app.bot import handlers
from app.services.payment_providers import CryptoPayProvider, YooKassaProvider, YooMoneyProvider
from app.web import routes


class CheckoutContracts(unittest.IsolatedAsyncioTestCase):
    async def test_customer_custom_plan_checkout_is_gone(self):
        with patch.object(routes, "_webapp_user", AsyncMock(return_value={"id":42})):
            calls = (
                (routes.webapp_create_checkout, routes.WebAppCheckout(days=30, traffic=40, devices=1)),
                (routes.webapp_external_checkout, routes.WebAppExternalCheckout(
                    days=30, traffic=40, devices=1, provider="yookassa")),
            )
            for fn, payload in calls:
                with self.subTest(fn=fn.__name__), self.assertRaises(HTTPException) as exc:
                    await fn(payload, x_telegram_init_data="authorized")
                self.assertEqual(exc.exception.status_code, 410)

    async def test_stars_invoice_uses_correct_currency_and_payload(self):
        message=SimpleNamespace(answer_invoice=AsyncMock(),answer=AsyncMock())
        with patch.object(handlers,"is_payment_method_enabled",AsyncMock(return_value=True)), \
             patch.object(handlers,"create_stars_payment_for_user",
                          AsyncMock(return_value=(SimpleNamespace(id=42),19))), \
             patch.object(handlers,"get_store_profile",AsyncMock(return_value={"name":"Remnaum"})):
            await handlers.send_stars_invoice(message,123,plan_id=4)
        params=message.answer_invoice.call_args.kwargs
        self.assertEqual((params["currency"],params["provider_token"],params["payload"]),
                         ("XTR","","remnaum:42"))
        self.assertEqual(params["prices"][0].amount,19)

    async def test_stars_invoice_disabled_or_telegram_error(self):
        message=SimpleNamespace(answer_invoice=AsyncMock(),answer=AsyncMock())
        with patch.object(handlers,"is_payment_method_enabled",AsyncMock(return_value=False)), \
             patch.object(handlers,"create_stars_payment_for_user",AsyncMock()) as create:
            await handlers.send_stars_invoice(message,123,plan_id=4)
        create.assert_not_awaited()
        message.answer_invoice.assert_not_awaited()
        self.assertIn("отключена",message.answer.call_args.args[0])
        message.answer.reset_mock()
        message.answer_invoice.side_effect=RuntimeError("secret-token-value")
        with patch.object(handlers,"is_payment_method_enabled",AsyncMock(return_value=True)), \
             patch.object(handlers,"create_stars_payment_for_user",
                          AsyncMock(return_value=(SimpleNamespace(id=42),19))), \
             patch.object(handlers,"get_store_profile",AsyncMock(return_value={"name":"Remnaum"})):
            with self.assertLogs("app.bot.handlers",level="ERROR"):
                await handlers.send_stars_invoice(message,123,plan_id=4)
        self.assertNotIn("secret-token-value",message.answer.call_args.args[0])

    async def test_stars_precheckout_ack_and_disabled(self):
        q=SimpleNamespace(from_user=SimpleNamespace(id=4),invoice_payload="remnaum:7",
                          total_amount=19,currency="XTR",answer=AsyncMock())
        with patch.object(handlers,"is_payment_method_enabled",AsyncMock(return_value=True)), \
             patch.object(handlers,"validate_stars_pre_checkout",AsyncMock(return_value=True)) as check:
            await handlers.stars_pre_checkout_handler(q)
        check.assert_awaited_once()
        self.assertTrue(q.answer.call_args.kwargs["ok"])
        q.answer.reset_mock()
        with patch.object(handlers,"is_payment_method_enabled",AsyncMock(return_value=False)), \
             patch.object(handlers,"validate_stars_pre_checkout",AsyncMock()) as check:
            with self.assertLogs("app.bot.handlers",level="ERROR"):
                await handlers.stars_pre_checkout_handler(q)
        check.assert_not_awaited()
        self.assertFalse(q.answer.call_args.kwargs["ok"])

    async def test_yookassa_checks_payment_at_provider(self):
        urls=[]
        def handler(request):
            urls.append(str(request.url))
            return httpx.Response(200,json={"id":"yk-10","status":"succeeded","paid":True,
                "metadata":{"payment_id":"10"},"amount":{"value":"199.00","currency":"RUB"}})
        provider=YooKassaProvider(httpx.MockTransport(handler),credentials={"shop_id":"1","secret_key":"2"})
        paid=await provider.verify_webhook(
            json.dumps({"event":"payment.succeeded","object":{"id":"yk-10"}}).encode(),{})
        self.assertEqual((paid.local_payment_id,paid.amount_minor),(10,19900))
        self.assertEqual(len(urls),1)

    async def test_yoomoney_rejects_validly_signed_but_missing_operation_id(self):
        fields={"notification_type":"p2p-incoming","operation_id":"","amount":"98.00",
                "withdraw_amount":"100.00","currency":"643","datetime":"2026-11-19T09:00:00Z",
                "sender":"","codepro":"false","label":"remnaum-10","unaccepted":"false"}
        signed="&".join(f"{quote(k,safe='~')}={quote(v,safe='~')}" for k,v in sorted(fields.items()))
        fields["sign"]=hmac.new(b"secret",signed.encode(),hashlib.sha256).hexdigest()
        with self.assertRaisesRegex(ValueError,"operation ID"):
            await YooMoneyProvider(credentials={"notification_secret":"secret"}).verify_webhook(
                urlencode(fields).encode(),{})

    async def test_cryptopay_create_fiat_invoice_and_reject_malformed_notification(self):
        calls=[]
        def handler(request):
            calls.append(json.loads(request.read()))
            return httpx.Response(200,json={"ok":True,"result":{
                "invoice_id":91,"bot_invoice_url":"https://t.me/CryptoBot?start=invoice"}})
        with patch("app.services.payment_providers.settings.cryptopay_api_base_url","https://pay.crypt.bot/api"), \
             patch("app.services.payment_providers.settings.cryptopay_accepted_assets","USDT,TON"):
            provider=CryptoPayProvider(httpx.MockTransport(handler),credentials={"api_token":"token"})
            link=await provider.create_payment(local_payment_id=8,amount_minor=19900,currency="RUB",description="VPN")
            self.assertEqual((link.payment_id,calls[0]["fiat"],calls[0]["amount"],calls[0]["payload"]),
                             ("91","RUB","199.00","8"))
            obj={"update_type":"invoice_paid","payload":{
                "invoice_id":91,"status":"paid","payload":"8","fiat":"RUB",
                "currency_type":"crypto","amount":"199.00"}}
            raw=json.dumps(obj).encode()
            signature=hmac.new(hashlib.sha256(b"token").digest(),raw,hashlib.sha256).hexdigest()
            with self.assertRaisesRegex(ValueError,"fiat invoice"):
                await provider.verify_webhook(raw,{"crypto-pay-api-signature":signature})

    async def test_admin_diagnostics_never_reveal_payment_secrets(self):
        with patch.object(routes,"_require_telegram_admin",AsyncMock(return_value={"id":1})), \
             patch.object(routes,"get_store_payment_settings",AsyncMock(return_value=(
                 {"telegram_stars":True,"yookassa":True,"yoomoney":False,"cryptopay":False},
                 {"yookassa":{"shop_id":"shop","secret_key":"sensitive-secret"}}))), \
             patch.object(routes.settings,"public_base_url","https://hooks.example"):
            data=await routes.admin_payment_diagnostics(x_telegram_init_data="verified")
        self.assertEqual(data["verification"],"configuration_only")
        self.assertEqual(data["providers"]["yookassa"]["webhook_url"],
                         "https://hooks.example/webhooks/payments/yookassa")
        self.assertNotIn("sensitive-secret",json.dumps(data))


if __name__=="__main__":
    unittest.main()
