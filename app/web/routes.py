import asyncio
from io import BytesIO
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import blake2s
import json
import logging

import httpx
from math import ceil
from pathlib import Path
from html import escape

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse, Response
from sqlalchemy import Date, cast, func, or_, select
from aiogram.exceptions import TelegramAPIError
from redis.exceptions import RedisError

from app.bot.buttons import BUTTONS, ButtonStyle
from app.bot.runtime import bot, dispatcher
from app.config import settings
from app.version import RELEASE_REPOSITORY, installed_release_version, latest_release_version
from app.db.models import Payment, Subscription, TariffPlan, User
from app.db.session import SessionLocal
from app.services.payment_providers import PAYMENT_WINDOW, get_provider
from app.services.release_notifications import enqueue_release, parse_release, verify_signature
from app.services.remnawave import RemnawaveClient, panel_client
from app.services.payment_service import PaymentProviderUnavailable, TermsAcceptanceRequired, settle_payment
from app.services.tariffs import format_traffic
from app.services.subscriptions import provision_paid_payment
from app.services.payment_notifications import notify_external_payment_success
from app.services.telegram_webapp import telegram_photo_url, validate_telegram_init_data
from app.services.store_documents import DOCUMENT_MAX_LENGTH, DocumentKind, list_documents, save_document
from app.services.notification_settings import (
    NotificationPreferences, NotificationSettings, get_notification_preferences, get_notification_settings,
    save_notification_preferences, save_notification_settings,
)
from app.services import customer_admin
from app.services.devices import DeviceNotFound, DevicesUnavailable, change_customer_device, get_customer_devices
from app.services.subscriptions import PanelAccountConflict
from app.services.users import BLOCKED_MESSAGE, BlockedCustomer, customer_is_blocked, upsert_active_customer

from app.bot.membership import membership_required
from app.services.store_operations import get_operations
from app.web.browser_auth import (
    app_origin, browser_identity, is_browser_host, on_app_host, on_miniapp_host, requested_login, web_credentials,
)

logger = logging.getLogger(__name__)
router = APIRouter()

@router.get("/admin/payments")
async def admin_payments_page():
    return RedirectResponse("/admin?tab=settings", status_code=307)


from pydantic import AliasChoices, BaseModel, Field, model_validator
from app.services.store_settings import (
    DEFAULT_METHODS, PROVIDER_LABELS, get_store_payment_settings, is_payment_method_enabled,
    get_payment_presentation, public_payment_settings, rubles_first, update_store_payment_settings,
)


class PaymentPresentationEntry(BaseModel):
    provider: str = Field(max_length=24)
    label: str = Field(max_length=24)


class PaymentSettingsUpdate(BaseModel):
    methods: dict[str, bool] = Field(default_factory=dict)
    credentials: dict[str, dict[str, str]] = Field(default_factory=dict)
    presentation: list[PaymentPresentationEntry] | None = Field(default=None, max_length=len(DEFAULT_METHODS))


async def _configured_provider(name: str):
    methods, credentials = await get_store_payment_settings()
    if not public_payment_settings(methods, credentials)["configured"].get(name):
        raise RuntimeError("Payment provider credentials are unavailable")
    return get_provider(name, credentials.get(name))


@router.get("/admin/api/payment-settings")
async def admin_get_payment_settings(x_telegram_init_data: str | dict | None = Depends(web_credentials)):
    await _require_telegram_admin(x_telegram_init_data)
    methods, credentials = await get_store_payment_settings()
    return public_payment_settings(methods, credentials) | {"presentation": await get_payment_presentation()}


@router.get("/admin/api/payment-diagnostics")
async def admin_payment_diagnostics(x_telegram_init_data: str | dict | None = Depends(web_credentials)):
    """Configuration-only readiness; no token, password or network probe is exposed."""
    await _require_telegram_admin(x_telegram_init_data)
    methods, credentials = await get_store_payment_settings()
    configured = public_payment_settings(methods, credentials)["configured"]
    base = settings.public_base_url.rstrip("/")
    return {
        "terms_url": await get_terms_url(),
        "presentation": await get_payment_presentation(),
        "providers": {
            name: {
                "enabled": bool(methods.get(name)),
                "configured": bool(configured.get(name) and (bot is not None if name == "telegram_stars" else True)),
                "webhook_url": f"{base}/webhooks/payments/{name}" if name in PROVIDER_LABELS and base.startswith("https://") else None,
            } for name in DEFAULT_METHODS
        },
        "verification": "configuration_only",
    }


@router.put("/admin/api/payment-settings")
async def admin_update_payment_settings(
    data: PaymentSettingsUpdate,
    x_telegram_init_data: str | dict | None = Depends(web_credentials),
):
    await _require_telegram_admin(x_telegram_init_data)
    try:
        if data.presentation is not None:
            return await update_store_payment_settings(
                data.methods, data.credentials, presentation=[item.model_dump() for item in data.presentation],
            )
        return await update_store_payment_settings(data.methods, data.credentials)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/api/v1/payment-methods")
async def public_payment_methods():
    methods = await _get_public_checkout_methods()
    return {
        "methods": methods, "terms_url": await get_terms_url(),
        "presentation": [item for item in rubles_first(await get_payment_presentation()) if methods.get(item["provider"])],
    }



from app.services.tariffs import get_store_tariff_pricing, update_store_tariff_pricing

class TariffPricingUpdate(BaseModel):
    pricing: dict[str, int | str] = Field(default_factory=dict)


@router.get("/admin/tariffs")
async def admin_tariffs_page():
    return RedirectResponse("/admin?tab=tariffs", status_code=307)


@router.get("/admin/api/tariff-pricing")
async def admin_get_tariff_pricing(x_telegram_init_data: str | dict | None = Depends(web_credentials)):
    await _require_telegram_admin(x_telegram_init_data)
    return await get_store_tariff_pricing()


@router.put("/admin/api/tariff-pricing")
async def admin_update_tariff_pricing(data: TariffPricingUpdate, x_telegram_init_data: str | dict | None = Depends(web_credentials)):
    await _require_telegram_admin(x_telegram_init_data)
    try:
        return await update_store_tariff_pricing(data.pricing)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/webhooks/github/releases")
async def github_release_webhook(request: Request):
    secret = settings.github_release_webhook_secret
    if not secret or bot is None or not settings.admin_id_set:
        raise HTTPException(status_code=404, detail="GitHub release webhook unavailable")
    body = await request.body()
    if not verify_signature(body, request.headers.get("X-Hub-Signature-256", ""), secret):
        raise HTTPException(status_code=403, detail="Invalid GitHub signature")
    if request.headers.get("X-GitHub-Event") != "release":
        return {"status": "ignored"}
    try:
        event = json.loads(body)
    except (UnicodeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid GitHub payload") from exc
    if not isinstance(event, dict) or event.get("action") != "published":
        return {"status": "ignored"}
    repository = event.get("repository")
    if not isinstance(repository, dict) or repository.get("full_name") != RELEASE_REPOSITORY:
        return {"status": "ignored"}
    release = parse_release(event.get("release"))
    if release is None:
        return {"status": "ignored"}
    try:
        queued = await enqueue_release(bot.id, release)
    except RedisError as exc:
        logger.warning("Could not queue GitHub release %s", release.tag, exc_info=True)
        raise HTTPException(status_code=503, detail="Could not queue GitHub release") from exc
    return {"status": "queued" if queued else "ignored"}


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
    return {"app": settings.app_name, "webapp_url": settings.webapp_root_url, "browser_webapp_url": app_origin()}


@router.get("/payments/return", response_class=HTMLResponse)
async def payment_return(failed: bool = False):
    heading = "Оплата не завершена" if failed else "Вернитесь в магазин"
    message = ("Вы можете вернуться к заказу и выбрать способ оплаты." if failed
               else "Статус заказа обновится после подтверждения оплаты.")
    return HTMLResponse(
        "<!doctype html><html lang='ru' data-theme='monochrome'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'><title>Remnaum</title>"
        "<link rel='stylesheet' href='/static/theme.css'><link rel='stylesheet' href='/store-theme.css'><link rel='stylesheet' href='/static/store.css'></head>"
        f"<body><main><section class='store-panel'><h2>{heading}</h2><p>{message}</p></section></main></body></html>"
    )


@router.get("/payments/yoomoney/{payment_id}", response_class=HTMLResponse)
async def yoomoney_payment_form(payment_id: int):
    async with SessionLocal() as session:
        payment = await session.scalar(
            select(Payment).where(Payment.id == payment_id, Payment.provider == "yoomoney")
        )
        if payment is not None and (
            (payment.status == "cancelled" and payment.expired_at is not None)
            or (payment.status == "pending" and datetime.now(timezone.utc) >= payment.created_at + PAYMENT_WINDOW)
        ):
            raise HTTPException(status_code=410, detail="Срок оплаты истёк. Создайте новый счёт в разделе «Тарифы».")
        if payment is None or payment.status != "pending":
            raise HTTPException(status_code=404, detail="Payment not found or no longer pending")
        amount = f"{payment.amount / 100:.2f}"
    methods, credentials = await get_store_payment_settings()
    if not public_payment_settings(methods, credentials)["configured"]["yoomoney"]:
        raise HTTPException(status_code=503, detail="Реквизиты ЮMoney недоступны")
    receiver_value = credentials["yoomoney"]["receiver"]
    receiver = escape(receiver_value, quote=True)
    label = escape(f"remnaum-{payment_id}", quote=True)
    return_url = escape(f"{settings.public_base_url.rstrip('/')}/payments/return", quote=True)
    html = f"""<!doctype html>
<html lang="ru" data-theme="monochrome"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Оплата через ЮMoney</title><link rel="stylesheet" href="/static/theme.css"><link rel="stylesheet" href="/store-theme.css"><link rel="stylesheet" href="/static/store.css"></head>
<body><main><section class="store-panel">
<h2>Оплата через ЮMoney</h2><p class="price">{amount} ₽</p>
<form class="store-pay-methods" method="post" action="https://yoomoney.ru/quickpay/confirm">
<input type="hidden" name="receiver" value="{receiver}">
<input type="hidden" name="label" value="{label}">
<input type="hidden" name="quickpay-form" value="button">
<input type="hidden" name="sum" value="{amount}">
<input type="hidden" name="successURL" value="{return_url}">
<button class="secondary-btn" type="submit" name="paymentType" value="PC">Кошелёк ЮMoney</button>
<button class="secondary-btn" type="submit" name="paymentType" value="AC">Банковская карта</button>
</form></section></main></body></html>"""
    return HTMLResponse(html)


async def finish_webhook_payment(payment_id: int) -> None:
    try:
        if await provision_paid_payment(payment_id):
            await notify_external_payment_success(payment_id)
    except Exception:
        logger.exception("Could not finish verified payment %s; recovery will retry", payment_id)


@router.post("/webhooks/payments/{provider}")
async def payment_webhook(provider: str, request: Request, background_tasks: BackgroundTasks):
    if provider not in PROVIDER_LABELS:
        raise HTTPException(status_code=404, detail="Unknown payment provider")
    try:
        adapter = await _configured_provider(provider)
        verified = await adapter.verify_webhook(await request.body(), dict(request.headers))
        if verified is not None and await settle_payment(verified, provider):
            background_tasks.add_task(finish_webhook_payment, verified.local_payment_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Could not process payment notification") from exc
    return {"ok": True}


from fastapi import Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import select
from aiogram.types import LabeledPrice

from app.db.models import Subscription, SubscriptionDetails, TermsAcceptance, User
from app.db.session import SessionLocal
from app.services.payment_service import accept_current_terms, create_stars_payment_for_user
from app.services.subscriptions import get_user_subscription
from app.services.telegram_webapp import validate_telegram_init_data
from app.config import settings
from app.bot.runtime import bot


def _require_telegram_identity(init_data: str | dict | None) -> dict:
    if isinstance(init_data, dict):
        return init_data
    if not init_data:
        raise HTTPException(status_code=401, detail="Telegram authentication is required")
    try:
        return validate_telegram_init_data(init_data)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


async def _check_webapp_membership(telegram_id: int):
    policy = await get_operations()
    if await membership_required(bot, telegram_id, policy):
        raise HTTPException(403, {"message": "Подпишитесь на канал магазина и повторите проверку.",
                                  "channel_url": policy["channel_url"]})


async def _webapp_user(init_data: str | dict):
    identity = _require_telegram_identity(init_data)
    telegram_id = int(identity["id"])
    await _check_webapp_membership(telegram_id)
    if identity.get("browser_provider"):
        return identity
    try:
        async with SessionLocal.begin() as session:
            await upsert_active_customer(session, telegram_id, identity.get("username"),
                                         photo_url=telegram_photo_url(identity.get("photo_url")), sync_photo=True)
    except BlockedCustomer as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return identity


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def mini_app_root(request: Request):
    if not (on_app_host(request) or on_miniapp_host(request)):
        raise HTTPException(404)
    return await mini_app_page(request)


@router.get("/app", response_class=HTMLResponse)
async def mini_app_page(request: Request):
    browser = is_browser_host(request)
    if browser and not await browser_identity(request):
        return RedirectResponse(requested_login(request), headers={"Cache-Control": "no-store"})
    web_dir = Path(__file__).parent
    html = (web_dir / "templates" / "miniapp.html").read_text(encoding="utf-8")
    for asset in ("miniapp.css", "miniapp.js"):
        revision = blake2s((web_dir / "static" / asset).read_bytes(), digest_size=8).hexdigest()
        html = html.replace(f'/static/{asset}"', f'/static/{asset}?v={revision}"')
    html = html.replace("__CUSTOMER_SURFACE__", "browser" if browser else "miniapp")
    if browser:
        html = html.replace(',maximum-scale=1,user-scalable=no', '')
        html = html.replace('<script src="https://telegram.org/js/telegram-web-app.js"></script>', '')
        html = html.replace("</head>", '<link rel="stylesheet" href="/static/browser.css">\n</head>')
    return HTMLResponse(html, headers={"Cache-Control": "no-store" if browser else "no-cache"})


async def _webapp_subscription_data(telegram_id: int) -> dict | None:
    subscription = await get_user_subscription(telegram_id)
    if not subscription:
        return None
    expiry = subscription.get("expires_at")
    if expiry and expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    status = subscription["status"]
    if status == "active" and expiry and expiry <= now:
        status = "expired"
    return {
        "plan_id": subscription.get("plan_id"),
        "status": status,
        "expires_at": expiry.isoformat() if expiry else None,
        "traffic_gb": subscription["traffic_gb"],
        "device_limit": subscription["device_limit"],
        "subscription_url": subscription["subscription_url"],
        "days_left": max(0, ceil((expiry - now).total_seconds() / 86400)) if expiry else None,
    }


@router.get("/api/v1/webapp/subscription")
async def webapp_subscription(x_telegram_init_data: str | dict | None = Depends(web_credentials)):
    identity = _require_telegram_identity(x_telegram_init_data)
    await _check_webapp_membership(int(identity["id"]))
    if await customer_is_blocked(int(identity["id"])):
        raise HTTPException(status_code=403, detail=BLOCKED_MESSAGE)
    return {"subscription": await _webapp_subscription_data(int(identity["id"]))}


@router.get("/api/v1/webapp/bootstrap")
async def webapp_bootstrap(x_telegram_init_data: str | dict | None = Depends(web_credentials)):
    if not x_telegram_init_data:
        raise HTTPException(status_code=401, detail="Telegram authentication is required")
    identity = await _webapp_user(x_telegram_init_data)
    pricing = await get_store_tariff_pricing()
    payment_methods = await _get_public_checkout_methods()
    policy = await get_operations()
    return {
        "user": {"id": identity["id"], "first_name": identity.get("first_name", ""),
                 "photo_url": telegram_photo_url(identity.get("photo_url"))},
        "profile": await public_store_profile(),
        "is_admin": int(identity["id"]) in settings.admin_id_set and identity.get("browser_provider") != "google",
        "browser_provider": identity.get("browser_provider"),
        "channel_required": bool(policy["required_channel"]),
        "google_link_available": identity.get("browser_provider") == "telegram" and policy["google_login"],
        "pricing": pricing,
        "stars_enabled": payment_methods["telegram_stars"],
        "payment_methods": payment_methods,
        "terms_url": await get_terms_url(),
        "limits": {
            "days_min": settings.tariff_min_days, "days_max": settings.tariff_max_days,
            "traffic_min": settings.tariff_min_traffic_gb, "traffic_max": settings.tariff_max_traffic_gb,
            "devices_min": settings.tariff_min_devices, "devices_max": settings.tariff_max_devices,
            "days_default": min(max(30, settings.tariff_min_days), settings.tariff_max_days),
            "traffic_default": min(max(100, settings.tariff_min_traffic_gb), settings.tariff_max_traffic_gb),
            "devices_default": min(max(1, settings.tariff_min_devices), settings.tariff_max_devices),
        },
        "subscription": await _webapp_subscription_data(int(identity["id"])),
    }


class WebAppTermsAcceptance(BaseModel):
    accepted: bool


@router.get("/api/v1/webapp/notifications")
async def webapp_get_notifications(x_telegram_init_data: str | dict | None = Depends(web_credentials)):
    identity = await _webapp_user(x_telegram_init_data)
    return await get_notification_preferences(int(identity["id"]))


@router.put("/api/v1/webapp/notifications")
async def webapp_save_notifications(data: NotificationPreferences, x_telegram_init_data: str | dict | None = Depends(web_credentials)):
    identity = _require_telegram_identity(x_telegram_init_data)
    await _check_webapp_membership(int(identity["id"]))
    try:
        await save_notification_preferences(int(identity["id"]), data)
    except BlockedCustomer as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return data


class WebAppCheckout(BaseModel):
    days: int
    traffic: int
    devices: int


@router.post("/api/v1/webapp/terms/accept")
async def webapp_accept_terms(data: WebAppTermsAcceptance, x_telegram_init_data: str | dict | None = Depends(web_credentials)):
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
async def webapp_create_checkout(data: WebAppCheckout, x_telegram_init_data: str | dict | None = Depends(web_credentials)):
    _require_telegram_identity(x_telegram_init_data)
    raise HTTPException(status_code=410, detail="Конструктор отключён. Выберите готовый тариф.")


async def _require_telegram_admin(init_data: str | None) -> dict:
    identity = _require_telegram_identity(init_data)
    telegram_id = int(identity["id"])
    if telegram_id not in settings.admin_id_set or identity.get("browser_provider") == "google":
        raise HTTPException(status_code=403, detail="Administrator access denied")
    return identity


@router.get("/admin/api/notifications")
async def admin_get_notifications(x_telegram_init_data: str | dict | None = Depends(web_credentials)):
    await _require_telegram_admin(x_telegram_init_data)
    return await get_notification_settings()


@router.put("/admin/api/notifications")
async def admin_save_notifications(data: NotificationSettings, x_telegram_init_data: str | dict | None = Depends(web_credentials)):
    await _require_telegram_admin(x_telegram_init_data)
    await save_notification_settings(data)
    return data


@router.get("/admin", response_class=HTMLResponse)
async def admin_dashboard_page(request: Request):
    if is_browser_host(request):
        identity = await browser_identity(request)
        if not identity or identity["browser_provider"] != "telegram":
            return RedirectResponse(requested_login(request, admin=True), headers={"Cache-Control": "no-store"})
        if identity["id"] not in settings.admin_id_set:
            raise HTTPException(403, "Доступ к админ-панели закрыт.")
    web_dir = Path(__file__).parent
    html = (web_dir / "templates" / "admin.html").read_text(encoding="utf-8")
    for asset in ("admin.css", "admin.js"):
        revision = blake2s((web_dir / "static" / asset).read_bytes(), digest_size=8).hexdigest()
        html = html.replace(f'/static/{asset}"', f'/static/{asset}?v={revision}"')
    version = installed_release_version()
    label = f"Релиз Remnaum {version}" if version else "Версия релиза Remnaum"
    return HTMLResponse(html.replace("__REMNAUM_RELEASE__", escape(label)), headers={"Cache-Control": "no-cache"})


@router.get("/admin/api/release-version")
async def admin_release_version(x_telegram_init_data: str | dict | None = Depends(web_credentials)):
    await _require_telegram_admin(x_telegram_init_data)
    return {"version": await latest_release_version()}


@router.get("/admin/api/overview")
async def admin_overview(x_telegram_init_data: str | dict | None = Depends(web_credentials)):
    identity = await _require_telegram_admin(x_telegram_init_data)
    now = datetime.now(timezone.utc)
    first_day = now.date() - timedelta(days=29)
    from_time = datetime.combine(first_day, datetime.min.time(), timezone.utc)
    paid_statuses = ("paid", "succeeded")
    latest = (
        select(Subscription.user_id, func.max(Subscription.id).label("id"))
        .group_by(Subscription.user_id).subquery()
    )
    async with SessionLocal() as session:
        users = await session.scalar(select(func.count(User.id))) or 0
        active_groups = (await session.execute(
            select(Subscription.paid_tariff, func.count())
            .join(latest, latest.c.id == Subscription.id)
            .join(User, User.id == Subscription.user_id)
            .where(Subscription.status == "active", Subscription.expires_at > now, User.blocked_at.is_(None))
            .group_by(Subscription.paid_tariff)
        )).all()
        plan_ids = [int(key) for key, _ in active_groups if key and key.isascii() and key.isdecimal()]
        plans = dict((await session.execute(
            select(TariffPlan.id, TariffPlan.title).where(TariffPlan.id.in_(plan_ids))
        )).all()) if plan_ids else {}
        counts = dict((await session.execute(
            select(Payment.status, func.count()).where(Payment.status.in_(("succeeded", "pending")))
            .group_by(Payment.status)
        )).all())
        totals = dict((await session.execute(
            select(Payment.currency, func.sum(Payment.amount))
            .where(Payment.currency.in_(("RUB", "XTR")), Payment.status.in_(paid_statuses))
            .group_by(Payment.currency)
        )).all())
        payment_day = cast(func.timezone("UTC", Payment.created_at), Date)
        daily = dict((await session.execute(
            select(payment_day, func.sum(Payment.amount)).where(
                Payment.currency == "RUB", Payment.status.in_(paid_statuses), Payment.created_at >= from_time,
            ).group_by(payment_day)
        )).all())

    plan_breakdown = []
    for key, count in active_groups:
        if key and key.isascii() and key.isdecimal():
            name = plans.get(int(key), f"Тариф №{key}")
        else:
            name = "Гибкий тариф" if key == "flexible" else "Без привязки к тарифу"
        plan_breakdown.append({"name": name, "count": count})
    return {
        "admin_name": identity.get("first_name", ""),
        "users": users,
        "active_subscriptions": sum(count for _, count in active_groups),
        "successful_payments": counts.get("succeeded", 0),
        "pending_payments": counts.get("pending", 0),
        "revenue_rub_minor": int(totals.get("RUB", 0)),
        "stars_received": int(totals.get("XTR", 0)),
        "active_plans": sorted(plan_breakdown, key=lambda plan: (-plan["count"], plan["name"])),
        "revenue_days": [
            {"date": day.isoformat(), "amount_minor": int(daily.get(day, 0))}
            for day in (first_day + timedelta(days=offset) for offset in range(30))
        ],
    }


@router.get("/admin/api/panel-overview")
async def admin_panel_overview(x_telegram_init_data: str | dict | None = Depends(web_credentials)):
    await _require_telegram_admin(x_telegram_init_data)
    client = await panel_client(RemnawaveClient)
    if not client.base_url or not client.token:
        return {"status": "not_configured"}

    async def read(path: str) -> dict:
        try:
            async with asyncio.timeout(5):
                data = await client.request("GET", path)
            value = data.get("response") if isinstance(data, dict) else None
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}

    stats, metadata, bandwidth, nodes = await asyncio.gather(
        read("/api/system/stats"), read("/api/system/metadata"),
        read("/api/system/stats/bandwidth"), read("/api/system/nodes/metrics"),
    )
    if not stats:
        return {"status": "unavailable"}

    def record(parent, key):
        item = parent.get(key)
        return item if isinstance(item, dict) else {}

    users = record(stats, "users")
    status_counts = record(users, "statusCounts")
    memory = record(stats, "memory")
    total, used = memory.get("total"), memory.get("used")
    version = metadata.get("version")
    node_items = nodes.get("nodes")
    return {
        "status": "available",
        "version": version if isinstance(version, str) else None,
        "users": users.get("totalUsers"),
        "online": record(stats, "onlineStats").get("onlineNow"),
        "active": status_counts.get("ACTIVE"),
        "expired": status_counts.get("EXPIRED"),
        "disabled": status_counts.get("DISABLED"),
        "limited": status_counts.get("LIMITED"),
        "nodes_online": record(stats, "nodes").get("totalOnline"),
        "memory_percent": round(100 * used / total, 1) if type(total) is int and total > 0 and type(used) is int else None,
        "traffic_7_days": record(bandwidth, "bandwidthLastSevenDays").get("current"),
        "traffic_30_days": record(bandwidth, "bandwidthLast30Days").get("current"),
        "nodes": [
            {"name": node.get("nodeName"), "online": node.get("usersOnline")}
            for node in node_items[:30] if isinstance(node, dict)
        ] if isinstance(node_items, list) else [],
    }


@router.get("/admin/api/users")
async def admin_users(
    limit: int = 20,
    offset: int = 0,
    x_telegram_init_data: str | dict | None = Depends(web_credentials),
    q: str = "",
):
    await _require_telegram_admin(x_telegram_init_data)
    if limit < 1 or limit > 100 or offset < 0:
        raise HTTPException(status_code=422, detail="Invalid pagination")
    latest_subscription = (
        select(Subscription.user_id, func.max(Subscription.id).label("subscription_id"))
        .group_by(Subscription.user_id)
        .subquery()
    )
    query = q.strip()[:80].lstrip("@")
    filters = []
    if query:
        matches = [User.username.icontains(query, autoescape=True), User.display_name.icontains(query, autoescape=True)]
        if query.isdecimal() and len(query) < 17:
            matches.append(User.telegram_id == int(query))
        filters.append(or_(*matches))
    async with SessionLocal() as session:
        total = await session.scalar(select(func.count(User.id)).where(*filters)) or 0
        result = await session.execute(
            select(User, Subscription)
            .outerjoin(latest_subscription, latest_subscription.c.user_id == User.id)
            .outerjoin(Subscription, Subscription.id == latest_subscription.c.subscription_id)
            .where(*filters)
            .order_by(User.created_at.desc(), User.id.desc())
            .limit(limit)
            .offset(offset)
        )
        items = []
        for user, subscription in result.all():
            items.append({
                "telegram_id": user.telegram_id,
                "username": user.username,
                "display_name": user.display_name,
                "photo_url": user.telegram_photo_url,
                "blocked": user.blocked_at is not None,
                "created_at": user.created_at.isoformat(),
                "subscription_status": subscription.status if subscription else "нет подписки",
                "expires_at": subscription.expires_at.isoformat() if subscription and subscription.expires_at else None,
            })
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def _payment_summary(payment: Payment) -> dict:
    try:
        order = json.loads(payment.payload or "{}")
    except (TypeError, ValueError):
        order = {}
    order = order if isinstance(order, dict) else {}
    tariff = order.get("tariff")
    tariff = tariff if isinstance(tariff, dict) else {}
    plan_id = order.get("plan_id")
    return {
        "id": payment.id, "provider": payment.provider, "amount": payment.amount,
        "currency": payment.currency, "status": payment.status,
        "created_at": payment.created_at.isoformat(),
        "plan_id": plan_id if type(plan_id) is int and plan_id > 0 else None,
        "plan_name": tariff.get("name") if isinstance(tariff.get("name"), str) else None,
        "duration_days": tariff.get("duration_days") if type(tariff.get("duration_days")) is int else None,
        "extra_devices": tariff.get("extra_devices") if type(tariff.get("extra_devices")) is int else 0,
    }


@router.get("/admin/api/payments")
async def admin_payments(
    limit: int = 20,
    offset: int = 0,
    x_telegram_init_data: str | dict | None = Depends(web_credentials),
):
    await _require_telegram_admin(x_telegram_init_data)
    if limit < 1 or limit > 100 or offset < 0:
        raise HTTPException(status_code=422, detail="Invalid pagination")
    async with SessionLocal() as session:
        total = await session.scalar(select(func.count(Payment.id))) or 0
        result = await session.execute(
            select(Payment, User.telegram_id, User.username)
            .outerjoin(User, User.id == Payment.user_id)
            .order_by(Payment.created_at.desc(), Payment.id.desc())
            .limit(limit)
            .offset(offset)
        )
        items = [
            _payment_summary(payment) | {"telegram_id": telegram_id, "username": username}
            for payment, telegram_id, username in result.all()
        ]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/admin/api/users/{telegram_id}")
async def admin_user_detail(
    telegram_id: int,
    x_telegram_init_data: str | dict | None = Depends(web_credentials),
):
    await _require_telegram_admin(x_telegram_init_data)
    if telegram_id < 1:
        raise HTTPException(status_code=422, detail="Invalid Telegram ID")
    async with SessionLocal() as session:
        user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
        if user is None:
            raise HTTPException(status_code=404, detail="User not found")
        subscriptions = (await session.execute(
            select(Subscription, SubscriptionDetails)
            .outerjoin(SubscriptionDetails, SubscriptionDetails.subscription_id == Subscription.id)
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
        "display_name": user.display_name,
        "photo_url": user.telegram_photo_url,
        "blocked": user.blocked_at is not None,
        "is_admin": telegram_id in settings.admin_id_set,
        "created_at": user.created_at.isoformat(),
        "subscriptions": [{
            "id": subscription.id,
            "status": subscription.status,
            "expires_at": subscription.expires_at.isoformat() if subscription.expires_at else None,
            "created_at": subscription.created_at.isoformat(),
            "plan_id": int(subscription.paid_tariff) if subscription.paid_tariff and subscription.paid_tariff.isdigit() else None,
            "traffic_gb": details.traffic_gb if details else None,
            "device_limit": details.device_limit if details else None,
        } for subscription, details in subscriptions],
        "payments": [{
            "id": payment.id,
            "provider": payment.provider,
            "amount": payment.amount,
            "currency": payment.currency,
            "status": payment.status,
            "created_at": payment.created_at.isoformat(),
        } for payment in payments],
    }


@router.get("/admin/api/users/{telegram_id}/avatar")
async def admin_customer_avatar(telegram_id: int, x_telegram_init_data: str | dict | None = Depends(web_credentials)):
    await _require_telegram_admin(x_telegram_init_data)
    async with SessionLocal() as session:
        registered = await session.scalar(select(User.id).where(User.telegram_id == telegram_id))
    if not bot or not registered:
        raise HTTPException(status_code=404, detail="Нет фотографии")
    try:
        async with asyncio.timeout(5):
            photos = await bot.get_user_profile_photos(telegram_id, limit=1)
            if photos.photos:
                photo = photos.photos[0][0]
                target = BytesIO()
                await bot.download(photo.file_id, destination=target)
                return Response(target.getvalue(), media_type="image/jpeg", headers={"Cache-Control": "private, no-store"})
    except (TelegramAPIError, TimeoutError):
        pass
    raise HTTPException(status_code=404, detail="Нет фотографии")


class CustomerProfileUpdate(BaseModel):
    display_name: str = Field(max_length=80)


class CustomerBlockUpdate(BaseModel):
    blocked: bool


class CustomerSubscriptionUpdate(BaseModel):
    plan_id: int = Field(gt=0)
    expires_at: datetime
    traffic_gb: int = Field(ge=0, le=1_000_000)
    device_limit: int = Field(ge=1, le=1000)


class CustomerRemoval(BaseModel):
    confirm_telegram_id: int = Field(gt=0)


async def _customer_operation(operation, *args, **kwargs):
    try:
        await operation(*args, **kwargs)
    except customer_admin.CustomerNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (RuntimeError, httpx.HTTPError) as exc:
        logger.warning("Customer operation %s could not synchronize with Remnawave: %s",
                       operation.__name__, type(exc).__name__)
        detail = str(exc) if isinstance(exc, RuntimeError) else "Remnawave не подтвердил операцию. Повторите попытку."
        raise HTTPException(status_code=502, detail=detail) from exc
    return {"ok": True}


@router.patch("/admin/api/users/{telegram_id}")
async def admin_change_customer_profile(
    telegram_id: int, data: CustomerProfileUpdate, x_telegram_init_data: str | dict | None = Depends(web_credentials),
):
    await _require_telegram_admin(x_telegram_init_data)
    return await _customer_operation(customer_admin.change_display_name, telegram_id, data.display_name)


@router.put("/admin/api/users/{telegram_id}/block")
async def admin_change_customer_block(
    telegram_id: int, data: CustomerBlockUpdate, x_telegram_init_data: str | dict | None = Depends(web_credentials),
):
    await _require_telegram_admin(x_telegram_init_data)
    return await _customer_operation(customer_admin.set_customer_block, telegram_id, data.blocked)


@router.put("/admin/api/users/{telegram_id}/subscription")
async def admin_save_customer_subscription(
    telegram_id: int, data: CustomerSubscriptionUpdate, x_telegram_init_data: str | dict | None = Depends(web_credentials),
):
    await _require_telegram_admin(x_telegram_init_data)
    return await _customer_operation(customer_admin.save_customer_subscription, telegram_id, **data.model_dump())


@router.delete("/admin/api/users/{telegram_id}/subscription")
async def admin_remove_customer_subscription(
    telegram_id: int, x_telegram_init_data: str | dict | None = Depends(web_credentials),
):
    await _require_telegram_admin(x_telegram_init_data)
    return await _customer_operation(customer_admin.remove_customer, telegram_id, delete_account=False)


@router.delete("/admin/api/users/{telegram_id}")
async def admin_remove_customer(
    telegram_id: int, data: CustomerRemoval, x_telegram_init_data: str | dict | None = Depends(web_credentials),
):
    await _require_telegram_admin(x_telegram_init_data)
    if data.confirm_telegram_id != telegram_id:
        raise HTTPException(status_code=422, detail="Для удаления подтвердите Telegram ID клиента.")
    return await _customer_operation(customer_admin.remove_customer, telegram_id, delete_account=True)


@router.post("/webhooks/remnawave")
async def remnawave_webhook(request: Request, x_remnawave_signature: str | None = Header(default=None)):
    from app.services.remnawave_webhooks import verify_remnawave_webhook
    from app.services.remnawave_events import record_and_reconcile

    body = await request.body()
    try:
        policy = await get_operations(credentials=True)
        event = verify_remnawave_webhook(
            body, x_remnawave_signature, policy.get("panel_webhook_secret") or settings.remnawave_webhook_secret,
        )
        is_new = await record_and_reconcile(body, event)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return {"ok": True, "event": event["event"], "duplicate": not is_new}


# Fixed plans share the existing Telegram-authenticated admin dashboard.
from app.services.squads import get_catalog, refresh_catalog, mode_squads
from app.db.models import SquadPolicy, InternalSquadCatalog
from app.services.plans import list_plans, save_plan


@router.get("/admin/api/squad-policies")
async def admin_squad_policies(x_telegram_init_data: str | dict | None = Depends(web_credentials)):
    await _require_telegram_admin(x_telegram_init_data)
    async with SessionLocal() as session:
        return {mode: await mode_squads(session, mode) for mode in ("trial", "flexible")}


@router.put("/admin/api/squad-policies/{mode}")
async def save_squad_policy(mode: str, selected: list[str], x_telegram_init_data: str | dict | None = Depends(web_credentials)):
    await _require_telegram_admin(x_telegram_init_data)
    if mode not in ("trial", "flexible"):
        raise HTTPException(404)
    async with SessionLocal.begin() as session:
        policy = await session.get(SquadPolicy, mode, with_for_update=True)
        catalog = await session.get(InternalSquadCatalog, 1)
        for key in set(selected) - set(policy.squads):
            if not catalog or not catalog.items.get(key, {}).get("available"):
                raise HTTPException(422, "Сквад недоступен")
        policy.squads = list(dict.fromkeys(selected))
        return policy.squads


@router.get("/admin/api/internal-squads")
async def admin_internal_squads(x_telegram_init_data: str | dict | None = Depends(web_credentials)):
    await _require_telegram_admin(x_telegram_init_data)
    return await get_catalog()


@router.post("/admin/api/internal-squads/refresh")
async def admin_refresh_internal_squads(x_telegram_init_data: str | dict | None = Depends(web_credentials)):
    await _require_telegram_admin(x_telegram_init_data)
    return await refresh_catalog()


class PlanPayload(BaseModel):
    internal_squads: list[str] | None = None
    title: str = Field(min_length=1, max_length=80, validation_alias=AliasChoices("title", "name"))
    description: str = Field(default="", max_length=2000)
    duration_days: int = Field(ge=1, le=365, validation_alias=AliasChoices("duration_days", "days"))
    traffic_gb: int = Field(ge=0, le=1000000)
    device_limit: int = Field(ge=1, le=1000, validation_alias=AliasChoices("device_limit", "devices_included"))
    price_rub_minor: int = Field(ge=0, le=100000000)
    extra_device_price_rub_minor: int | None = Field(default=None, ge=0, le=100000000)
    extra_device_price_stars: int | None = Field(default=None, ge=0, le=1000000)
    badge: str = Field(default="", max_length=32)
    is_featured: bool = False
    price_stars: int = Field(ge=1, le=1000000)
    is_active: bool = True
    sort_order: int = Field(default=0, ge=0, le=10000)


    @model_validator(mode="before")
    @classmethod
    def accept_rubles(cls, data):
        if not isinstance(data, dict):
            return data
        data = data.copy()
        for field in ("price_rub", "extra_device_price_rub"):
            if field not in data or field + "_minor" in data:
                continue
            value = data[field]
            if value is None and field == "extra_device_price_rub":
                data[field + "_minor"] = None
                continue
            try:
                minor = Decimal(str(value)) * 100
                if not minor.is_finite() or not 0 <= minor <= 100_000_000 or minor != minor.to_integral_value():
                    raise ValueError
            except (ArithmeticError, ValueError):
                raise ValueError(f"Недопустимая сумма в рублях: {field}") from None
            data[field + "_minor"] = int(minor)
        return data


@router.get("/api/v1/plans")
async def public_fixed_plans():
    return {"items": await list_plans(active_only=True)}


@router.get("/admin/api/plans")
async def admin_list_fixed_plans(x_telegram_init_data: str | dict | None = Depends(web_credentials)):
    await _require_telegram_admin(x_telegram_init_data)
    return {"items": await list_plans(active_only=False)}


@router.post("/admin/api/plans")
async def admin_create_fixed_plan(
    data: PlanPayload, x_telegram_init_data: str | dict | None = Depends(web_credentials)
):
    await _require_telegram_admin(x_telegram_init_data)
    try:
        return await save_plan(data.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/admin/api/plans/{plan_id}")
async def admin_update_fixed_plan(
    plan_id: int, data: PlanPayload, x_telegram_init_data: str | dict | None = Depends(web_credentials)
):
    await _require_telegram_admin(x_telegram_init_data)
    if plan_id < 1:
        raise HTTPException(status_code=422, detail="Invalid plan ID")
    try:
        return await save_plan(data.model_dump(exclude_unset=True), plan_id=plan_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


class WebAppFixedPlanCheckout(BaseModel):
    plan_id: int = Field(ge=1)
    extra_devices: int = Field(default=0, ge=0, le=1000, strict=True)
    revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{20}$")


@router.post("/api/v1/webapp/plans/checkout")
async def webapp_checkout_ready_plan(data: WebAppFixedPlanCheckout, x_telegram_init_data: str | dict | None = Depends(web_credentials)):
    if not x_telegram_init_data:
        raise HTTPException(status_code=401, detail="Telegram authentication is required")
    identity = await _webapp_user(x_telegram_init_data)
    telegram_id = int(identity["id"])
    if identity.get("browser_provider"):
        raise HTTPException(403, "Оплата Stars доступна в Telegram.")
    if not await is_payment_method_enabled("telegram_stars"):
        raise HTTPException(status_code=403, detail="Оплата в Telegram Stars отключена.")
    if bot is None:
        raise HTTPException(status_code=503, detail="Оплата в Telegram Stars временно недоступна.")
    try:
        payment, stars = await create_stars_payment_for_user(telegram_id, **data.model_dump())
        tariff = json.loads(payment.payload)["tariff"]
    except TermsAcceptanceRequired as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        url = await bot.create_invoice_link(
            title=(await get_store_profile())["name"][:28] + " VPN",
            description=(f"VPN на {tariff['duration_days']} дней · {format_traffic(tariff['traffic_gb'])}"
                         f" · устройств: {tariff['device_limit']}"),
            payload=f"remnaum:{payment.id}", provider_token="", currency="XTR",
            prices=[LabeledPrice(label="VPN-подписка", amount=stars)],
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Не удалось открыть счёт Telegram Stars. Попробуйте ещё раз.") from exc
    return {"payment_id": payment.id, "stars": stars, "invoice_url": url}


from app.services.store_profile import COLOR_OPACITY, THEME_COLORS, get_store_profile, update_store_profile, get_terms_url


class StoreProfilePayload(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    tagline: str = Field(default="", max_length=300)
    description: str = Field(default="", max_length=300)
    connection_instructions: str = Field(default="", max_length=1600)
    bot_connection_instructions: str = Field(default="", max_length=1600)
    accent_color: str = Field(default="#4b8bea", pattern=r"^#[a-fA-F0-9]{6}$")
    support_contact: str = Field(default="", max_length=300)
    terms_url: str = Field(default="", max_length=300)


class StoreDocumentPayload(BaseModel):
    body: str = Field(max_length=DOCUMENT_MAX_LENGTH)


@router.get("/admin/api/documents")
async def admin_list_documents(x_telegram_init_data: str | dict | None = Depends(web_credentials)):
    await _require_telegram_admin(x_telegram_init_data)
    return {"documents": await list_documents()}


@router.put("/admin/api/documents/{kind}")
async def admin_save_document(
    kind: DocumentKind, data: StoreDocumentPayload,
    x_telegram_init_data: str | dict | None = Depends(web_credentials),
):
    await _require_telegram_admin(x_telegram_init_data)
    body = data.body.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not body:
        raise HTTPException(status_code=422, detail="Введите текст документа.")
    if "\x00" in body:
        raise HTTPException(status_code=422, detail="Текст содержит недопустимый нулевой символ.")
    await save_document(kind, body)
    return {"body": body}


from app.services.store_media import (
    delete_logo_image, delete_welcome_image, image_path, logo_image_url, save_logo_image,
    save_welcome_image, welcome_image_path, welcome_image_url,
)

@router.get("/api/v1/store-profile")
async def public_store_profile():
    profile = {**await get_store_profile(), "welcome_image_url": welcome_image_url()}
    if logo := logo_image_url():
        profile["logo_image_url"] = logo
    return profile


@router.get("/admin/api/store-profile")
async def admin_get_store_profile(x_telegram_init_data: str | dict | None = Depends(web_credentials)):
    viewer = await _require_telegram_admin(x_telegram_init_data)
    return (await public_store_profile()) | {"viewer": {"id": viewer.get("id"), "photo_url": telegram_photo_url(viewer.get("photo_url"))}}




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
    x_telegram_init_data: str | dict | None = Depends(web_credentials),
):
    await _require_telegram_admin(x_telegram_init_data)
    try:
        return {"url": save_welcome_image(data.data_base64)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/admin/api/store-profile/welcome-image")
async def admin_delete_welcome_image(x_telegram_init_data: str | dict | None = Depends(web_credentials)):
    await _require_telegram_admin(x_telegram_init_data)
    delete_welcome_image()
    return {"url": None}


@router.get("/api/v1/store-profile/logo-image")
async def public_logo_image():
    logo = image_path("logo")
    if logo is None:
        raise HTTPException(status_code=404, detail="Логотип не установлен")
    return FileResponse(
        logo, media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=300", "X-Content-Type-Options": "nosniff"},
    )


@router.put("/admin/api/store-profile/logo-image")
async def admin_upload_logo_image(
    data: WelcomeImagePayload, x_telegram_init_data: str | dict | None = Depends(web_credentials),
):
    await _require_telegram_admin(x_telegram_init_data)
    try:
        return {"url": save_logo_image(data.data_base64)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/admin/api/store-profile/logo-image")
async def admin_remove_logo_image(x_telegram_init_data: str | dict | None = Depends(web_credentials)):
    await _require_telegram_admin(x_telegram_init_data)
    delete_logo_image()
    return {"url": None}


@router.get("/admin/api/bot-identity")
async def admin_bot_identity(x_telegram_init_data: str | dict | None = Depends(web_credentials)):
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
    data: StoreProfilePayload, x_telegram_init_data: str | dict | None = Depends(web_credentials)
):
    await _require_telegram_admin(x_telegram_init_data)
    try:
        return await update_store_profile(data.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/api/v1/webapp/purchases")
async def webapp_purchases(x_telegram_init_data: str | dict | None = Depends(web_credentials)):
    if not x_telegram_init_data:
        raise HTTPException(status_code=401, detail="Telegram authentication is required")
    identity = await _webapp_user(x_telegram_init_data)
    async with SessionLocal() as session:
        payments = (await session.scalars(
            select(Payment).join(User, Payment.user_id == User.id)
            .where(User.telegram_id == int(identity["id"]))
            .order_by(Payment.created_at.desc(), Payment.id.desc()).limit(20)
        )).all()
        return {"items": [_payment_summary(payment) for payment in payments]}


@router.get("/api/v1/webapp/devices")
async def webapp_hwid_devices(x_telegram_init_data: str | dict | None = Depends(web_credentials)):
    if not x_telegram_init_data:
        raise HTTPException(status_code=401, detail="Telegram authentication is required")
    identity = await _webapp_user(x_telegram_init_data)
    return await _device_request(get_customer_devices, int(identity["id"]))


async def _device_request(operation, *args, **kwargs):
    try:
        return await operation(*args, **kwargs)
    except BlockedCustomer as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except DeviceNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PanelAccountConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DevicesUnavailable as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


class DeviceNameUpdate(BaseModel):
    name: str = Field(max_length=48)


@router.patch("/api/v1/webapp/devices/{device_id}")
async def webapp_update_device_name(device_id: str, data: DeviceNameUpdate,
                                    x_telegram_init_data: str | dict | None = Depends(web_credentials)):
    identity = _require_telegram_identity(x_telegram_init_data)
    await _check_webapp_membership(int(identity["id"]))
    return await _device_request(change_customer_device, int(identity["id"]), device_id, name=data.name)


@router.delete("/api/v1/webapp/devices/{device_id}")
async def webapp_remove_device(device_id: str, x_telegram_init_data: str | dict | None = Depends(web_credentials)):
    identity = _require_telegram_identity(x_telegram_init_data)
    await _check_webapp_membership(int(identity["id"]))
    await _device_request(change_customer_device, int(identity["id"]), device_id, name=None)
    return {"ok": True}


@router.get("/api/v1/webapp/referrals")
async def webapp_referrals(x_telegram_init_data: str | dict | None = Depends(web_credentials)):
    from app.services.referrals import referral_code, referral_count, get_referral_settings, reward_balance
    if not x_telegram_init_data:
        raise HTTPException(status_code=401, detail="Telegram authentication is required")
    identity = await _webapp_user(x_telegram_init_data)
    telegram_id = int(identity["id"])
    from app.services.bot_identity import bot_username
    username = bot_username()
    referral_settings = await get_referral_settings()
    return {
        "link": f"https://t.me/{username}?start={referral_code(telegram_id)}" if username else None,
        "count": await referral_count(telegram_id),
        "rewards_enabled": referral_settings["enabled"],
        "reward_days": referral_settings["reward_days"],
        "reward": await reward_balance(telegram_id),
    }



from app.services.referrals import get_referral_settings, update_referral_settings


class ReferralSettingsPayload(BaseModel):
    enabled: bool
    reward_days: int = Field(ge=1, le=90)


@router.get("/admin/api/referrals")
async def admin_referrals(x_telegram_init_data: str | dict | None = Depends(web_credentials)):
    await _require_telegram_admin(x_telegram_init_data)
    return await get_referral_settings()


@router.put("/admin/api/referrals")
async def admin_update_referrals(
    data: ReferralSettingsPayload, x_telegram_init_data: str | dict | None = Depends(web_credentials)
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
async def admin_get_trial(x_telegram_init_data: str | dict | None = Depends(web_credentials)):
    await _require_telegram_admin(x_telegram_init_data)
    return await get_trial_settings()


@router.put("/admin/api/trial")
async def admin_put_trial(
    data: TrialSettingsPayload, x_telegram_init_data: str | dict | None = Depends(web_credentials)
):
    await _require_telegram_admin(x_telegram_init_data)
    try:
        return await update_trial_settings(data.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/admin/api/promos")
async def admin_get_promos(x_telegram_init_data: str | dict | None = Depends(web_credentials)):
    await _require_telegram_admin(x_telegram_init_data)
    return {"items": await list_promos()}


@router.post("/admin/api/promos")
async def admin_create_promo(
    data: PromoCreatePayload, x_telegram_init_data: str | dict | None = Depends(web_credentials)
):
    await _require_telegram_admin(x_telegram_init_data)
    try:
        return await create_promo(data.code, data.bonus_days, data.max_uses)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/admin/api/promos/{promo_id}")
async def admin_put_promo(
    promo_id: int, data: PromoStatusPayload,
    x_telegram_init_data: str | dict | None = Depends(web_credentials),
):
    await _require_telegram_admin(x_telegram_init_data)
    try:
        return await set_promo_active(promo_id, data.active)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/api/v1/webapp/offers")
async def webapp_offers(x_telegram_init_data: str | dict | None = Depends(web_credentials)):
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
async def webapp_claim_trial(x_telegram_init_data: str | dict | None = Depends(web_credentials)):
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
    x_telegram_init_data: str | dict | None = Depends(web_credentials),
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


class WebAppExternalCheckout(WebAppCheckout):
    provider: str


class WebAppPlanExternalCheckout(WebAppFixedPlanCheckout):
    provider: str


def _validate_external_provider(name: str) -> None:
    if name not in PROVIDER_LABELS:
        raise HTTPException(status_code=422, detail="Unknown external provider")


async def _external_checkout(telegram_id: int, provider: str, **plan_options):
    try:
        payment, link = await create_payment_for_user(telegram_id, provider, **plan_options)
    except TermsAcceptanceRequired as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except PaymentProviderUnavailable as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if urlsplit(link.url).scheme != "https":
        raise HTTPException(status_code=502, detail="Payment provider returned a non-HTTPS link")
    return {"payment_id": payment.id, "provider": provider, "url": link.url}


@router.post("/api/v1/webapp/external/checkout")
async def webapp_external_checkout(
    data: WebAppExternalCheckout, x_telegram_init_data: str | dict | None = Depends(web_credentials),
):
    _require_telegram_identity(x_telegram_init_data)
    raise HTTPException(status_code=410, detail="Конструктор отключён. Выберите готовый тариф.")


@router.post("/api/v1/webapp/plans/external/checkout")
async def webapp_external_plan_checkout(
    data: WebAppPlanExternalCheckout, x_telegram_init_data: str | dict | None = Depends(web_credentials),
):
    if not x_telegram_init_data:
        raise HTTPException(status_code=401, detail="Telegram authentication is required")
    identity = await _webapp_user(x_telegram_init_data)
    _validate_external_provider(data.provider)
    telegram_id = int(identity["id"])
    return await _external_checkout(telegram_id, data.provider, **data.model_dump(exclude={"provider"}))


async def _get_public_checkout_methods():
    methods, credentials = await get_store_payment_settings()
    configured = public_payment_settings(methods, credentials)["configured"]
    configured["telegram_stars"] = bot is not None
    return {key: bool(methods.get(key) and configured.get(key)) for key in DEFAULT_METHODS}


from typing import Annotated, Literal

from fastapi import Query
from pydantic import StringConstraints

from app.bot.campaigns import campaign_lock, run_campaign
from app.services import support
from app.services.bot_identity import bot_username
from app.services.store_operations import save_operations

WebCredentials = Annotated[str | dict | None, Depends(web_credentials)]


class OperationsPayload(BaseModel):
    telegram_login: bool = True
    google_login: bool = False
    google_client_id: str = Field(default="", max_length=255)
    google_client_secret: str = Field(default="", max_length=512)
    required_channel: str = Field(pattern=r"^(?:@[a-zA-Z0-9_]{5,32}|-100[0-9]{5,16})?$")
    channel_url: str = Field(max_length=300)
    extra_devices_max: int = Field(ge=0, le=1000, strict=True)
    panel_url: str = Field(max_length=500)
    panel_token: str = Field(default="", max_length=4096)
    panel_webhook_secret: str = Field(default="", max_length=512)

    @model_validator(mode="after")
    def validate_urls(self):
        if self.required_channel:
            channel = urlsplit(self.channel_url)
            if channel.scheme != "https" or channel.netloc.lower() != "t.me" or not channel.path.strip("/"):
                raise ValueError("Укажите HTTPS-ссылку на Telegram-канал.")
        if self.panel_url:
            panel = urlsplit(self.panel_url)
            if (panel.scheme != "https" or not panel.hostname or panel.username is not None
                    or panel.query or panel.fragment or panel.path not in {"", "/"}):
                raise ValueError("Укажите HTTPS-адрес панели без пути и параметров.")
            _ = panel.port
            self.panel_url = self.panel_url.rstrip("/")
        return self


class BotButtonsPayload(BaseModel):
    buttons: dict[str, ButtonStyle] = Field(max_length=len(BUTTONS))


@router.get("/admin/api/bot-buttons")
async def admin_bot_buttons(x_telegram_init_data: WebCredentials):
    await _require_telegram_admin(x_telegram_init_data)
    return bot_button_catalog((await get_operations())["bot_buttons"])


def bot_button_catalog(saved):
    return {"items": [
        {"key": key, "section": section, "label": label, "default_emoji": emoji, **(
            saved.get(key) or {"emoji": emoji, "style": ""}
        )}
        for key, (section, label, emoji) in BUTTONS.items()
    ]}


@router.put("/admin/api/bot-buttons")
async def admin_save_bot_buttons(data: BotButtonsPayload, x_telegram_init_data: WebCredentials):
    await _require_telegram_admin(x_telegram_init_data)
    if data.buttons.keys() - BUTTONS.keys():
        raise HTTPException(422, "Неизвестная кнопка.")
    styles = {
        key: value.model_dump() for key, value in data.buttons.items()
        if value.emoji != BUTTONS[key][2] or value.style
    }
    await save_operations({"bot_buttons": styles}, {})
    return bot_button_catalog(styles)


@router.get("/admin/api/operations")
async def admin_operations(x_telegram_init_data: WebCredentials):
    await _require_telegram_admin(x_telegram_init_data)
    values = await get_operations(credentials=True)
    return {key: values[key] for key in OperationsPayload.model_fields
            if key in values and key not in {"google_client_secret", "panel_token", "panel_webhook_secret"}} | {
        "panel_configured": bool((values["panel_url"] or settings.remnawave_base_url)
                                 and (values.get("panel_token") or settings.remnawave_api_token)),
        "panel_webhook_configured": bool(values.get("panel_webhook_secret") or settings.remnawave_webhook_secret),
        "browser_origin": app_origin(),
        "google_callback_url": app_origin() + "/auth/google/callback",
        "google_configured": bool(values["google_client_id"] and values.get("google_client_secret")),
        "telegram_configured": bool(bot_username() and settings.telegram_bot_token),
    }


@router.put("/admin/api/operations")
async def admin_save_operations(data: OperationsPayload, x_telegram_init_data: WebCredentials):
    await _require_telegram_admin(x_telegram_init_data)
    if data.required_channel:
        if bot is None:
            raise HTTPException(422, "Подключите Telegram-бота.")
        target = int(data.required_channel) if data.required_channel.startswith("-") else data.required_channel
        try:
            member = await bot.get_chat_member(target, bot.id)
        except TelegramAPIError as exc:
            raise HTTPException(422, "Бот не смог открыть указанный канал.") from exc
        if member.status not in {"administrator", "creator"}:
            raise HTTPException(422, "Добавьте бота в администраторы канала.")
    if data.panel_url:
        old = await get_operations(credentials=True)
        if data.panel_token or old["panel_url"] != data.panel_url:
            if not data.panel_token:
                raise HTTPException(422, "При смене URL укажите API-токен панели.")
            try:
                await RemnawaveClient(base_url=data.panel_url, token=data.panel_token).request(
                    "GET", "/api/internal-squads",
                )
            except (httpx.HTTPError, ValueError) as exc:
                raise HTTPException(422, "Не удалось подключиться к панели. Проверьте адрес и токен.") from exc
    secrets = {"google_client_secret", "panel_token", "panel_webhook_secret"}
    try:
        await save_operations(data.model_dump(exclude=secrets, exclude_unset=True), {
            key: getattr(data, key).strip() for key in secrets
        })
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return await admin_operations(x_telegram_init_data)


class TelegramLoginSettings(BaseModel):
    enabled: bool


class GoogleLoginSettings(TelegramLoginSettings):
    client_id: Annotated[str, StringConstraints(strip_whitespace=True, max_length=255)]
    client_secret: Annotated[str, StringConstraints(strip_whitespace=True, max_length=512)] = ""


@router.put("/admin/api/login/telegram")
async def admin_save_telegram_login(data: TelegramLoginSettings, x_telegram_init_data: WebCredentials):
    await _require_telegram_admin(x_telegram_init_data)
    await save_operations({"telegram_login": data.enabled}, {})
    return {"enabled": data.enabled}


@router.put("/admin/api/login/google")
async def admin_save_google_login(data: GoogleLoginSettings, x_telegram_init_data: WebCredentials):
    await _require_telegram_admin(x_telegram_init_data)
    try:
        await save_operations({"google_login": data.enabled, "google_client_id": data.client_id},
                              {"google_client_secret": data.client_secret})
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"enabled": data.enabled}


class CampaignPayload(BaseModel):
    action: Literal["broadcast", "refresh"]
    text: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] = ""


@router.post("/admin/api/campaigns", status_code=202)
async def admin_campaign(data: CampaignPayload, background: BackgroundTasks, x_telegram_init_data: WebCredentials):
    admin = await _require_telegram_admin(x_telegram_init_data)
    if bot is None:
        raise HTTPException(503, "Telegram-бот не настроен.")
    if data.action == "broadcast" and not data.text:
        raise HTTPException(422, "Введите текст рассылки.")
    lock = await campaign_lock()
    if lock is None:
        raise HTTPException(409, "Уже выполняется рассылка или обновление меню.")
    background.add_task(run_campaign, bot, int(admin["id"]), data.text if data.action == "broadcast" else None, lock)
    return {"queued": True}


HexColor = Annotated[str, StringConstraints(pattern=r"^#[0-9a-fA-F]{6}$")]


class ThemePayload(BaseModel):
    colors: dict[str, HexColor] = Field(max_length=len(THEME_COLORS))


@router.get("/store-theme.css", include_in_schema=False)
async def store_theme_css():
    colors = (await get_store_profile())["theme_colors"]
    rules = "".join(f"--color-{key}:{value}{COLOR_OPACITY.get(key, '')};" for key, value in colors.items())
    return Response(f":root,[data-theme=monochrome]{{{rules}}}", media_type="text/css",
                    headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"})


@router.get("/api/v1/webapp/theme")
async def webapp_theme(x_telegram_init_data: WebCredentials):
    await _webapp_user(x_telegram_init_data)
    return {"colors": (await get_store_profile())["theme_colors"]}


@router.put("/admin/api/theme")
async def admin_save_theme(data: ThemePayload, x_telegram_init_data: WebCredentials):
    await _require_telegram_admin(x_telegram_init_data)
    try:
        profile = await update_store_profile({"theme_colors": data.colors})
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"colors": profile["theme_colors"]}


class SupportSettings(BaseModel):
    support_contact: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)]


@router.put("/admin/api/support-settings")
async def admin_save_support_settings(data: SupportSettings, x_telegram_init_data: WebCredentials):
    await _require_telegram_admin(x_telegram_init_data)
    profile = await update_store_profile(data.model_dump())
    return {"support_contact": profile["support_contact"]}


TicketBody = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=3000)]


class ReplyPayload(BaseModel):
    body: TicketBody


class TicketPayload(ReplyPayload):
    subject: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


class TicketState(BaseModel):
    closed: bool


TicketFilter = Literal["all", "open", "closed"]
TicketId = Annotated[int, Field(gt=0, le=2_147_483_647)]


@router.get("/api/v1/webapp/tickets")
async def webapp_tickets(
    x_telegram_init_data: WebCredentials, offset: int = Query(default=0, ge=0, le=2_147_483_647),
    state: TicketFilter = "all", search: str = Query(default="", max_length=120),
):
    identity = await _webapp_user(x_telegram_init_data)
    return await support.list_tickets(int(identity["id"]), offset, state, search.strip())


@router.post("/api/v1/webapp/tickets", status_code=201)
async def webapp_open_ticket(data: TicketPayload, background: BackgroundTasks, x_telegram_init_data: WebCredentials):
    identity = await _webapp_user(x_telegram_init_data)
    ticket_id = await support.create_ticket(int(identity["id"]), data.subject, data.body)
    background.add_task(support.notify_ticket, bot, ticket_id, event="created")
    return {"id": ticket_id}


@router.get("/api/v1/webapp/tickets/{ticket_id}")
async def webapp_ticket(ticket_id: TicketId, x_telegram_init_data: WebCredentials):
    identity = await _webapp_user(x_telegram_init_data)
    return await _ticket_detail(ticket_id, int(identity["id"]))


@router.post("/api/v1/webapp/tickets/{ticket_id}/replies")
async def webapp_reply(ticket_id: TicketId, data: ReplyPayload, background: BackgroundTasks, x_telegram_init_data: WebCredentials):
    identity = await _webapp_user(x_telegram_init_data)
    return await _ticket_reply(ticket_id, data.body, background, int(identity["id"]))


@router.get("/admin/api/tickets")
async def admin_tickets(
    x_telegram_init_data: WebCredentials, offset: int = Query(default=0, ge=0, le=2_147_483_647),
    state: TicketFilter = "all", search: str = Query(default="", max_length=120),
):
    await _require_telegram_admin(x_telegram_init_data)
    return await support.list_tickets(None, offset, state, search.strip())


async def _ticket_detail(ticket_id, user_id=None):
    item = await support.get_ticket(ticket_id, user_id)
    if item is None:
        raise HTTPException(404, "Тикет не найден.")
    return item


async def _ticket_reply(ticket_id, body, background, user_id=None):
    try:
        result = await support.update_ticket(ticket_id, user_id, body=body)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if result is None:
        raise HTTPException(404, "Тикет не найден.")
    recipient, _ = result
    background.add_task(support.notify_ticket, bot, ticket_id, recipient if user_id is None else None)
    return {"ok": True}


@router.get("/admin/api/tickets/{ticket_id}")
async def admin_ticket(ticket_id: TicketId, x_telegram_init_data: WebCredentials):
    await _require_telegram_admin(x_telegram_init_data)
    return await _ticket_detail(ticket_id)


@router.post("/admin/api/tickets/{ticket_id}/replies")
async def admin_reply(ticket_id: TicketId, data: ReplyPayload, background: BackgroundTasks, x_telegram_init_data: WebCredentials):
    await _require_telegram_admin(x_telegram_init_data)
    return await _ticket_reply(ticket_id, data.body, background)


async def _set_ticket_state(ticket_id, closed, background, user_id=None):
    result = await support.update_ticket(ticket_id, user_id, closed=closed)
    if result is None:
        raise HTTPException(404, "Тикет не найден.")
    recipient, changed = result
    if changed:
        background.add_task(support.notify_ticket, bot, ticket_id, recipient if user_id is None else None,
                            event="closed" if closed else "reopened")
    return {"closed": closed}


@router.put("/api/v1/webapp/tickets/{ticket_id}")
async def webapp_ticket_state(ticket_id: TicketId, data: TicketState, background: BackgroundTasks,
                              x_telegram_init_data: WebCredentials):
    identity = await _webapp_user(x_telegram_init_data)
    return await _set_ticket_state(ticket_id, data.closed, background, int(identity["id"]))


@router.put("/admin/api/tickets/{ticket_id}")
async def admin_ticket_state(ticket_id: TicketId, data: TicketState, background: BackgroundTasks,
                             x_telegram_init_data: WebCredentials):
    await _require_telegram_admin(x_telegram_init_data)
    return await _set_ticket_state(ticket_id, data.closed, background)
