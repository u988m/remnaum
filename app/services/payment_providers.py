from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote

import httpx

from app.config import settings


@dataclass(frozen=True)
class PaymentLink:
    provider: str
    payment_id: str
    url: str


@dataclass(frozen=True)
class VerifiedPayment:
    local_payment_id: int
    provider_payment_id: str
    amount_minor: int
    currency: str


def _major_amount(amount_minor: int) -> str:
    return f"{Decimal(amount_minor) / Decimal(100):.2f}"


def _minor_amount(value: str | int | float) -> int:
    return int((Decimal(str(value)) * 100).quantize(Decimal("1")))


class PaymentProvider:
    name = "base"

    async def create_payment(
        self, *, local_payment_id: int, amount_minor: int, currency: str, description: str,
        telegram_id: int | None = None,
    ) -> PaymentLink:
        raise NotImplementedError

    async def verify_webhook(self, payload: bytes, headers: dict[str, str]) -> VerifiedPayment | None:
        raise NotImplementedError


class YooKassaProvider(PaymentProvider):
    name = "yookassa"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None, credentials: dict[str, str] | None = None):
        credentials = credentials or {}
        self.shop_id = credentials.get("shop_id", "")
        self.secret = credentials.get("secret_key", "")
        self.currency = settings.yookassa_currency
        self.transport = transport

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        if not self.shop_id or not self.secret:
            raise RuntimeError("YooKassa credentials are not configured")
        async with httpx.AsyncClient(
            base_url="https://api.yookassa.ru/v3/",
            auth=(self.shop_id, self.secret),
            timeout=20,
            transport=self.transport,
        ) as client:
            response = await client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()

    async def create_payment(
        self, *, local_payment_id: int, amount_minor: int, currency: str, description: str,
        telegram_id: int | None = None,
    ) -> PaymentLink:
        if currency.upper() != self.currency.upper():
            raise ValueError(f"YooKassa is configured for {self.currency}, not {currency}")
        result = await self._request(
            "POST",
            "payments",
            headers={"Idempotence-Key": str(uuid.uuid5(uuid.NAMESPACE_URL, f"remnaum-payment-{local_payment_id}"))},
            json={
                "amount": {"value": _major_amount(amount_minor), "currency": currency.upper()},
                "capture": True,
                "confirmation": {
                    "type": "redirect",
                    "return_url": f"{settings.public_base_url.rstrip('/')}/payments/return",
                },
                "description": description[:128],
                "metadata": {"payment_id": str(local_payment_id)},
            },
        )
        confirmation_url = (result.get("confirmation") or {}).get("confirmation_url")
        if not result.get("id") or not confirmation_url:
            raise RuntimeError("YooKassa returned an incomplete payment object")
        return PaymentLink(self.name, str(result["id"]), confirmation_url)

    async def verify_webhook(self, payload: bytes, headers: dict[str, str]) -> VerifiedPayment:
        event = json.loads(payload)
        if event.get("event") != "payment.succeeded":
            raise ValueError("Unsupported YooKassa event")
        object_data = event.get("object") or {}
        remote_id = object_data.get("id")
        if not remote_id:
            raise ValueError("Missing YooKassa payment ID")
        verified = await self._request("GET", f"payments/{remote_id}")
        if verified.get("status") != "succeeded" or verified.get("paid") is not True:
            raise ValueError("YooKassa payment is not paid")
        metadata = verified.get("metadata") or {}
        amount = verified.get("amount") or {}
        local_id = metadata.get("payment_id")
        if not str(local_id or "").isdigit():
            raise ValueError("Missing local payment reference")
        return VerifiedPayment(
            local_payment_id=int(local_id),
            provider_payment_id=str(remote_id),
            amount_minor=_minor_amount(amount.get("value", "0")),
            currency=str(amount.get("currency", "")).upper(),
        )


class YooMoneyProvider(PaymentProvider):
    name = "yoomoney"

    def __init__(self, credentials: dict[str, str] | None = None):
        credentials = credentials or {}
        self.receiver = credentials.get("receiver", "")
        self.secret = credentials.get("notification_secret", "")

    async def create_payment(
        self, *, local_payment_id: int, amount_minor: int, currency: str, description: str,
        telegram_id: int | None = None,
    ) -> PaymentLink:
        if currency.upper() != "RUB":
            raise ValueError("YooMoney wallet payments support RUB only")
        if not self.receiver or not self.secret:
            raise RuntimeError("YooMoney wallet credentials are not configured")
        return PaymentLink(
            self.name,
            f"ym-{local_payment_id}",
            f"{settings.public_base_url.rstrip('/')}/payments/yoomoney/{local_payment_id}",
        )

    async def verify_webhook(self, payload: bytes, headers: dict[str, str]) -> VerifiedPayment:
        from urllib.parse import parse_qsl

        pairs = parse_qsl(payload.decode("utf-8"), keep_blank_values=True, strict_parsing=True, max_num_fields=50)
        fields = dict(pairs)
        if len(fields) != len(pairs):
            raise ValueError("Duplicate YooMoney notification fields")
        signature = fields.pop("sign", "")
        if not signature or not self.secret:
            raise ValueError("Missing YooMoney signature")
        signed = "&".join(
            f"{quote(key, safe='~')}={quote(value, safe='~')}"
            for key, value in sorted(fields.items())
        )
        expected = hmac.new(self.secret.encode(), signed.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise ValueError("Invalid YooMoney signature")
        label = fields.get("label", "")
        if not label.startswith("remnaum-") or not label[8:].isdigit():
            raise ValueError("Invalid YooMoney payment label")
        if (fields.get("notification_type") not in {"p2p-incoming", "card-incoming"}
                or fields.get("currency") != "643" or fields.get("codepro") != "false"
                or fields.get("unaccepted", "false") != "false"):
            raise ValueError("YooMoney payment is not acceptable")
        operation_id = fields.get("operation_id", "")
        if not operation_id:
            raise ValueError("Missing YooMoney operation ID")
        return VerifiedPayment(
            local_payment_id=int(label[8:]),
            provider_payment_id=operation_id,
            amount_minor=_verified_minor_amount(fields.get("withdraw_amount")),
            currency="RUB",
        )


class CryptoPayProvider(PaymentProvider):
    name = "cryptopay"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None, credentials: dict[str, str] | None = None):
        credentials = credentials or {}
        self.token = credentials.get("api_token", "")
        self.assets = [item.strip().upper() for item in settings.cryptopay_accepted_assets.split(",") if item.strip()]
        self.transport = transport

    async def _request(self, method: str, data: dict[str, Any]) -> dict[str, Any]:
        if not self.token:
            raise RuntimeError("Crypto Pay API token is not configured")
        async with httpx.AsyncClient(
            base_url=settings.cryptopay_api_base_url.rstrip("/") + "/",
            headers={"Crypto-Pay-API-Token": self.token},
            timeout=20,
            transport=self.transport,
        ) as client:
            response = await client.post(method, json=data)
            response.raise_for_status()
            result = response.json()
        if not result.get("ok"):
            raise RuntimeError(f"Crypto Pay API error: {result.get('error', 'unknown')}")
        return result.get("result") or {}

    async def create_payment(
        self, *, local_payment_id: int, amount_minor: int, currency: str, description: str,
        telegram_id: int | None = None,
    ) -> PaymentLink:
        result = await self._request(
            "createInvoice",
            {
                "currency_type": "fiat",
                "fiat": currency.upper(),
                "amount": _major_amount(amount_minor),
                "accepted_assets": ",".join(self.assets) if self.assets else "USDT,TON,BTC,ETH,TRX,USDC",
                "description": description[:1024],
                "payload": str(local_payment_id),
                "allow_comments": False,
                "allow_anonymous": False,
                "expires_in": 3600,
            },
        )
        url = (result.get("web_app_invoice_url") or result.get("bot_invoice_url")
               or result.get("mini_app_invoice_url") or result.get("pay_url"))
        if not result.get("invoice_id") or not url:
            raise RuntimeError("Crypto Pay returned an incomplete invoice")
        return PaymentLink(self.name, str(result["invoice_id"]), url)

    async def verify_webhook(self, payload: bytes, headers: dict[str, str]) -> VerifiedPayment:
        if not self.token:
            raise RuntimeError("Crypto Pay API token is not configured")
        signature = headers.get("crypto-pay-api-signature", "")
        secret = hashlib.sha256(self.token.encode()).digest()
        expected = hmac.new(secret, payload, hashlib.sha256).hexdigest()
        if not signature or not hmac.compare_digest(expected, signature):
            raise ValueError("Invalid Crypto Pay webhook signature")
        update = json.loads(payload)
        if update.get("update_type") != "invoice_paid":
            raise ValueError("Unsupported Crypto Pay update")
        invoice = update.get("payload") or {}
        local_id = invoice.get("payload")
        if invoice.get("status") != "paid" or not str(local_id or "").isdigit():
            raise ValueError("Crypto Pay invoice is not paid or has no local reference")
        currency = str(invoice.get("fiat") or "").upper()
        if (invoice.get("currency_type") not in (None, "fiat") or not currency
                or not str(invoice.get("invoice_id") or "").isascii()
                or not str(invoice.get("invoice_id") or "").isdecimal()):
            raise ValueError("Invalid Crypto Pay fiat invoice")
        return VerifiedPayment(
            local_payment_id=int(local_id),
            provider_payment_id=str(invoice.get("invoice_id", "")),
            amount_minor=_verified_minor_amount(invoice.get("amount")),
            currency=currency,
        )


def _verified_minor_amount(value) -> int:
    try:
        minor = Decimal(str(value)) * 100
    except InvalidOperation as exc:
        raise ValueError("Invalid payment amount") from exc
    if not minor.is_finite() or not 0 < minor <= 2_147_483_647 or minor != minor.to_integral_value():
        raise ValueError("Invalid payment amount")
    return int(minor)


class PlategaProvider(PaymentProvider):
    name = "platega"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None, credentials: dict[str, str] | None = None):
        credentials = credentials or {}
        self.merchant_id = credentials.get("merchant_id", "")
        self.secret = credentials.get("secret_key", "")
        self.transport = transport

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        if not self.merchant_id or not self.secret:
            raise RuntimeError("Platega credentials are not configured")
        async with httpx.AsyncClient(
            base_url="https://app.platega.io/",
            headers={"X-MerchantId": self.merchant_id, "X-Secret": self.secret},
            timeout=20,
            transport=self.transport,
        ) as client:
            response = await client.request(method, path, **kwargs)
            response.raise_for_status()
        return response.json()

    async def create_payment(
        self, *, local_payment_id: int, amount_minor: int, currency: str, description: str,
        telegram_id: int | None = None,
    ) -> PaymentLink:
        if not telegram_id:
            raise ValueError("Platega requires a customer ID")
        return_url = f"{settings.public_base_url.rstrip('/')}/payments/return"
        order = f"remnaum-{local_payment_id}"
        result = await self._request("POST", "v2/transaction/process", json={
            "paymentDetails": {"amount": amount_minor / 100, "currency": currency.upper()},
            "description": description,
            "return": return_url,
            "failedUrl": return_url + "?failed=1",
            "payload": order,
            "orderId": order,
            "metadata": {"user_email": f"{telegram_id}@telegram.org", "userId": str(telegram_id)},
        })
        transaction_id = str(uuid.UUID(str(result.get("transactionId") or "")))
        if not result.get("url"):
            raise RuntimeError("Platega returned an incomplete payment link")
        return PaymentLink(self.name, transaction_id, result["url"])

    async def verify_webhook(self, payload: bytes, headers: dict[str, str]) -> VerifiedPayment | None:
        if (not self.merchant_id or not self.secret
                or not hmac.compare_digest(headers.get("x-merchantid", "").encode(), self.merchant_id.encode())
                or not hmac.compare_digest(headers.get("x-secret", "").encode(), self.secret.encode())):
            raise ValueError("Invalid Platega callback credentials")
        update = json.loads(payload)
        if not isinstance(update, dict):
            raise ValueError("Invalid Platega callback")
        if update.get("status") != "CONFIRMED":
            return None
        transaction_id = str(uuid.UUID(str(update.get("id") or "")))
        payment = await self._request("GET", f"transaction/{transaction_id}")
        if payment.get("status") != "CONFIRMED":
            return None
        order = str(payment.get("payload") or "")
        local_id = order.removeprefix("remnaum-")
        if (str(payment.get("id")) != transaction_id or not order.startswith("remnaum-")
                or not local_id.isascii() or not local_id.isdecimal()):
            raise ValueError("Invalid Platega payment reference")
        details = payment.get("paymentDetails") or {}
        return VerifiedPayment(int(local_id), transaction_id, _verified_minor_amount(details.get("amount")),
                               str(details.get("currency") or "").upper())


def get_provider(name: str, credentials: dict[str, str] | None = None) -> PaymentProvider:
    providers: dict[str, type[PaymentProvider]] = {
        "yookassa": YooKassaProvider,
        "yoomoney": YooMoneyProvider,
        "cryptopay": CryptoPayProvider,
        "platega": PlategaProvider,
    }
    try:
        return providers[name](credentials=credentials)
    except KeyError as exc:
        raise ValueError("Unsupported payment provider") from exc
