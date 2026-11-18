"""Independent browser storefront. Telegram Login Widget auth, non-Telegram checkout.

All digital sales inside Telegram continue to use Telegram Stars.
External provider redirects are only offered on this separately opened website.
"""
from __future__ import annotations
from html import escape
from urllib.parse import urlsplit

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.dialects.postgresql import insert

from app.config import settings
from app.db.models import User
from app.db.session import SessionLocal
from app.services.payment_service import accept_current_terms, create_payment_for_user
from app.services.plans import choose_plan, list_plans
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
    html = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>VPN-магазин</title><meta name="color-scheme" content="dark"><style>
:root{--accent:#558cff;color-scheme:dark;font-family:system-ui,-apple-system,sans-serif;background:#080e19;color:#f0f5ff}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(ellipse at top right,#233a5c 0,#080e19 48%);min-height:100vh}
main{max-width:1050px;margin:auto;padding:26px 18px 85px}
header{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:58px}
.brand{font-size:23px;font-weight:800;letter-spacing:-.8px}.pill{border:1px solid #344257;border-radius:99px;padding:8px 12px;color:#aebfd4;font-size:12px}
h1{font-size:clamp(34px,6vw,58px);line-height:1.05;letter-spacing:-2px;max-width:780px;margin:0 0 20px}
.lead{font-size:18px;max-width:620px;line-height:1.5;color:#a8bad1;margin-bottom:36px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(225px,1fr));gap:16px;margin:22px 0}
.card{padding:24px;border-radius:20px;background:#111c2d;border:1px solid #2c405b;box-shadow:0 10px 38px #0003}
.card h3{margin:0 0 10px;font-size:22px}.small{color:#9daec4;font-size:14px;line-height:1.7}
.price{font-size:30px;font-weight:800;margin:18px 0}
button{cursor:pointer;background:var(--accent);color:#fff;padding:13px 18px;border:0;border-radius:11px;font-weight:700;font-size:14px}
button:disabled{opacity:.5;cursor:not-allowed}
select{background:#091323;color:#fff;border:1px solid #445a79;padding:13px;border-radius:11px;max-width:330px;width:100%}
section{margin:36px 0}a{color:#99c5ff}#account{display:none}
#status{color:#c9d7e8;margin:14px 0}.login{padding:20px;border:1px solid #324760;background:#121f31;border-radius:17px}
footer{margin-top:60px;font-size:13px;color:#8b9db3}
</style></head><body><main>
<header><div class="brand" id="name">Remnaum</div><span class="pill">Безопасный VPN · Remnawave</span></header>
<h1 id="tagline">VPN, который вы выбираете сами.</h1>
<p class="lead" id="description">Управляйте подпиской и подключайте устройства без сложных настроек.</p>
<section><h2>Выберите тариф</h2><div id="plans" class="cards"></div></section>
<section class="login" id="login"><h2>Вход для оформления заказа</h2>
<p class="small">Для покупки через самостоятельный сайт подтвердите аккаунт Telegram. В Mini App оплата цифровых подписок доступна через Telegram Stars.</p>
__LOGIN_WIDGET__</section>
<section id="account" class="login"><h2 id="hello">Вы вошли</h2>
<p class="small">Оплата проходит на защищённой странице выбранного провайдера. Ключи платёжных систем не передаются браузеру.</p>
<label><input type="checkbox" id="terms"> Принимаю <a id="terms-url" href="#" target="_blank" rel="noopener">условия продажи</a></label>
<p>Способ оплаты</p><select id="provider"></select><br><br>
<button id="buy" disabled>Перейти к оплате</button><p id="status" role="status"></p>
</section>
<footer>Покупка через самостоятельный веб-магазин. Если вы открыли Mini App непосредственно в Telegram, используйте Telegram Stars.</footer>
</main><script>
const $=id=>document.getElementById(id);let selected=null;let store=null;let me=null;
async function request(path,options){const r=await fetch(path,options);const data=await r.json();if(!r.ok)throw Error(data.detail||"Ошибка");return data}
async function init(){
try{
 store=await request("/api/v1/store/catalog");
 $("name").textContent=store.profile.name;$("tagline").textContent=store.profile.tagline||"Выберите тариф";
 $("description").textContent=store.profile.description;
 document.documentElement.style.setProperty("--accent",store.profile.accent_color);
 if(store.terms_url){$("terms-url").href=store.terms_url}else{$("terms-url").textContent="условия не настроены";$("terms-url").removeAttribute("href")}
 const list=$("plans");list.replaceChildren();
 if(!store.plans.length){list.textContent="Тарифы пока не опубликованы."}
 for(const p of store.plans){
   const card=document.createElement("article");card.className="card";
   const name=document.createElement("h3");name.textContent=p.title;
   const desc=document.createElement("p");desc.className="small";desc.textContent=p.description||"Выберите подходящую подписку";
   const spec=document.createElement("p");spec.className="small";spec.textContent=p.duration_days+" дней · "+p.traffic_gb+" ГБ · "+p.device_limit+" устройств";
   const price=document.createElement("div");price.className="price";price.textContent=(p.price_rub_minor/100).toFixed(2)+" ₽";
   const choose=document.createElement("button");choose.textContent="Выбрать тариф";
   choose.onclick=()=>{selected=p;document.querySelectorAll(".card button").forEach(b=>b.textContent="Выбрать тариф");choose.textContent="✓ Выбрано";$("buy").disabled=!me||!store.terms_url||!store.methods.length;$("account").scrollIntoView({behavior:"smooth"})};
   card.append(name,desc,spec,price,choose);list.append(card);
 }
 const provider=$("provider");provider.replaceChildren();
 store.methods.forEach(p=>{const el=document.createElement("option");el.value=p;el.textContent=({"yookassa":"ЮKassa","yoomoney":"ЮMoney","cryptopay":"Crypto Pay"})[p]||p;provider.append(el)});
 try{me=await request("/api/v1/store/me");$("login").style.display="none";$("account").style.display="block";$("hello").textContent="Здравствуйте, "+(me.first_name||"покупатель")+"!"}catch(e){$("login").style.display="block"}
 if(!store.methods.length){$("status").textContent="Внешние способы оплаты пока не настроены владельцем магазина."}
}catch(e){$("plans").textContent=e.message}}
$("buy").onclick=async()=>{try{
 if(!selected||!me)throw Error("Сначала войдите в аккаунт и выберите тариф");
 if(!$("terms").checked||!store.terms_url)throw Error("Прочитайте и примите условия продажи");
 $("buy").disabled=true;$("status").textContent="Создаём платёж…";
 const response=await request("/api/v1/store/checkout",{method:"POST",headers:{"Content-Type":"application/json"},
   body:JSON.stringify({plan_id:selected.id,provider:$("provider").value,accepted_terms:true})});
 if(!response.url.startsWith("https://"))throw Error("Платёжный провайдер вернул неправильную ссылку");
 window.location.assign(response.url);
}catch(e){$("status").textContent=e.message;$("buy").disabled=false}};
init();
</script></body></html>"""
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
    if not data.accepted_terms or not await get_terms_url():
        raise HTTPException(status_code=403, detail="Please accept published terms")
    methods, _ = await get_store_payment_settings()
    if not methods.get(data.provider):
        raise HTTPException(status_code=403, detail="Payment method is disabled")
    try:
        await choose_plan(telegram_id, data.plan_id)
        await accept_current_terms(telegram_id)
        payment, link = await create_payment_for_user(telegram_id, data.provider)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not link.url.startswith("https://"):
        raise HTTPException(status_code=502, detail="Unsafe payment redirect")
    return {"payment_id": payment.id, "url": link.url}
