from html import escape

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from app.bot.runtime import bot, dispatcher
from app.config import settings
from app.db.models import Payment
from app.db.session import SessionLocal
from app.services.payment_providers import get_provider
from app.services.payment_service import settle_payment
from app.services.subscriptions import provision_paid_payment

router = APIRouter()

import secrets
from pydantic import BaseModel, Field
from app.services.store_settings import get_store_payment_settings, public_payment_settings, update_store_payment_settings


class PaymentSettingsUpdate(BaseModel):
    methods: dict[str, bool] = Field(default_factory=dict)
    credentials: dict[str, dict[str, str]] = Field(default_factory=dict)


def _require_admin_token(token: str | None) -> None:
    if not settings.admin_api_token:
        raise HTTPException(status_code=503, detail="ADMIN_API_TOKEN is not configured")
    if not token or not secrets.compare_digest(token, settings.admin_api_token):
        raise HTTPException(status_code=403, detail="Administrator authentication required")


@router.get("/admin/api/payment-settings")
async def admin_get_payment_settings(x_admin_token: str | None = Header(default=None)):
    _require_admin_token(x_admin_token)
    methods, credentials = await get_store_payment_settings()
    return public_payment_settings(methods, credentials)


@router.put("/admin/api/payment-settings")
async def admin_update_payment_settings(
    data: PaymentSettingsUpdate,
    x_admin_token: str | None = Header(default=None),
):
    _require_admin_token(x_admin_token)
    try:
        return await update_store_payment_settings(data.methods, data.credentials)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/api/v1/payment-methods")
async def public_payment_methods():
    methods, credentials = await get_store_payment_settings()
    configured = public_payment_settings(methods, credentials)["configured"]
    return {"methods": {key: enabled and configured.get(key, False) for key, enabled in methods.items()}}



@router.post("/telegram/webhook")
async def telegram_webhook(request: Request, x_telegram_bot_api_secret_token: str | None = Header(default=None)):
    if not settings.telegram_use_webhook:
        raise HTTPException(status_code=404, detail="Webhook mode is disabled")
    if settings.telegram_webhook_secret and x_telegram_bot_api_secret_token != settings.telegram_webhook_secret:
        raise HTTPException(status_code=403, detail="Invalid webhook secret")
    if bot is None:
        raise HTTPException(status_code=503, detail="Bot token is not configured")
    from aiogram.types import Update
    update = Update.model_validate(await request.json(), context={"bot": bot})
    await dispatcher.feed_update(bot, update)
    return {"ok": True}


@router.get("/api/v1/config")
async def public_config():
    return {"app": settings.app_name, "webapp_url": settings.webapp_url}


@router.get("/payments/return", response_class=HTMLResponse)
async def payment_return():
    return HTMLResponse(
        "<!doctype html><html lang='ru'><meta charset='utf-8'><title>Remnaum</title>"
        "<body><h2>Вернитесь в Telegram</h2><p>Статус оплаты обновится автоматически после подтверждения платёжной системой.</p></body></html>"
    )


@router.get("/payments/yoomoney/{payment_id}", response_class=HTMLResponse)
async def yoomoney_payment_form(payment_id: int):
    async with SessionLocal() as session:
        payment = await session.scalar(
            select(Payment).where(Payment.id == payment_id, Payment.provider == "yoomoney")
        )
        if payment is None or payment.status != "pending":
            raise HTTPException(status_code=404, detail="Payment not found or no longer pending")
        amount = f"{payment.amount / 100:.2f}"
    receiver = escape(settings.yoomoney_receiver, quote=True)
    label = escape(f"remnaum-{payment_id}", quote=True)
    html = f"""<!doctype html>
<html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Оплата Remnaum</title><body>
<h2>Оплата Remnaum</h2><p>Сумма: <b>{amount} RUB</b></p>
<form method="post" action="https://yoomoney.ru/quickpay/confirm">
<input type="hidden" name="receiver" value="{receiver}">
<input type="hidden" name="label" value="{label}">
<input type="hidden" name="quickpay-form" value="button">
<input type="hidden" name="sum" value="{amount}">
<button type="submit" name="paymentType" value="PC">Оплатить кошельком ЮMoney</button>
<button type="submit" name="paymentType" value="AC">Оплатить банковской картой</button>
</form></body></html>"""
    return HTMLResponse(html)


@router.post("/webhooks/payments/yookassa")
async def yookassa_webhook(request: Request):
    body = await request.body()
    try:
        verified = await get_provider("yookassa").verify_webhook(body, dict(request.headers))
        await settle_payment(verified, "yookassa")
        await provision_paid_payment(verified.local_payment_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Could not verify YooKassa payment") from exc
    return {"ok": True}


@router.post("/webhooks/payments/yoomoney")
async def yoomoney_webhook(request: Request):
    body = await request.body()
    try:
        verified = await get_provider("yoomoney").verify_webhook(body, dict(request.headers))
        await settle_payment(verified, "yoomoney")
        await provision_paid_payment(verified.local_payment_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Could not process YooMoney notification") from exc
    return {"ok": True}


@router.post("/webhooks/payments/cryptopay")
async def cryptopay_webhook(request: Request):
    body = await request.body()
    try:
        verified = await get_provider("cryptopay").verify_webhook(body, dict(request.headers))
        await settle_payment(verified, "cryptopay")
        await provision_paid_payment(verified.local_payment_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Could not process Crypto Pay update") from exc
    return {"ok": True}
