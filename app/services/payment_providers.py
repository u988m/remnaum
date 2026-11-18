from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from decimal import Decimal
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
        self, *, local_payment_id: int, amount_minor: int, currency: str, description: str
    ) -> PaymentLink:
        raise NotImplementedError

    async def verify_webhook(self, payload: bytes, headers: dict[str, str]) -> VerifiedPayment:
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
        self, *, local_payment_id: int, amount_minor: int, currency: str, description: str
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
        self, *, local_payment_id: int, amount_minor: int, currency: str, description: str
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

        fields = dict(parse_qsl(payload.decode("utf-8"), keep_blank_values=True))
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
        if fields.get("currency") != "643" or fields.get("codepro") == "true" or fields.get("unaccepted") == "true":
            raise ValueError("YooMoney payment is not acceptable")
        return VerifiedPayment(
            local_payment_id=int(label[8:]),
            provider_payment_id=fields.get("operation_id", ""),
            amount_minor=_minor_amount(fields.get("withdraw_amount", "0")),
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
        self, *, local_payment_id: int, amount_minor: int, currency: str, description: str
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
        url = result.get("bot_invoice_url") or result.get("web_app_invoice_url")
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
        return VerifiedPayment(
            local_payment_id=int(local_id),
            provider_payment_id=str(invoice.get("invoice_id", "")),
            amount_minor=_minor_amount(invoice.get("amount", "0")),
            currency=currency,
        )


def get_provider(name: str, credentials: dict[str, str] | None = None) -> PaymentProvider:
    providers: dict[str, type[PaymentProvider]] = {
        "yookassa": YooKassaProvider,
        "yoomoney": YooMoneyProvider,
        "cryptopay": CryptoPayProvider,
    }
    try:
        return providers[name](credentials=credentials)
    except KeyError as exc:
        raise ValueError("Unsupported payment provider") from exc
