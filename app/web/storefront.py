"""Independent browser storefront. Telegram Login Widget auth, non-Telegram checkout.

All digital sales inside Telegram continue to use Telegram Stars.
External provider redirects are only offered on this separately opened website.
"""
from __future__ import annotations
from html import escape
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.dialects.postgresql import insert

from app.config import settings
from app.db.models import User
from app.db.session import SessionLocal
from app.services.payment_service import (
    PaymentProviderUnavailable, TermsAcceptanceRequired, accept_current_terms, create_payment_for_user,
)
from app.services.plans import list_plans
from app.services.store_auth import issue_store_session, verify_store_session, verify_telegram_login
from app.services.store_profile import get_store_profile, get_terms_url
from app.services.store_settings import get_store_payment_settings, public_payment_settings

router = APIRouter()
COOKIE_NAME = "__Host-remnaum-session"
EXTERNAL_PROVIDERS = {"yookassa", "yoomoney", "cryptopay"}


def _require_store_host(request: Request) -> None:
    expected = urlsplit(settings.public_base_url).hostname
    if not expected or request.url.hostname != expected:
        raise HTTPException(status_code=404, detail="Storefront not available on this host")


def _store_user(request: Request) -> int:
    _require_store_host(request)
    try:
        return verify_store_session(request.cookies.get(COOKIE_NAME), settings.app_secret_key)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Войдите через Telegram на сайте магазина") from exc


@router.get("/store", response_class=HTMLResponse)
async def store_page(request: Request):
    _require_store_host(request)
    username = settings.telegram_bot_username.lstrip("@")
    auth_url = settings.public_base_url.rstrip("/") + "/store/auth"
    if not username:
        widget = "<p>Вход через Telegram ещё не настроен владельцем магазина.</p>"
    else:
        widget = ('<script async src="https://telegram.org/js/telegram-widget.js?22" '
                  'data-telegram-login="' + escape(username, quote=True) + '" '
                  'data-size="large" data-radius="12" data-auth-url="' +
                  escape(auth_url, quote=True) + '" data-request-access="write"></script>')
    html = (Path(__file__).parent / "templates" / "store.html").read_text(encoding="utf-8")
    return HTMLResponse(html.replace("__LOGIN_WIDGET__", widget))


@router.get("/store/auth")
async def store_auth(request: Request):
    _require_store_host(request)
    values = dict(request.query_params)
    try:
        identity = verify_telegram_login(values, settings.telegram_bot_token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    async with SessionLocal() as session:
        stmt = insert(User).values(telegram_id=identity["id"], username=identity["username"])
        stmt = stmt.on_conflict_do_update(
            index_elements=[User.telegram_id], set_={"username": identity["username"]},
        )
        await session.execute(stmt)
        await session.commit()
    response = RedirectResponse("/store", status_code=303)
    response.set_cookie(
        COOKIE_NAME, issue_store_session(identity["id"], settings.app_secret_key),
        max_age=43200, secure=True, httponly=True, samesite="lax", path="/",
    )
    return response


@router.get("/api/v1/store/catalog")
async def store_catalog(request: Request):
    _require_store_host(request)
    methods, credentials = await get_store_payment_settings()
    configured = public_payment_settings(methods, credentials)["configured"]
    return {
        "profile": await get_store_profile(),
        "terms_url": await get_terms_url(),
        "plans": await list_plans(active_only=True),
        "methods": [name for name in ("yookassa", "yoomoney", "cryptopay") if methods.get(name) and configured.get(name)],
    }


@router.get("/api/v1/store/me")
async def store_me(request: Request):
    telegram_id = _store_user(request)
    async with SessionLocal() as session:
        from sqlalchemy import select
        user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
    if user is None:
        raise HTTPException(status_code=401, detail="Account not registered")
    return {"id": telegram_id, "first_name": user.username or ""}


class StoreCheckoutPayload(BaseModel):
    plan_id: int = Field(ge=1)
    provider: str = Field(min_length=1, max_length=32)
    accepted_terms: bool


@router.post("/api/v1/store/checkout")
async def store_checkout(request: Request, data: StoreCheckoutPayload):
    telegram_id = _store_user(request)
    if request.headers.get("origin") != settings.public_base_url.rstrip("/"):
        raise HTTPException(status_code=403, detail="Invalid checkout origin")
    if data.provider not in EXTERNAL_PROVIDERS:
        raise HTTPException(status_code=422, detail="Unsupported payment provider")
    if await get_terms_url() and not data.accepted_terms:
        raise HTTPException(status_code=403, detail="Перед оплатой примите условия продажи.")
    methods, _ = await get_store_payment_settings()
    if not methods.get(data.provider):
        raise HTTPException(status_code=403, detail="Payment method is disabled")
    try:
        if data.accepted_terms:
            await accept_current_terms(telegram_id)
        payment, link = await create_payment_for_user(telegram_id, data.provider, plan_id=data.plan_id)
    except TermsAcceptanceRequired as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except PaymentProviderUnavailable as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not link.url.startswith("https://"):
        raise HTTPException(status_code=502, detail="Unsafe payment redirect")
    return {"payment_id": payment.id, "url": link.url}
