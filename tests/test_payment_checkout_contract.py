"""Contract tests: all payment providers, Stars flow, and disabled custom checkout."""
import hashlib
import hmac
import json
import unittest
from types import SimpleNamespace
from urllib.parse import quote, urlencode
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI

from app.bot import handlers
from app.services.payment_providers import CryptoPayProvider, VerifiedPayment, YooKassaProvider, YooMoneyProvider
from app.web import routes


class CheckoutContracts(unittest.IsolatedAsyncioTestCase):
    async def test_retired_checkout_authenticates_without_opening_a_database_session(self):
        app = FastAPI()
        app.include_router(routes.router)
        payload = {"days": 30, "traffic": 50, "devices": 1, "provider": "yookassa"}

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            with patch.object(routes, "SessionLocal") as session, \
                 patch.object(routes, "validate_telegram_init_data", return_value={"id": 123}) as validate:
                for suffix in ("checkout", "external/checkout"):
                    path = f"/api/v1/webapp/{suffix}"
                    with self.subTest(path=path):
                        self.assertEqual((await client.post(path, json=payload)).status_code, 401)
                        response = await client.post(path, json=payload, headers={"X-Telegram-Init-Data": "valid"})
                        self.assertEqual(response.status_code, 410)
                self.assertEqual(validate.call_count, 2)
                session.assert_not_called()

    async def test_stars_invoice_uses_correct_currency_and_payload(self):
        message=SimpleNamespace(answer_invoice=AsyncMock(),edit_text=AsyncMock(),delete=AsyncMock())
        with patch.object(handlers,"is_payment_method_enabled",AsyncMock(return_value=True)), \
             patch.object(handlers,"create_stars_payment_for_user",
                          AsyncMock(return_value=(SimpleNamespace(id=42),19))), \
             patch.object(handlers,"get_store_profile",AsyncMock(return_value={"name":"Remnaum"})):
            await handlers.send_stars_invoice(message,123,plan_id=4)
        params=message.answer_invoice.call_args.kwargs
        self.assertEqual((params["currency"],params["provider_token"],params["payload"]),
                         ("XTR","","remnaum:42"))
        self.assertEqual(params["prices"][0].amount,19)
        self.assertTrue(params["reply_markup"].inline_keyboard[0][0].pay)
        message.delete.assert_awaited_once()

    async def test_stars_invoice_disabled_or_telegram_error(self):
        message=SimpleNamespace(answer_invoice=AsyncMock(),edit_text=AsyncMock())
        with patch.object(handlers,"is_payment_method_enabled",AsyncMock(return_value=False)), \
             patch.object(handlers,"create_stars_payment_for_user",AsyncMock()) as create:
            await handlers.send_stars_invoice(message,123,plan_id=4)
        create.assert_not_awaited()
        message.answer_invoice.assert_not_awaited()
        self.assertIn("отключена",message.edit_text.call_args.args[0])
        message.edit_text.reset_mock()
        message.answer_invoice.side_effect=RuntimeError("secret-token-value")
        with patch.object(handlers,"is_payment_method_enabled",AsyncMock(return_value=True)), \
             patch.object(handlers,"create_stars_payment_for_user",
                          AsyncMock(return_value=(SimpleNamespace(id=42),19))), \
             patch.object(handlers,"get_store_profile",AsyncMock(return_value={"name":"Remnaum"})):
            with self.assertLogs("app.bot.handlers",level="ERROR"):
                await handlers.send_stars_invoice(message,123,plan_id=4)
        self.assertNotIn("secret-token-value",message.edit_text.call_args.args[0])

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
             patch.object(routes,"get_payment_presentation",return_value=[]), \
             patch.object(routes,"get_store_payment_settings",AsyncMock(return_value=(
                 {"telegram_stars":True,"yookassa":True,"yoomoney":False,"cryptopay":False},
                 {"yookassa":{"shop_id":"shop","secret_key":"sensitive-secret"}}))), \
             patch.object(routes,"get_terms_url",return_value=""), \
             patch.object(routes.settings,"public_base_url","https://hooks.example"):
            data=await routes.admin_payment_diagnostics(x_telegram_init_data="verified")
        self.assertEqual(data["verification"],"configuration_only")
        self.assertEqual(data["providers"]["yookassa"]["webhook_url"],
                         "https://hooks.example/webhooks/payments/yookassa")
        self.assertNotIn("sensitive-secret",json.dumps(data))

    async def test_bot_offers_enabled_methods_when_no_terms_url_is_configured(self):
        from app.db.models import TariffPlan
        from app.services.plans import plan_revision

        plan = TariffPlan(
            id=4, title="Безлимит", duration_days=30, traffic_gb=0,
            device_limit=5, price_rub_minor=10000, price_stars=100, is_active=True,
        )
        revision = plan_revision(plan)
        callback = SimpleNamespace(
            data=f"plan:confirm:4:0:{revision}", answer=AsyncMock(),
            message=SimpleNamespace(edit_text=AsyncMock()), from_user=SimpleNamespace(id=1),
        )
        methods = {"telegram_stars": True, "yookassa": True}
        credentials = {"yookassa": {"shop_id": "shop", "secret_key": "test-key"}}
        with patch.object(handlers, "get_plan", return_value=plan), \
             patch.object(handlers, "get_terms_url", return_value=""), \
             patch.object(handlers, "has_accepted_current_terms") as accepted, \
             patch.object(handlers, "get_payment_presentation", return_value=[
                 {"provider":"yookassa", "label":"Карты"}, {"provider":"telegram_stars", "label":"Звёзды"},
             ]), \
             patch.object(handlers, "welcome_image_path", return_value=None), \
             patch.object(handlers, "get_store_payment_settings", return_value=(methods, credentials)):
            await handlers.fixed_plan_checkout_handler(callback)
        accepted.assert_not_awaited()
        callback.answer.assert_awaited_once_with()
        keyboard = callback.message.edit_text.await_args.kwargs["reply_markup"].inline_keyboard
        self.assertEqual([row[0].callback_data for row in keyboard], [
            f"plan:pay:yookassa:4:0:{revision}", f"plan:pay:stars:4:0:{revision}", "plan:select:4:0",
        ])
        self.assertIn("Карты", keyboard[0][0].text)

    async def test_platega_callback_delivers_only_verified_settled_payments(self):
        app = FastAPI()
        app.include_router(routes.router)
        verified = VerifiedPayment(7, "transaction", 19900, "RUB")
        adapter = SimpleNamespace(verify_webhook=AsyncMock(side_effect=[None, verified, verified]))
        with (
            patch.object(routes, "_configured_provider", return_value=adapter),
            patch.object(routes, "settle_payment", side_effect=[True, False]) as settle,
            patch.object(routes, "provision_paid_payment", return_value=True) as provision,
            patch.object(routes, "notify_external_payment_success") as notify,
        ):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                path = "/webhooks/payments/platega"
                self.assertEqual((await client.post(path, json={"status": "CANCELED"})).status_code, 200)
                settle.assert_not_awaited()
                for _ in range(2):
                    self.assertEqual((await client.post(path, json={"status": "CONFIRMED"})).status_code, 200)
            self.assertEqual(settle.await_count, 2)
            provision.assert_awaited_once_with(7)
            notify.assert_awaited_once_with(7)

    async def test_terms_are_enforced_when_published(self):
        from app.services import payment_service

        with patch.object(payment_service, "get_terms_url", return_value="https://store.test/terms"), \
             patch.object(payment_service, "has_accepted_current_terms", return_value=False):
            with self.assertRaises(payment_service.TermsAcceptanceRequired):
                await payment_service.create_stars_payment_for_user(1, plan_id=4)
            self.assertFalse(await payment_service.validate_stars_pre_checkout(1, 7, 10, "XTR", "remnaum:7"))


if __name__=="__main__":
    unittest.main()
