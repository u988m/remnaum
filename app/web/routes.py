from html import escape

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.bot.runtime import bot, dispatcher
from app.config import settings
from app.db.models import Payment, Subscription, User
from app.db.session import SessionLocal
from app.services.payment_providers import get_provider
from app.services.payment_service import settle_payment
from app.services.subscriptions import provision_paid_payment
from app.services.telegram_webapp import validate_telegram_init_data

router = APIRouter()

@router.get("/admin/payments", response_class=HTMLResponse)
async def admin_payments_page():
    return HTMLResponse("""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Remnaum — платежи</title><style>
body{font:16px system-ui;background:#10131a;color:#f4f6fb;margin:0;padding:24px}
main{max-width:760px;margin:auto}section{background:#1a202b;border:1px solid #303949;border-radius:14px;padding:18px;margin:14px 0}
label{display:block;margin:10px 0}input[type=text],input[type=password]{box-sizing:border-box;width:100%;padding:11px;background:#0e131b;color:#fff;border:1px solid #465267;border-radius:8px}
button{padding:11px 16px;border:0;border-radius:8px;background:#dbeafe;color:#111827;font-weight:700}
small{color:#aab5c7}.row{display:flex;justify-content:space-between;align-items:center;gap:12px}
</style></head><body><main><h1>Платёжные системы</h1>
<p>Настройки магазина Remnaum. Секреты сохраняются в зашифрованном виде и не отображаются повторно.</p>
<label>Ключ администратора<input id="token" type="password" autocomplete="current-password"></label>
<section><div class="row"><b>Telegram Stars</b><input id="telegram_stars" type="checkbox" checked></div><small>Оплата в Telegram. Включена по умолчанию.</small></section>
<section><div class="row"><b>ЮKassa</b><input id="yookassa" type="checkbox"></div><label>Shop ID<input id="yookassa_shop_id" type="text"></label><label>Secret key<input id="yookassa_secret_key" type="password"></label><small id="yookassa_status"></small></section>
<section><div class="row"><b>ЮMoney</b><input id="yoomoney" type="checkbox"></div><label>Номер кошелька<input id="yoomoney_receiver" type="text"></label><label>Секрет уведомлений<input id="yoomoney_notification_secret" type="password"></label><small id="yoomoney_status"></small></section>
<section><div class="row"><b>Crypto Pay</b><input id="cryptopay" type="checkbox"></div><label>API token<input id="cryptopay_api_token" type="password"></label><small id="cryptopay_status"></small></section>
<button id="load">Загрузить настройки</button> <button id="save">Сохранить</button><p id="status"></p>
<script>
const ids=["telegram_stars","yookassa","yoomoney","cryptopay"];
const token=()=>document.getElementById("token").value;
async function api(method,body){const r=await fetch("/admin/api/payment-settings",{method,headers:{"X-Admin-Token":token(),"Content-Type":"application/json"},body:body?JSON.stringify(body):undefined});const d=await r.json();if(!r.ok)throw Error(d.detail||"Ошибка запроса");return d}
document.getElementById("load").onclick=async()=>{try{const d=await api("GET");ids.forEach(k=>document.getElementById(k).checked=!!d.methods[k]);Object.keys(d.configured).forEach(k=>{const el=document.getElementById(k+"_status");if(el)el.textContent=d.configured[k]?"Реквизиты сохранены":"Реквизиты не настроены"});document.getElementById("status").textContent="Настройки загружены"}catch(e){document.getElementById("status").textContent=e.message}};
document.getElementById("save").onclick=async()=>{try{const methods={};ids.forEach(k=>methods[k]=document.getElementById(k).checked);const credentials={yookassa:{shop_id:document.getElementById("yookassa_shop_id").value,secret_key:document.getElementById("yookassa_secret_key").value},yoomoney:{receiver:document.getElementById("yoomoney_receiver").value,notification_secret:document.getElementById("yoomoney_notification_secret").value},cryptopay:{api_token:document.getElementById("cryptopay_api_token").value}};const d=await api("PUT",{methods,credentials});Object.keys(d.configured).forEach(k=>{const el=document.getElementById(k+"_status");if(el)el.textContent=d.configured[k]?"Реквизиты сохранены":"Реквизиты не настроены"});["yookassa_secret_key","yoomoney_notification_secret","cryptopay_api_token"].forEach(k=>document.getElementById(k).value="");document.getElementById("status").textContent="Настройки сохранены"}catch(e){document.getElementById("status").textContent=e.message}};
</script></main></body></html>""")

import secrets
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



from app.services.tariffs import get_store_tariff_pricing, update_store_tariff_pricing, set_tariff_draft

class TariffPricingUpdate(BaseModel):
    pricing: dict[str, int | str] = Field(default_factory=dict)


@router.get("/admin/tariffs")
async def admin_tariffs_page():
    return HTMLResponse("""<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Remnaum — тарифы</title><body style="font:16px system-ui;max-width:760px;margin:32px auto;padding:0 16px;background:#10131a;color:#f4f6fb"><h1>Цены тарифов</h1><p>Настройте формулу стоимости отдельно для рублей и Telegram Stars.</p><label>Ключ администратора <input id="token" type="password"></label><div id="fields"></div><button id="load">Загрузить</button> <button id="save">Сохранить</button><p id="status"></p><script>
const keys=["rub_per_day_minor","rub_per_gb_minor","rub_per_device_minor","stars_per_day","stars_per_gb","stars_per_device","currency"];
const labels={rub_per_day_minor:"Рубли: за день (копейки)",rub_per_gb_minor:"Рубли: за 1 ГБ (копейки)",rub_per_device_minor:"Рубли: за устройство (копейки)",stars_per_day:"Stars: за день",stars_per_gb:"Stars: за 1 ГБ",stars_per_device:"Stars: за устройство",currency:"Валюта внешнего магазина"};
const fields=document.getElementById("fields");keys.forEach(k=>{const l=document.createElement("label");l.style.display="block";l.style.margin="12px 0";l.textContent=labels[k];const i=document.createElement("input");i.id=k;i.type=k==="currency"?"text":"number";i.min="0";i.style.display="block";i.style.padding="9px";l.appendChild(i);fields.appendChild(l)});
async function api(method,body){const r=await fetch("/admin/api/tariff-pricing",{method,headers:{"X-Admin-Token":document.getElementById("token").value,"Content-Type":"application/json"},body:body?JSON.stringify(body):undefined});const d=await r.json();if(!r.ok)throw Error(d.detail||"Ошибка запроса");return d}
function fill(d){keys.forEach(k=>document.getElementById(k).value=d[k])}
document.getElementById("load").onclick=async()=>{try{fill(await api("GET"));document.getElementById("status").textContent="Цены загружены"}catch(e){document.getElementById("status").textContent=e.message}};
document.getElementById("save").onclick=async()=>{try{const pricing={};keys.forEach(k=>pricing[k]=k==="currency"?document.getElementById(k).value:Number(document.getElementById(k).value));fill(await api("PUT",{pricing}));document.getElementById("status").textContent="Цены сохранены"}catch(e){document.getElementById("status").textContent=e.message}};
</script></body></html>""")


@router.get("/admin/api/tariff-pricing")
async def admin_get_tariff_pricing(x_admin_token: str | None = Header(default=None)):
    _require_admin_token(x_admin_token)
    return await get_store_tariff_pricing()


@router.put("/admin/api/tariff-pricing")
async def admin_update_tariff_pricing(data: TariffPricingUpdate, x_admin_token: str | None = Header(default=None)):
    _require_admin_token(x_admin_token)
    try:
        return await update_store_tariff_pricing(data.pricing)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


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
        verified = await (await _configured_provider("yoomoney")).verify_webhook(body, dict(request.headers))
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
        verified = await (await _configured_provider("cryptopay")).verify_webhook(body, dict(request.headers))
        await settle_payment(verified, "cryptopay")
        await provision_paid_payment(verified.local_payment_id)
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
    return HTMLResponse("""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="dark"><title>Remnaum</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
:root{color-scheme:dark;font-family:Inter,system-ui,-apple-system,sans-serif;background:var(--tg-theme-bg-color,#0b1018);color:var(--tg-theme-text-color,#f5f7fb)}
*{box-sizing:border-box}body{margin:0;background:var(--tg-theme-bg-color,#0b1018);color:var(--tg-theme-text-color,#f5f7fb)}
main{max-width:620px;margin:auto;padding:22px 16px calc(28px + env(safe-area-inset-bottom))}
header{display:flex;align-items:center;justify-content:space-between;margin-bottom:26px}
.brand{font-size:22px;font-weight:800;letter-spacing:-.7px}.tag{font-size:11px;color:var(--tg-theme-hint-color,#91a0b5);border:1px solid var(--tg-theme-section-separator-color,#293548);padding:6px 9px;border-radius:20px}
h1{font-size:27px;letter-spacing:-.8px;margin:0 0 8px}.muted{color:var(--tg-theme-hint-color,#98a5b7);line-height:1.5}
.panel{background:var(--tg-theme-secondary-bg-color,#151d29);border:1px solid var(--tg-theme-section-separator-color,#293548);border-radius:18px;padding:18px;margin-top:16px}
.panel h2{font-size:17px;margin:0 0 14px}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
.metric{background:var(--tg-theme-bg-color,#0b1018);border-radius:12px;padding:12px 8px;text-align:center}.metric b{display:block;font-size:18px}.metric span{font-size:11px;color:var(--tg-theme-hint-color,#98a5b7)}
.row{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:10px 0}
select{width:100%;padding:13px;border-radius:11px;border:1px solid var(--tg-theme-section-separator-color,#334155);background:var(--tg-theme-bg-color,#0b1018);color:inherit;font:inherit}
.price{font-size:25px;font-weight:800;letter-spacing:-.6px}.subprice{font-size:13px;color:var(--tg-theme-hint-color,#98a5b7);margin-top:4px}
button{width:100%;padding:15px;border:0;border-radius:13px;background:var(--tg-theme-button-color,#4b8bea);color:var(--tg-theme-button-text-color,#fff);font-size:15px;font-weight:750;margin-top:14px}
button:disabled{opacity:.45}a{color:var(--tg-theme-link-color,#65a9ff)}.status{font-size:13px;line-height:1.5;margin-top:12px;min-height:20px}.check{display:flex;align-items:flex-start;gap:9px;font-size:13px;color:var(--tg-theme-hint-color,#98a5b7);line-height:1.5;margin-top:14px}
.check input{margin-top:3px}#app{display:none}.loading{text-align:center;padding:60px 20px;color:var(--tg-theme-hint-color,#98a5b7)}
</style></head><body><main><div id="loading" class="loading">Загружаем ваш магазин…</div><div id="app">
<header><div class="brand">Remnaum<span style="color:var(--tg-theme-button-color,#4b8bea)">.</span></div><div class="tag">VPN STORE</div></header>
<h1>Ваш VPN — ваши правила</h1><div class="muted" id="welcome">Выберите параметры подписки и получите доступ за пару секунд.</div>
<section class="panel" id="subscription" style="display:none"><h2>Ваша подписка</h2><div class="row"><span class="muted">Статус</span><b id="sub-status">—</b></div><div class="row"><span class="muted">Действует до</span><b id="sub-expiry">—</b></div><div class="grid"><div class="metric"><b id="sub-traffic">—</b><span>ГБ</span></div><div class="metric"><b id="sub-devices">—</b><span>устройств</span></div><div class="metric"><b id="sub-days">—</b><span>дней осталось</span></div></div><a id="sub-link" href="#" target="_blank" style="display:none">Открыть ссылку подключения</a></section>
<section class="panel"><h2>Соберите свой тариф</h2><div class="row"><label for="days">Срок подписки</label><select id="days"></select></div><div class="row"><label for="traffic">Трафик</label><select id="traffic"></select></div><div class="row"><label for="devices">Устройства</label><select id="devices"></select></div>
<div style="margin-top:16px"><div class="muted">Стоимость в Telegram</div><div class="price" id="stars-price">—</div></div>
<label class="check"><input id="terms" type="checkbox"><span>Я принимаю <a id="terms-link" href="#" target="_blank">условия оказания услуги</a>.</span></label>
<button id="buy" disabled>Оплатить Telegram Stars</button><div id="status" class="status"></div></section>
<div class="muted" style="font-size:12px;text-align:center;margin-top:20px">Безопасная оплата через Telegram Stars · Remnaum</div>
</div></main>
<script>
const tg=window.Telegram?.WebApp;tg?.ready();tg?.expand();
let data=null;
const $=id=>document.getElementById(id);
function api(path,body){return fetch(path,{method:body?"POST":"GET",headers:{"Content-Type":"application/json","X-Telegram-Init-Data":tg?.initData||""},body:body?JSON.stringify(body):undefined}).then(async r=>{const d=await r.json();if(!r.ok)throw Error(d.detail||"Ошибка запроса");return d})}
function fillOptions(id,min,max,step,selected,label){const el=$(id);el.replaceChildren();const chosen=Math.min(max,Math.max(min,selected));const values=[];for(let n=min;n<=max;n+=step)values.push(n);if(!values.includes(chosen))values.push(chosen);values.sort((a,b)=>a-b);values.forEach(n=>{const o=document.createElement("option");o.value=String(n);o.textContent=n+" "+label;el.appendChild(o)});el.value=String(chosen)}
function limits(){return {days:+$("days").value,traffic:+$("traffic").value,devices:+$("devices").value}}
function recalc(){if(!data)return;const v=limits(),p=data.pricing;const stars=v.days*p.stars_per_day+v.traffic*p.stars_per_gb+v.devices*p.stars_per_device;$("stars-price").textContent=stars+" ⭐";$("buy").textContent="Оплатить "+stars+" Stars";$("buy").disabled=!$("terms").checked||!data.stars_enabled||!data.terms_url||stars<1}
["days","traffic","devices"].forEach(k=>$(k).addEventListener("change",recalc));$("terms").addEventListener("change",recalc);
$("buy").onclick=async()=>{try{$("buy").disabled=true;$("status").textContent="Создаём счёт…";await api("/api/v1/webapp/terms/accept",{accepted:true});const result=await api("/api/v1/webapp/checkout",limits());if(!tg?.openInvoice)throw Error("Откройте магазин из Telegram, чтобы оплатить счёт");tg.openInvoice(result.invoice_url,(status)=>{$("status").textContent=status==="paid"?"Оплата подтверждена. Обновляем подписку…":status==="cancelled"?"Оплата отменена":"Статус оплаты: "+status;if(status==="paid")setTimeout(()=>location.reload(),1800);else recalc()})}catch(e){$("status").textContent=e.message;recalc()}};
async function start(){try{if(!tg?.initData)throw Error("Откройте Mini App через кнопку в Telegram");data=await api("/api/v1/webapp/bootstrap");$("loading").style.display="none";$("app").style.display="block";$("welcome").textContent=data.user.first_name?"Здравствуйте, "+data.user.first_name+"! Настройте подписку под себя.":"Выберите параметры подписки.";if(data.terms_url){$("terms-link").href=data.terms_url}else{$("terms-link").textContent="условия не настроены";$("terms-link").removeAttribute("href");$("buy").disabled=true}
fillOptions("days",data.limits.days_min,data.limits.days_max,1,data.limits.days_default,"дней");fillOptions("traffic",data.limits.traffic_min,data.limits.traffic_max,1,data.limits.traffic_default,"ГБ");fillOptions("devices",data.limits.devices_min,data.limits.devices_max,1,data.limits.devices_default,"устройств");if(data.subscription){const s=data.subscription;$("subscription").style.display="block";$("sub-status").textContent=s.status;$("sub-expiry").textContent=s.expires_at?new Date(s.expires_at).toLocaleString():"—";$("sub-traffic").textContent=s.traffic_gb??"—";$("sub-devices").textContent=s.device_limit??"—";$("sub-days").textContent=s.days_left??"—";if(s.subscription_url){$("sub-link").href=s.subscription_url;$("sub-link").style.display="inline-block"}}
$("buy").disabled=!data.stars_enabled;recalc()}catch(e){$("loading").textContent=e.message}}
start();
</script></body></html>""")


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
        "pricing": {key: value for key, value in pricing.items() if key.startswith("stars_")},
        "stars_enabled": await is_payment_method_enabled("telegram_stars"),
        "terms_url": settings.telegram_terms_url,
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
            title="Remnaum VPN",
            description=f"VPN-подписка: {data.days} дней, {data.traffic} ГБ, {data.devices} устройств",
            payload=f"remnaum:{payment.id}",
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice(label="Remnaum VPN subscription", amount=stars)],
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
    return HTMLResponse("""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="dark"><title>Remnaum — панель управления</title>
<style>
:root{font-family:Inter,system-ui,-apple-system,sans-serif;color-scheme:dark;background:#0b1018;color:#f4f7fc}
*{box-sizing:border-box}body{margin:0;background:#0b1018;color:#f4f7fc}main{max-width:1100px;margin:auto;padding:24px 16px 48px}
header{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:26px}.brand{font-size:23px;font-weight:850;letter-spacing:-.8px}
.tag{font-size:11px;color:#9eacc0;border:1px solid #293548;padding:6px 10px;border-radius:20px}
h1{font-size:27px;margin:0 0 7px;letter-spacing:-.8px}.muted{color:#9eacc0;line-height:1.5}
.cards{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:20px 0}
.card,.section{background:#151d29;border:1px solid #293548;border-radius:16px;padding:17px}
.card span{display:block;color:#9eacc0;font-size:12px}.card b{display:block;font-size:26px;margin-top:7px}
nav{display:flex;gap:8px;margin:22px 0 12px;flex-wrap:wrap}
nav button,.action{border:1px solid #34445c;background:#182334;color:#e9f0fa;border-radius:10px;padding:10px 14px;font-weight:650}
nav button.active,.primary{background:#3978d4;border-color:#3978d4;color:white}
.section{margin-top:12px;overflow:hidden}.section h2{font-size:17px;margin:0 0 14px}
.toolbar{display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:12px}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{text-align:left;padding:12px 9px;border-bottom:1px solid #293548;white-space:nowrap}
th{color:#9eacc0;font-weight:600}td{max-width:250px;overflow:hidden;text-overflow:ellipsis}
.table-wrap{overflow-x:auto}.status{font-size:13px;min-height:22px;margin-top:12px;color:#9eacc0}
.pill{display:inline-block;padding:4px 8px;border-radius:20px;background:#24354a;color:#d7e6fb;font-size:11px}
.empty{padding:25px;text-align:center;color:#9eacc0}
@media(max-width:700px){.cards{grid-template-columns:repeat(2,minmax(0,1fr))}.card b{font-size:22px}main{padding:18px 12px 36px}header{margin-bottom:20px}h1{font-size:23px}th,td{padding:10px 7px}}
</style></head><body><main>
<header><div class="brand">Remnaum<span style="color:#5b9cff">.</span></div><div class="tag">ADMIN PANEL</div></header>
<div id="loading" class="section">Проверяем доступ администратора…</div>
<div id="dashboard" hidden>
<h1>Панель управления</h1><div class="muted" id="welcome">Обзор вашего магазина</div>
<div class="cards">
<div class="card"><span>Всего клиентов</span><b id="users-count">—</b></div>
<div class="card"><span>Активные подписки</span><b id="active-count">—</b></div>
<div class="card"><span>Успешные платежи</span><b id="paid-count">—</b></div>
<div class="card"><span>Ожидают оплаты</span><b id="pending-count">—</b></div>
</div>
<nav><button class="active" data-tab="users">Клиенты</button><button data-tab="payments">Платежи</button><button data-tab="settings" onclick="location.href='/admin/payments'">Оплата</button><button data-tab="tariffs" onclick="location.href='/admin/tariffs'">Тарифы</button></nav>
<section class="section" id="users-view"><div class="toolbar"><h2>Клиенты и подписки</h2><button class="action" id="users-refresh">Обновить</button></div><div class="table-wrap"><table><thead><tr><th>Telegram ID</th><th>Username</th><th>Подписка</th><th>До</th><th>Регистрация</th></tr></thead><tbody id="users-table"></tbody></table></div><div id="customer-detail" class="section" hidden aria-live="polite"><div class="toolbar"><h2 id="customer-title">Карточка клиента</h2><button class="action" id="customer-close">Закрыть</button></div><p class="muted" id="customer-summary"></p><h3>Подписки</h3><div class="table-wrap"><table><thead><tr><th>ID</th><th>Статус</th><th>Действует до</th><th>Создана</th></tr></thead><tbody id="customer-subscriptions"></tbody></table></div><h3>Платежи</h3><div class="table-wrap"><table><thead><tr><th>ID</th><th>Провайдер</th><th>Сумма</th><th>Статус</th><th>Дата</th></tr></thead><tbody id="customer-payments"></tbody></table></div></div><div class="toolbar" style="margin-top:12px"><button class="action" id="users-prev">Назад</button><span id="users-page" class="muted">Страница 1</span><button class="action" id="users-next">Далее</button></div></section>
<section class="section" id="payments-view" hidden><div class="toolbar"><h2>История платежей</h2><button class="action" id="payments-refresh">Обновить</button></div><div class="table-wrap"><table><thead><tr><th>ID</th><th>Клиент</th><th>Провайдер</th><th>Сумма</th><th>Статус</th><th>Дата</th></tr></thead><tbody id="payments-table"></tbody></table></div><div class="toolbar" style="margin-top:12px"><button class="action" id="payments-prev">Назад</button><span id="payments-page" class="muted">Страница 1</span><button class="action" id="payments-next">Далее</button></div></section>
<div id="status" class="status"></div>
</div></main>
<script>
const tg=window.Telegram?.WebApp;tg?.ready();tg?.expand();const $=id=>document.getElementById(id);let userOffset=0,paymentOffset=0;const pageSize=20;
async function api(path){const r=await fetch(path,{headers:{"X-Telegram-Init-Data":tg?.initData||""}});const d=await r.json();if(!r.ok)throw Error(d.detail||"Ошибка запроса");return d}
function cell(value){const td=document.createElement("td");td.textContent=value??"—";return td}
function row(target,values){const tr=document.createElement("tr");values.forEach(v=>tr.appendChild(cell(v)));$(target).appendChild(tr)}
async function overview(){const d=await api("/admin/api/overview");$("users-count").textContent=d.users;$("active-count").textContent=d.active_subscriptions;$("paid-count").textContent=d.successful_payments;$("pending-count").textContent=d.pending_payments}
async function loadUsers(){const d=await api("/admin/api/users?limit="+pageSize+"&offset="+userOffset);$("users-table").replaceChildren();d.items.forEach(u=>{row("users-table",[u.telegram_id,u.username?"@"+u.username:"—",u.subscription_status,u.expires_at?new Date(u.expires_at).toLocaleDateString():"—",new Date(u.created_at).toLocaleDateString()]);const tr=$("users-table").lastElementChild;const button=document.createElement("button");button.className="action";button.textContent="Подробнее";button.onclick=()=>showCustomer(u.telegram_id);tr.appendChild(cell(button.textContent));tr.lastElementChild.replaceChildren(button)});$("users-page").textContent="Показано "+(d.total?userOffset+1:0)+"–"+Math.min(userOffset+d.items.length,d.total)+" из "+d.total;$("users-prev").disabled=userOffset===0;$("users-next").disabled=userOffset+pageSize>=d.total}
async function showCustomer(id){$("status").textContent="";try{const d=await api("/admin/api/users/"+encodeURIComponent(id));$("customer-title").textContent="Клиент "+d.telegram_id;$("customer-summary").textContent=(d.username?"@"+d.username+" · ":"")+"Зарегистрирован: "+new Date(d.created_at).toLocaleString();$("customer-subscriptions").replaceChildren();$("customer-payments").replaceChildren();d.subscriptions.forEach(x=>row("customer-subscriptions",[x.id,x.status,x.expires_at?new Date(x.expires_at).toLocaleString():"—",new Date(x.created_at).toLocaleString()]));d.payments.forEach(x=>row("customer-payments",[x.id,x.provider,x.amount+" "+x.currency,x.status,new Date(x.created_at).toLocaleString()]));$("customer-detail").hidden=false;$("customer-detail").scrollIntoView({behavior:"smooth",block:"nearest"})}catch(e){$("status").textContent=e.message}}
$("customer-close").onclick=()=>{$("customer-detail").hidden=true};
async function loadPayments(){const d=await api("/admin/api/payments?limit="+pageSize+"&offset="+paymentOffset);$("payments-table").replaceChildren();d.items.forEach(p=>row("payments-table",[p.id,p.telegram_id,p.provider,p.amount+" "+p.currency,p.status,new Date(p.created_at).toLocaleString()]));$("payments-page").textContent="Показано "+(d.total?paymentOffset+1:0)+"–"+Math.min(paymentOffset+d.items.length,d.total)+" из "+d.total;$("payments-prev").disabled=paymentOffset===0;$("payments-next").disabled=paymentOffset+pageSize>=d.total}
async function start(){try{if(!tg?.initData)throw Error("Откройте панель через Telegram Mini App");const d=await api("/admin/api/overview");$("loading").hidden=true;$("dashboard").hidden=false;$("welcome").textContent="Здравствуйте, "+(d.admin_name||"администратор");$("users-count").textContent=d.users;$("active-count").textContent=d.active_subscriptions;$("paid-count").textContent=d.successful_payments;$("pending-count").textContent=d.pending_payments;await loadUsers()}catch(e){$("loading").textContent=e.message}}
document.querySelectorAll("nav button[data-tab]").forEach(b=>b.addEventListener("click",async()=>{document.querySelectorAll("nav button").forEach(x=>x.classList.remove("active"));b.classList.add("active");const users=b.dataset.tab==="users";$("users-view").hidden=!users;$("payments-view").hidden=users;if(!users&&b.dataset.tab==="payments")try{await loadPayments()}catch(e){$("status").textContent=e.message}}));
$("users-refresh").onclick=()=>loadUsers().catch(e=>$("status").textContent=e.message);$("payments-refresh").onclick=()=>loadPayments().catch(e=>$("status").textContent=e.message);
$("users-prev").onclick=()=>{userOffset=Math.max(0,userOffset-pageSize);loadUsers()};$("users-next").onclick=()=>{userOffset+=pageSize;loadUsers()};
$("payments-prev").onclick=()=>{paymentOffset=Math.max(0,paymentOffset-pageSize);loadPayments()};$("payments-next").onclick=()=>{paymentOffset+=pageSize;loadPayments()};
start();
</script></body></html>""")


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
