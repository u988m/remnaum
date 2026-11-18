from pathlib import Path
from html import escape

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.bot.runtime import bot, dispatcher
from app.config import settings
from app.db.models import Payment, Subscription, User
from app.db.session import SessionLocal
from app.services.payment_providers import get_provider
from app.services.payment_service import settle_payment
from app.services.subscriptions import provision_paid_payment
from app.services.payment_notifications import notify_external_payment_success
from app.services.telegram_webapp import validate_telegram_init_data

router = APIRouter()

@router.get("/admin/payments")
async def admin_payments_page():
    return RedirectResponse("/admin?tab=settings", status_code=307)


from pydantic import BaseModel, Field
from app.services.store_settings import get_store_payment_settings, public_payment_settings, update_store_payment_settings, is_payment_method_enabled


class PaymentSettingsUpdate(BaseModel):
    methods: dict[str, bool] = Field(default_factory=dict)
    credentials: dict[str, dict[str, str]] = Field(default_factory=dict)


async def _configured_provider(name: str):
    methods, credentials = await get_store_payment_settings()
    if not methods.get(name):
        raise HTTPException(status_code=404, detail="Payment provider is disabled")
    return get_provider(name, credentials.get(name))


@router.get("/admin/api/payment-settings")
async def admin_get_payment_settings(x_telegram_init_data: str | None = Header(default=None)):
    await _require_telegram_admin(x_telegram_init_data)
    methods, credentials = await get_store_payment_settings()
    return public_payment_settings(methods, credentials)


@router.put("/admin/api/payment-settings")
async def admin_update_payment_settings(
    data: PaymentSettingsUpdate,
    x_telegram_init_data: str | None = Header(default=None),
):
    await _require_telegram_admin(x_telegram_init_data)
    try:
        return await update_store_payment_settings(data.methods, data.credentials)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/api/v1/payment-methods")
async def public_payment_methods():
    methods, credentials = await get_store_payment_settings()
    configured = public_payment_settings(methods, credentials)["configured"]
    return {"methods": {key: enabled and configured.get(key, False) for key, enabled in methods.items()}}



from app.services.tariffs import get_store_tariff_pricing, update_store_tariff_pricing, set_tariff_draft

class TariffPricingUpdate(BaseModel):
    pricing: dict[str, int | str] = Field(default_factory=dict)


@router.get("/admin/tariffs")
async def admin_tariffs_page():
    return RedirectResponse("/admin?tab=tariffs", status_code=307)


@router.get("/admin/api/tariff-pricing")
async def admin_get_tariff_pricing(x_telegram_init_data: str | None = Header(default=None)):
    await _require_telegram_admin(x_telegram_init_data)
    return await get_store_tariff_pricing()


@router.put("/admin/api/tariff-pricing")
async def admin_update_tariff_pricing(data: TariffPricingUpdate, x_telegram_init_data: str | None = Header(default=None)):
    await _require_telegram_admin(x_telegram_init_data)
    try:
        return await update_store_tariff_pricing(data.pricing)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/telegram/webhook")
async def telegram_webhook(request: Request, x_telegram_bot_api_secret_token: str | None = Header(default=None)):
    if not settings.telegram_use_webhook:
        raise HTTPException(status_code=404, detail="Webhook mode is disabled")
    import hmac
    if not settings.telegram_webhook_secret:
        raise HTTPException(status_code=503, detail="Webhook secret is not configured")
    if not x_telegram_bot_api_secret_token or not hmac.compare_digest(x_telegram_bot_api_secret_token, settings.telegram_webhook_secret):
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
    methods, credentials = await get_store_payment_settings()
    if not methods.get("yoomoney"):
        raise HTTPException(status_code=404, detail="Payment provider is disabled")
    receiver_value = (credentials.get("yoomoney") or {}).get("receiver") or settings.yoomoney_receiver
    receiver = escape(receiver_value, quote=True)
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
        verified = await (await _configured_provider("yookassa")).verify_webhook(body, dict(request.headers))
        await settle_payment(verified, "yookassa")
        provisioned = await provision_paid_payment(verified.local_payment_id)
        if provisioned:
            await notify_external_payment_success(verified.local_payment_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Could not verify YooKassa payment") from exc
    return {"ok": True}


@router.post("/webhooks/payments/yoomoney")
async def yoomoney_webhook(request: Request):
    body = await request.body()
    try:
        verified = await (await _configured_provider("yoomoney")).verify_webhook(body, dict(request.headers))
        await settle_payment(verified, "yoomoney")
        provisioned = await provision_paid_payment(verified.local_payment_id)
        if provisioned:
            await notify_external_payment_success(verified.local_payment_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Could not process YooMoney notification") from exc
    return {"ok": True}


@router.post("/webhooks/payments/cryptopay")
async def cryptopay_webhook(request: Request):
    body = await request.body()
    try:
        verified = await (await _configured_provider("cryptopay")).verify_webhook(body, dict(request.headers))
        await settle_payment(verified, "cryptopay")
        provisioned = await provision_paid_payment(verified.local_payment_id)
        if provisioned:
            await notify_external_payment_success(verified.local_payment_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Could not process Crypto Pay update") from exc
    return {"ok": True}


from datetime import timezone

from fastapi import Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from aiogram.types import LabeledPrice

from app.db.models import Subscription, SubscriptionDetails, TermsAcceptance, User
from app.db.session import SessionLocal
from app.services.payment_service import accept_current_terms, create_stars_payment_for_user, has_accepted_current_terms
from app.services.subscriptions import get_user_subscription
from app.services.tariffs import calculate_store_stars_price, get_store_tariff_pricing, set_tariff_draft
from app.services.telegram_webapp import validate_telegram_init_data
from app.config import settings
from app.bot.runtime import bot


async def _webapp_user(init_data: str):
    try:
        identity = validate_telegram_init_data(init_data)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    telegram_id = int(identity["id"])
    async with SessionLocal() as session:
        statement = pg_insert(User).values(
            telegram_id=telegram_id,
            username=identity.get("username"),
        ).on_conflict_do_update(
            index_elements=[User.telegram_id],
            set_={"username": identity.get("username")},
        )
        await session.execute(statement)
        await session.commit()
    return identity


@router.get("/app", response_class=HTMLResponse)
async def mini_app_page():
    return HTMLResponse((Path(__file__).parent / "templates" / "miniapp.html").read_text(encoding="utf-8"))


@router.get("/api/v1/webapp/bootstrap")
async def webapp_bootstrap(x_telegram_init_data: str | None = Header(default=None)):
    if not x_telegram_init_data:
        raise HTTPException(status_code=401, detail="Telegram authentication is required")
    identity = await _webapp_user(x_telegram_init_data)
    pricing = await get_store_tariff_pricing()
    subscription = await get_user_subscription(int(identity["id"]))
    subscription_data = None
    if subscription:
        expiry = subscription.get("expires_at")
        if expiry and expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        days_left = max(0, (expiry - datetime.now(timezone.utc)).days) if expiry else None
        subscription_data = {
            "status": subscription["status"],
            "expires_at": expiry.isoformat() if expiry else None,
            "traffic_gb": subscription["traffic_gb"],
            "device_limit": subscription["device_limit"],
            "subscription_url": subscription["subscription_url"],
            "days_left": days_left,
        }
    return {
        "user": {"id": identity["id"], "first_name": identity.get("first_name", "")},
        "profile": await public_store_profile(),
        "is_admin": int(identity["id"]) in settings.admin_id_set,
        "pricing": pricing,
        "stars_enabled": await is_payment_method_enabled("telegram_stars"),
        "payment_methods": await _get_public_checkout_methods(),
        "terms_url": await get_terms_url(),
        "limits": {
            "days_min": settings.tariff_min_days, "days_max": settings.tariff_max_days,
            "traffic_min": settings.tariff_min_traffic_gb, "traffic_max": settings.tariff_max_traffic_gb,
            "devices_min": settings.tariff_min_devices, "devices_max": settings.tariff_max_devices,
            "days_default": min(max(30, settings.tariff_min_days), settings.tariff_max_days),
            "traffic_default": min(max(100, settings.tariff_min_traffic_gb), settings.tariff_max_traffic_gb),
            "devices_default": min(max(1, settings.tariff_min_devices), settings.tariff_max_devices),
        },
        "subscription": subscription_data,
    }


class WebAppTermsAcceptance(BaseModel):
    accepted: bool


class WebAppCheckout(BaseModel):
    days: int
    traffic: int
    devices: int


@router.post("/api/v1/webapp/terms/accept")
async def webapp_accept_terms(data: WebAppTermsAcceptance, x_telegram_init_data: str | None = Header(default=None)):
    if not x_telegram_init_data:
        raise HTTPException(status_code=401, detail="Telegram authentication is required")
    identity = await _webapp_user(x_telegram_init_data)
    if not data.accepted:
        raise HTTPException(status_code=400, detail="Terms acceptance is required")
    try:
        await accept_current_terms(int(identity["id"]))
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"accepted": True}


@router.post("/api/v1/webapp/checkout")
async def webapp_create_checkout(data: WebAppCheckout, x_telegram_init_data: str | None = Header(default=None)):
    if not x_telegram_init_data:
        raise HTTPException(status_code=401, detail="Telegram authentication is required")
    identity = await _webapp_user(x_telegram_init_data)
    if not await has_accepted_current_terms(int(identity["id"])):
        raise HTTPException(status_code=403, detail="Accept the current terms before checkout")
    if not await is_payment_method_enabled("telegram_stars"):
        raise HTTPException(status_code=403, detail="Telegram Stars checkout is disabled")
    if min(data.days, data.traffic, data.devices) < 1:
        raise HTTPException(status_code=422, detail="Tariff values must be positive")
    if not settings.tariff_min_days <= data.days <= settings.tariff_max_days:
        raise HTTPException(status_code=422, detail="Duration is outside configured limits")
    if not settings.tariff_min_traffic_gb <= data.traffic <= settings.tariff_max_traffic_gb:
        raise HTTPException(status_code=422, detail="Traffic is outside configured limits")
    if not settings.tariff_min_devices <= data.devices <= settings.tariff_max_devices:
        raise HTTPException(status_code=422, detail="Device count is outside configured limits")
    await set_tariff_draft(int(identity["id"]), identity.get("username"), data.days, data.traffic, data.devices)
    payment, stars = await create_stars_payment_for_user(int(identity["id"]))
    if bot is None:
        raise HTTPException(status_code=503, detail="Telegram bot is not initialized")
    try:
        invoice_url = await bot.create_invoice_link(
            title=(await get_store_profile())["name"][:50] + " VPN",
            description=f"VPN-подписка: {data.days} дней, {data.traffic} ГБ, {data.devices} устройств",
            payload=f"remnaum:{payment.id}",
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice(label="VPN-подписка", amount=stars)],
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Could not create Telegram Stars invoice") from exc
    return {"payment_id": payment.id, "stars": stars, "invoice_url": invoice_url}



async def _require_telegram_admin(init_data: str | None) -> dict:
    if not init_data:
        raise HTTPException(status_code=401, detail="Telegram authentication is required")
    try:
        identity = validate_telegram_init_data(init_data)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    telegram_id = int(identity["id"])
    if telegram_id not in settings.admin_id_set:
        raise HTTPException(status_code=403, detail="Administrator access denied")
    return identity


@router.get("/admin", response_class=HTMLResponse)
async def admin_dashboard_page():
    return HTMLResponse((Path(__file__).parent / "templates" / "admin.html").read_text(encoding="utf-8"))


@router.get("/admin/api/overview")
async def admin_overview(x_telegram_init_data: str | None = Header(default=None)):
    identity = await _require_telegram_admin(x_telegram_init_data)
    async with SessionLocal() as session:
        users = await session.scalar(select(func.count(User.id))) or 0
        active = await session.scalar(select(func.count(Subscription.id)).where(Subscription.status == "active")) or 0
        succeeded = await session.scalar(select(func.count(Payment.id)).where(Payment.status == "succeeded")) or 0
        pending = await session.scalar(select(func.count(Payment.id)).where(Payment.status == "pending")) or 0
    return {
        "admin_name": identity.get("first_name", ""),
        "users": users,
        "active_subscriptions": active,
        "successful_payments": succeeded,
        "pending_payments": pending,
    }


@router.get("/admin/api/users")
async def admin_users(
    limit: int = 20,
    offset: int = 0,
    x_telegram_init_data: str | None = Header(default=None),
):
    await _require_telegram_admin(x_telegram_init_data)
    if limit < 1 or limit > 100 or offset < 0:
        raise HTTPException(status_code=422, detail="Invalid pagination")
    latest_subscription = (
        select(Subscription.user_id, func.max(Subscription.id).label("subscription_id"))
        .group_by(Subscription.user_id)
        .subquery()
    )
    async with SessionLocal() as session:
        total = await session.scalar(select(func.count(User.id))) or 0
        result = await session.execute(
            select(User, Subscription)
            .outerjoin(latest_subscription, latest_subscription.c.user_id == User.id)
            .outerjoin(Subscription, Subscription.id == latest_subscription.c.subscription_id)
            .order_by(User.created_at.desc(), User.id.desc())
            .limit(limit)
            .offset(offset)
        )
        items = []
        for user, subscription in result.all():
            items.append({
                "telegram_id": user.telegram_id,
                "username": user.username,
                "created_at": user.created_at.isoformat(),
                "subscription_status": subscription.status if subscription else "нет подписки",
                "expires_at": subscription.expires_at.isoformat() if subscription and subscription.expires_at else None,
            })
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/admin/api/payments")
async def admin_payments(
    limit: int = 20,
    offset: int = 0,
    x_telegram_init_data: str | None = Header(default=None),
):
    await _require_telegram_admin(x_telegram_init_data)
    if limit < 1 or limit > 100 or offset < 0:
        raise HTTPException(status_code=422, detail="Invalid pagination")
    async with SessionLocal() as session:
        total = await session.scalar(select(func.count(Payment.id))) or 0
        result = await session.execute(
            select(Payment, User.telegram_id)
            .join(User, User.id == Payment.user_id)
            .order_by(Payment.created_at.desc(), Payment.id.desc())
            .limit(limit)
            .offset(offset)
        )
        items = [{
            "id": payment.id,
            "telegram_id": telegram_id,
            "provider": payment.provider,
            "amount": payment.amount,
            "currency": payment.currency,
            "status": payment.status,
            "created_at": payment.created_at.isoformat(),
        } for payment, telegram_id in result.all()]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/admin/api/users/{telegram_id}")
async def admin_user_detail(
    telegram_id: int,
    x_telegram_init_data: str | None = Header(default=None),
):
    await _require_telegram_admin(x_telegram_init_data)
    if telegram_id < 1:
        raise HTTPException(status_code=422, detail="Invalid Telegram ID")
    async with SessionLocal() as session:
        user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
        if user is None:
            raise HTTPException(status_code=404, detail="User not found")
        subscriptions = (await session.scalars(
            select(Subscription)
            .where(Subscription.user_id == user.id)
            .order_by(Subscription.created_at.desc(), Subscription.id.desc())
            .limit(50)
        )).all()
        payments = (await session.scalars(
            select(Payment)
            .where(Payment.user_id == user.id)
            .order_by(Payment.created_at.desc(), Payment.id.desc())
            .limit(50)
        )).all()
    return {
        "telegram_id": user.telegram_id,
        "username": user.username,
        "created_at": user.created_at.isoformat(),
        "subscriptions": [{
            "id": subscription.id,
            "status": subscription.status,
            "expires_at": subscription.expires_at.isoformat() if subscription.expires_at else None,
            "created_at": subscription.created_at.isoformat(),
        } for subscription in subscriptions],
        "payments": [{
            "id": payment.id,
            "provider": payment.provider,
            "amount": payment.amount,
            "currency": payment.currency,
            "status": payment.status,
            "created_at": payment.created_at.isoformat(),
        } for payment in payments],
    }


@router.post("/webhooks/remnawave")
async def remnawave_webhook(request: Request, x_remnawave_signature: str | None = Header(default=None)):
    from app.services.remnawave_webhooks import verify_remnawave_webhook
    from app.services.remnawave_events import record_and_reconcile

    body = await request.body()
    try:
        event = verify_remnawave_webhook(body, x_remnawave_signature, settings.remnawave_webhook_secret)
        is_new = await record_and_reconcile(body, event)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return {"ok": True, "event": event["event"], "duplicate": not is_new}


# Fixed plans share the existing Telegram-authenticated admin dashboard.
from app.services.plans import list_plans, save_plan, choose_plan


class PlanPayload(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=2000)
    duration_days: int = Field(ge=1, le=365)
    traffic_gb: int = Field(ge=1, le=1000000)
    device_limit: int = Field(ge=1, le=1000)
    price_rub_minor: int = Field(ge=0, le=100000000)
    price_stars: int = Field(ge=1, le=1000000)
    is_active: bool = True
    sort_order: int = Field(default=0, ge=0, le=10000)


@router.get("/api/v1/plans")
async def public_fixed_plans():
    return {"items": await list_plans(active_only=True)}


@router.get("/admin/api/plans")
async def admin_list_fixed_plans(x_telegram_init_data: str | None = Header(default=None)):
    await _require_telegram_admin(x_telegram_init_data)
    return {"items": await list_plans(active_only=False)}


@router.post("/admin/api/plans")
async def admin_create_fixed_plan(
    data: PlanPayload, x_telegram_init_data: str | None = Header(default=None)
):
    await _require_telegram_admin(x_telegram_init_data)
    try:
        return await save_plan(data.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/admin/api/plans/{plan_id}")
async def admin_update_fixed_plan(
    plan_id: int, data: PlanPayload, x_telegram_init_data: str | None = Header(default=None)
):
    await _require_telegram_admin(x_telegram_init_data)
    if plan_id < 1:
        raise HTTPException(status_code=422, detail="Invalid plan ID")
    try:
        return await save_plan(data.model_dump(), plan_id=plan_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


class WebAppFixedPlanCheckout(BaseModel):
    plan_id: int = Field(ge=1)


@router.post("/api/v1/webapp/plans/checkout")
async def webapp_checkout_ready_plan(data: WebAppFixedPlanCheckout, x_telegram_init_data: str | None = Header(default=None)):
    if not x_telegram_init_data:
        raise HTTPException(status_code=401, detail="Telegram authentication is required")
    identity = await _webapp_user(x_telegram_init_data)
    telegram_id = int(identity["id"])
    if not await has_accepted_current_terms(telegram_id):
        raise HTTPException(status_code=403, detail="Accept the current terms before checkout")
    if not await is_payment_method_enabled("telegram_stars"):
        raise HTTPException(status_code=403, detail="Telegram Stars checkout is disabled")
    try:
        quote = await choose_plan(telegram_id, data.plan_id)
        payment, stars = await create_stars_payment_for_user(telegram_id)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if bot is None:
        raise HTTPException(status_code=503, detail="Telegram bot is not initialized")
    try:
        url = await bot.create_invoice_link(
            title=(await get_store_profile())["name"][:50] + " VPN",
            description=f"VPN: {quote.duration_days} дней, {quote.traffic_gb} ГБ, {quote.device_limit} устройств",
            payload=f"remnaum:{payment.id}", provider_token="", currency="XTR",
            prices=[LabeledPrice(label="VPN-подписка", amount=stars)],
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Could not create Stars invoice") from exc
    return {"payment_id": payment.id, "stars": stars, "invoice_url": url}


from app.services.store_profile import get_store_profile, update_store_profile, get_terms_url


class StoreProfilePayload(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    tagline: str = Field(default="", max_length=300)
    description: str = Field(default="", max_length=300)
    accent_color: str = Field(default="#4b8bea", pattern=r"^#[a-fA-F0-9]{6}$")
    support_contact: str = Field(default="", max_length=300)
    terms_url: str = Field(default="", max_length=300)


from app.services.store_media import welcome_image_url, welcome_image_path, save_welcome_image, delete_welcome_image

@router.get("/api/v1/store-profile")
async def public_store_profile():
    return {**await get_store_profile(), "welcome_image_url": welcome_image_url()}


@router.get("/admin/api/store-profile")
async def admin_get_store_profile(x_telegram_init_data: str | None = Header(default=None)):
    await _require_telegram_admin(x_telegram_init_data)
    return {**await get_store_profile(), "welcome_image_url": welcome_image_url()}




class WelcomeImagePayload(BaseModel):
    data_base64: str = Field(min_length=32, max_length=7_200_000)


@router.get("/api/v1/store-profile/welcome-image")
async def public_welcome_image():
    image = welcome_image_path()
    if image is None:
        raise HTTPException(status_code=404, detail="Изображение не установлено")
    return FileResponse(
        image, media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=300", "X-Content-Type-Options": "nosniff"},
    )


@router.put("/admin/api/store-profile/welcome-image")
async def admin_upload_welcome_image(
    data: WelcomeImagePayload,
    x_telegram_init_data: str | None = Header(default=None),
):
    await _require_telegram_admin(x_telegram_init_data)
    try:
        return {"url": save_welcome_image(data.data_base64)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/admin/api/store-profile/welcome-image")
async def admin_delete_welcome_image(x_telegram_init_data: str | None = Header(default=None)):
    await _require_telegram_admin(x_telegram_init_data)
    delete_welcome_image()
    return {"url": None}


@router.get("/admin/api/bot-identity")
async def admin_bot_identity(x_telegram_init_data: str | None = Header(default=None)):
    await _require_telegram_admin(x_telegram_init_data)
    if bot is None:
        return {"username": "", "name": "", "id": None}
    try:
        identity = await bot.get_me()
    except Exception:
        return {"username": settings.telegram_bot_username.lstrip("@"), "name": "", "id": None}
    return {"username": identity.username or "", "name": identity.first_name or "", "id": identity.id}


@router.put("/admin/api/store-profile")
async def admin_put_store_profile(
    data: StoreProfilePayload, x_telegram_init_data: str | None = Header(default=None)
):
    await _require_telegram_admin(x_telegram_init_data)
    try:
        return await update_store_profile(data.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/api/v1/webapp/purchases")
async def webapp_purchases(x_telegram_init_data: str | None = Header(default=None)):
    if not x_telegram_init_data:
        raise HTTPException(status_code=401, detail="Telegram authentication is required")
    identity = await _webapp_user(x_telegram_init_data)
    async with SessionLocal() as session:
        payments = (await session.scalars(
            select(Payment).join(User, Payment.user_id == User.id)
            .where(User.telegram_id == int(identity["id"]))
            .order_by(Payment.created_at.desc(), Payment.id.desc()).limit(20)
        )).all()
        return {"items": [{
            "id": p.id, "provider": p.provider, "amount": p.amount,
            "currency": p.currency, "status": p.status, "created_at": p.created_at.isoformat(),
        } for p in payments]}


@router.get("/api/v1/webapp/devices")
async def webapp_hwid_devices(x_telegram_init_data: str | None = Header(default=None)):
    from app.services.remnawave import RemnawaveClient
    if not x_telegram_init_data:
        raise HTTPException(status_code=401, detail="Telegram authentication is required")
    identity = await _webapp_user(x_telegram_init_data)
    subscription = await get_user_subscription(int(identity["id"]))
    if not subscription or not subscription.get("panel_user_id"):
        return {"limit": None, "items": []}
    try:
        result = await RemnawaveClient().get_hwid_devices(subscription["panel_user_id"])
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Device list temporarily unavailable") from exc
    data = result.get("response", result) if isinstance(result, dict) else result
    devices = data if isinstance(data, list) else data.get("devices", data.get("items", [])) if isinstance(data, dict) else []
    return {"limit": subscription.get("device_limit"), "items": [
        {"name": str(device.get("deviceModel") or device.get("platform") or device.get("userAgent") or "Устройство")[:100]}
        for device in devices[:25] if isinstance(device, dict)
    ]}


@router.get("/api/v1/webapp/referrals")
async def webapp_referrals(x_telegram_init_data: str | None = Header(default=None)):
    from app.services.referrals import referral_code, referral_count, get_referral_settings, reward_balance
    if not x_telegram_init_data:
        raise HTTPException(status_code=401, detail="Telegram authentication is required")
    identity = await _webapp_user(x_telegram_init_data)
    telegram_id = int(identity["id"])
    from app.services.bot_identity import bot_username
    username = bot_username()
    return {
        "link": f"https://t.me/{username}?start={referral_code(telegram_id)}" if username else None,
        "count": await referral_count(telegram_id),
        "rewards_enabled": (await get_referral_settings())["enabled"],
        "reward": await reward_balance(telegram_id),
    }



from app.services.referrals import get_referral_settings, update_referral_settings


class ReferralSettingsPayload(BaseModel):
    enabled: bool
    reward_days: int = Field(ge=1, le=90)


@router.get("/admin/api/referrals")
async def admin_referrals(x_telegram_init_data: str | None = Header(default=None)):
    await _require_telegram_admin(x_telegram_init_data)
    return await get_referral_settings()


@router.put("/admin/api/referrals")
async def admin_update_referrals(
    data: ReferralSettingsPayload, x_telegram_init_data: str | None = Header(default=None)
):
    await _require_telegram_admin(x_telegram_init_data)
    try:
        return await update_referral_settings(data.enabled, data.reward_days)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# Campaigns are stored in PostgreSQL. No secret or marketing setting is
# required in the environment file.
from app.services.trials import (
    get_trial_settings, update_trial_settings, trial_eligible, claim_trial,
)
from app.services.promos import (
    create_promo, list_promos, set_promo_active, redeem_promo, promo_balance,
)


class TrialSettingsPayload(BaseModel):
    enabled: bool
    days: int = Field(ge=1, le=30)
    traffic_gb: int = Field(ge=1, le=200)
    device_limit: int = Field(ge=1, le=5)


class PromoCreatePayload(BaseModel):
    code: str = Field(min_length=4, max_length=32)
    bonus_days: int = Field(ge=1, le=90)
    max_uses: int = Field(ge=1, le=100000)


class PromoStatusPayload(BaseModel):
    active: bool


class PromoRedeemPayload(BaseModel):
    code: str = Field(min_length=4, max_length=32)


@router.get("/admin/api/trial")
async def admin_get_trial(x_telegram_init_data: str | None = Header(default=None)):
    await _require_telegram_admin(x_telegram_init_data)
    return await get_trial_settings()


@router.put("/admin/api/trial")
async def admin_put_trial(
    data: TrialSettingsPayload, x_telegram_init_data: str | None = Header(default=None)
):
    await _require_telegram_admin(x_telegram_init_data)
    try:
        return await update_trial_settings(data.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/admin/api/promos")
async def admin_get_promos(x_telegram_init_data: str | None = Header(default=None)):
    await _require_telegram_admin(x_telegram_init_data)
    return {"items": await list_promos()}


@router.post("/admin/api/promos")
async def admin_create_promo(
    data: PromoCreatePayload, x_telegram_init_data: str | None = Header(default=None)
):
    await _require_telegram_admin(x_telegram_init_data)
    try:
        return await create_promo(data.code, data.bonus_days, data.max_uses)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/admin/api/promos/{promo_id}")
async def admin_put_promo(
    promo_id: int, data: PromoStatusPayload,
    x_telegram_init_data: str | None = Header(default=None),
):
    await _require_telegram_admin(x_telegram_init_data)
    try:
        return await set_promo_active(promo_id, data.active)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/api/v1/webapp/offers")
async def webapp_offers(x_telegram_init_data: str | None = Header(default=None)):
    if not x_telegram_init_data:
        raise HTTPException(status_code=401, detail="Telegram authentication is required")
    identity = await _webapp_user(x_telegram_init_data)
    telegram_id = int(identity["id"])
    config = await get_trial_settings()
    return {
        "trial": {**config, "eligible": await trial_eligible(telegram_id)},
        "promo_balance": await promo_balance(telegram_id),
    }


@router.post("/api/v1/webapp/trial")
async def webapp_claim_trial(x_telegram_init_data: str | None = Header(default=None)):
    if not x_telegram_init_data:
        raise HTTPException(status_code=401, detail="Telegram authentication is required")
    identity = await _webapp_user(x_telegram_init_data)
    try:
        return await claim_trial(int(identity["id"]))
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Remnawave trial provisioning failed") from exc


@router.post("/api/v1/webapp/promos/redeem")
async def webapp_redeem_promo(
    data: PromoRedeemPayload,
    x_telegram_init_data: str | None = Header(default=None),
):
    if not x_telegram_init_data:
        raise HTTPException(status_code=401, detail="Telegram authentication is required")
    identity = await _webapp_user(x_telegram_init_data)
    try:
        reward = await redeem_promo(int(identity["id"]), data.code)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"reward": reward, "balance": await promo_balance(int(identity["id"]))}


# Shared checkout for the Telegram-authenticated Mini App.
# The provider controls enabled state and encrypted credentials in PostgreSQL.
from urllib.parse import urlsplit
from app.services.payment_service import create_payment_for_user
from app.services.store_settings import get_store_payment_settings, public_payment_settings


class WebAppExternalCheckout(WebAppCheckout):
    provider: str


class WebAppPlanExternalCheckout(WebAppFixedPlanCheckout):
    provider: str


def _validate_external_provider(name: str) -> None:
    if name not in ("yookassa", "yoomoney", "cryptopay"):
        raise HTTPException(status_code=422, detail="Unknown external provider")


async def _external_checkout(telegram_id: int, provider: str):
    try:
        payment, link = await create_payment_for_user(telegram_id, provider)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if urlsplit(link.url).scheme != "https":
        raise HTTPException(status_code=502, detail="Payment provider returned a non-HTTPS link")
    return {"payment_id": payment.id, "provider": provider, "url": link.url}


@router.post("/api/v1/webapp/external/checkout")
async def webapp_external_checkout(
    data: WebAppExternalCheckout, x_telegram_init_data: str | None = Header(default=None),
):
    if not x_telegram_init_data:
        raise HTTPException(status_code=401, detail="Telegram authentication is required")
    identity = await _webapp_user(x_telegram_init_data)
    _validate_external_provider(data.provider)
    telegram_id = int(identity["id"])
    if not await has_accepted_current_terms(telegram_id):
        raise HTTPException(status_code=403, detail="Accept the current terms before checkout")
    if not (settings.tariff_min_days <= data.days <= settings.tariff_max_days
            and settings.tariff_min_traffic_gb <= data.traffic <= settings.tariff_max_traffic_gb
            and settings.tariff_min_devices <= data.devices <= settings.tariff_max_devices):
        raise HTTPException(status_code=422, detail="Tariff values outside allowed range")
    await set_tariff_draft(telegram_id, identity.get("username"), data.days, data.traffic, data.devices)
    return await _external_checkout(telegram_id, data.provider)


@router.post("/api/v1/webapp/plans/external/checkout")
async def webapp_external_plan_checkout(
    data: WebAppPlanExternalCheckout, x_telegram_init_data: str | None = Header(default=None),
):
    if not x_telegram_init_data:
        raise HTTPException(status_code=401, detail="Telegram authentication is required")
    identity = await _webapp_user(x_telegram_init_data)
    _validate_external_provider(data.provider)
    telegram_id = int(identity["id"])
    if not await has_accepted_current_terms(telegram_id):
        raise HTTPException(status_code=403, detail="Accept the current terms before checkout")
    try:
        await choose_plan(telegram_id, data.plan_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await _external_checkout(telegram_id, data.provider)


async def _get_public_checkout_methods():
    methods, credentials = await get_store_payment_settings()
    configured = public_payment_settings(methods, credentials)["configured"]
    return {key: bool(methods.get(key) and configured.get(key)) for key in (
        "telegram_stars", "yookassa", "yoomoney", "cryptopay"
    )}
