"""Browser sign-in using Telegram and linked Google accounts."""
import base64
import hashlib
import json
import secrets
from html import escape
from urllib.parse import urlencode, urlsplit

import httpx
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.bot.menu import redis
from app.config import settings
from app.db.models import GoogleIdentity, User
from app.db.session import SessionLocal
from app.services.bot_identity import bot_username
from app.services.store_auth import issue_store_session, verify_store_session, verify_telegram_login
from app.services.store_operations import get_operations
from app.services.users import BLOCKED_MESSAGE, BlockedCustomer, upsert_active_customer

router = APIRouter()
SESSION = "__Host-remnaum-web"
FLOW = "__Host-remnaum-login"
FLOW_TTL = 600


def app_origin():
    url = urlsplit(settings.webapp_url or settings.public_base_url)
    return f"{url.scheme}://{url.netloc}"


def on_app_host(request):
    return request.url.netloc == urlsplit(app_origin()).netloc


def require_app_host(request):
    if not on_app_host(request) or not app_origin().startswith("https://"):
        raise HTTPException(404, "Вход доступен на HTTPS-домене веб-приложения.")
    if len(settings.app_secret_key) < 32:
        raise HTTPException(503, "Для веб-входа требуется настроить APP_SECRET_KEY.")


def set_cookie(response, name, value, max_age):
    response.set_cookie(name, value, secure=True, httponly=True, samesite="lax", max_age=max_age, path="/")


def session_key(provider):
    if len(settings.app_secret_key) < 32:
        raise ValueError("Browser sign-in requires a strong APP_SECRET_KEY")
    return settings.app_secret_key + ":webapp:" + provider


async def browser_identity(request):
    cookie = request.cookies.get(SESSION, "")
    provider, _, token = cookie.partition(".")
    if provider not in {"telegram", "google"}:
        return None
    require_app_host(request)
    if request.method not in {"GET", "HEAD"} and request.headers.get("origin") != app_origin():
        raise HTTPException(403, "Недопустимый источник запроса.")
    try:
        telegram_id = verify_store_session(token, session_key(provider))
    except ValueError:
        return None
    policy = await get_operations()
    if not policy[provider + "_login"]:
        return None
    async with SessionLocal() as session:
        user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
        if user is None:
            return None
        if user.blocked_at:
            raise HTTPException(403, BLOCKED_MESSAGE)
        return {"id": telegram_id, "username": user.username, "first_name": user.display_name or user.username or "",
                "photo_url": user.telegram_photo_url, "browser_provider": provider}


async def web_credentials(request: Request, x_telegram_init_data: str | None = Header(default=None)):
    return x_telegram_init_data or await browser_identity(request)


def login_response(telegram_id, provider):
    response = RedirectResponse("/", status_code=303)
    set_cookie(response, SESSION, provider + "." + issue_store_session(telegram_id, session_key(provider)), 43200)
    response.delete_cookie(FLOW, secure=True, httponly=True)
    return response


async def save_flow(request, provider, data):
    nonce = request.cookies.get(FLOW) or secrets.token_urlsafe(32)
    state = secrets.token_urlsafe(32)
    await redis.set(f"remnaum:auth:{provider}:{nonce}:{state}", json.dumps(data), ex=FLOW_TTL)
    return nonce, state


async def take_flow(request, provider, state):
    nonce = request.cookies.get(FLOW, "")
    raw = await redis.getdel(f"remnaum:auth:{provider}:{nonce}:{state}") if nonce and len(state) == 43 else None
    if not raw:
        raise HTTPException(400, "Срок входа истёк. Откройте страницу входа заново.")
    return json.loads(raw)


@router.get("/auth", response_class=HTMLResponse)
async def login_page(request: Request):
    require_app_host(request)
    policy = await get_operations()
    options = []
    nonce = None
    if policy["telegram_login"] and bot_username() and settings.telegram_bot_token:
        nonce, state = await save_flow(request, "telegram", {})
        options.append(
            '<script async src="https://telegram.org/js/telegram-widget.js?22" '
            f'data-telegram-login="{escape(bot_username(), quote=True)}" data-size="large" '
            f'data-auth-url="{escape(app_origin(), quote=True)}/auth/telegram/{state}"></script>'
        )
    if policy["google_login"]:
        options.append('<p><a href="/auth/google">Войти через Google</a></p>')
    if not options:
        options.append("<p>Откройте Mini App из Telegram.</p>")
    response = HTMLResponse(
        '<!doctype html><html lang="ru"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<title>Вход</title><link rel="stylesheet" href="/static/theme.css">'
        '<main style="max-width:32rem;margin:12vh auto;padding:1.5rem"><h1>Вход</h1>'
        + "".join(options) + "</main></html>",
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )
    if nonce:
        set_cookie(response, FLOW, nonce, FLOW_TTL)
    return response


@router.get("/auth/telegram/{state}")
async def telegram_browser_login(request: Request, state: str):
    require_app_host(request)
    if not (await get_operations())["telegram_login"]:
        raise HTTPException(403, "Вход через Telegram отключён.")
    await take_flow(request, "telegram", state)
    try:
        identity = verify_telegram_login(dict(request.query_params), settings.telegram_bot_token)
        async with SessionLocal.begin() as session:
            await upsert_active_customer(session, identity["id"], identity["username"])
    except BlockedCustomer as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(401, str(exc)) from exc
    return login_response(identity["id"], "telegram")


@router.get("/auth/google")
async def google_login_start(request: Request, link: bool = False):
    require_app_host(request)
    policy = await get_operations()
    if not policy["google_login"]:
        raise HTTPException(403, "Вход через Google отключён.")
    telegram_id = None
    if link:
        identity = await browser_identity(request)
        if not identity or identity["browser_provider"] != "telegram":
            raise HTTPException(403, "Для привязки Google войдите через Telegram.")
        telegram_id = identity["id"]
    verifier = secrets.token_urlsafe(48)
    nonce, state = await save_flow(request, "google", {
        "verifier": verifier, "telegram_id": telegram_id, "client_id": policy["google_client_id"],
    })
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    response = RedirectResponse("https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
        "client_id": policy["google_client_id"], "redirect_uri": app_origin() + "/auth/google/callback",
        "response_type": "code", "scope": "openid", "state": state, "prompt": "select_account",
        "code_challenge": challenge, "code_challenge_method": "S256",
    }))
    set_cookie(response, FLOW, nonce, FLOW_TTL)
    return response


@router.get("/auth/google/callback")
async def google_login_finish(request: Request, state: str, code: str = ""):
    require_app_host(request)
    flow = await take_flow(request, "google", state)
    policy = await get_operations(credentials=True)
    if not policy["google_login"] or policy["google_client_id"] != flow["client_id"] or not code:
        raise HTTPException(403, "Вход через Google недоступен. Начните заново.")
    telegram_id = flow["telegram_id"]
    if telegram_id is not None:
        identity = await browser_identity(request)
        if not identity or identity["browser_provider"] != "telegram" or identity["id"] != telegram_id:
            raise HTTPException(403, "Сессия Telegram изменилась. Повторите привязку.")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            token = await client.post("https://oauth2.googleapis.com/token", data={
                "code": code, "grant_type": "authorization_code",
                "client_id": flow["client_id"], "client_secret": policy["google_client_secret"],
                "redirect_uri": app_origin() + "/auth/google/callback", "code_verifier": flow["verifier"],
            })
            token.raise_for_status()
            info = await client.get("https://openidconnect.googleapis.com/v1/userinfo", headers={
                "Authorization": "Bearer " + token.json()["access_token"],
            })
            info.raise_for_status()
            subject = info.json()["sub"]
            if not isinstance(subject, str) or not 1 <= len(subject) <= 255:
                raise ValueError("Invalid Google subject")
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        raise HTTPException(502, "Google не подтвердил вход. Повторите попытку.") from exc
    try:
        async with SessionLocal.begin() as session:
            if telegram_id is not None:
                user = await session.scalar(select(User).where(User.telegram_id == telegram_id).with_for_update())
                if user is None or user.blocked_at:
                    raise HTTPException(403, BLOCKED_MESSAGE)
                existing = await session.get(GoogleIdentity, subject)
                if existing is None:
                    session.add(GoogleIdentity(subject=subject, user_id=user.id))
                elif existing.user_id != user.id:
                    raise HTTPException(409, "Этот аккаунт Google уже привязан.")
            else:
                user = await session.scalar(select(User).join(GoogleIdentity).where(GoogleIdentity.subject == subject))
                if user is None:
                    raise HTTPException(403, "Сначала войдите через Telegram и привяжите Google в профиле.")
                if user.blocked_at:
                    raise HTTPException(403, BLOCKED_MESSAGE)
                telegram_id = user.telegram_id
    except IntegrityError as exc:
        raise HTTPException(409, "К этому профилю уже привязан аккаунт Google.") from exc
    return login_response(telegram_id, "telegram" if flow["telegram_id"] is not None else "google")


@router.post("/auth/logout")
async def logout(request: Request):
    require_app_host(request)
    if request.headers.get("origin") != app_origin():
        raise HTTPException(403, "Недопустимый источник запроса.")
    response = RedirectResponse("/auth", status_code=303)
    response.delete_cookie(SESSION, secure=True, httponly=True)
    return response
