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
<title>Remnaum — платежи</title><script src="https://telegram.org/js/telegram-web-app.js"></script><style>
body{font:16px system-ui;background:#10131a;color:#f4f6fb;margin:0;padding:24px}
main{max-width:760px;margin:auto}section{background:#1a202b;border:1px solid #303949;border-radius:14px;padding:18px;margin:14px 0}
label{display:block;margin:10px 0}input[type=text],input[type=password]{box-sizing:border-box;width:100%;padding:11px;background:#0e131b;color:#fff;border:1px solid #465267;border-radius:8px}
button{padding:11px 16px;border:0;border-radius:8px;background:#dbeafe;color:#111827;font-weight:700}
small{color:#aab5c7}.row{display:flex;justify-content:space-between;align-items:center;gap:12px}
</style></head><body><main><h1>Платёжные системы</h1>
<p>Настройки магазина Remnaum. Секреты сохраняются в зашифрованном виде и не отображаются повторно.</p>
<a href="/admin" style="color:#9ecbff">← Панель управления</a>
<section><div class="row"><b>Telegram Stars</b><input id="telegram_stars" type="checkbox" checked></div><small>Оплата в Telegram. Включена по умолчанию.</small></section>
<section><div class="row"><b>ЮKassa</b><input id="yookassa" type="checkbox"></div><label>Shop ID<input id="yookassa_shop_id" type="text"></label><label>Secret key<input id="yookassa_secret_key" type="password"></label><small id="yookassa_status"></small></section>
<section><div class="row"><b>ЮMoney</b><input id="yoomoney" type="checkbox"></div><label>Номер кошелька<input id="yoomoney_receiver" type="text"></label><label>Секрет уведомлений<input id="yoomoney_notification_secret" type="password"></label><small id="yoomoney_status"></small></section>
<section><div class="row"><b>Crypto Pay</b><input id="cryptopay" type="checkbox"></div><label>API token<input id="cryptopay_api_token" type="password"></label><small id="cryptopay_status"></small></section>
<button id="load">Загрузить настройки</button> <button id="save">Сохранить</button><p id="status"></p>
<script>
const ids=["telegram_stars","yookassa","yoomoney","cryptopay"];
const tg=window.Telegram?.WebApp;tg?.ready();
async function api(method,body){const r=await fetch("/admin/api/payment-settings",{method,headers:{"X-Telegram-Init-Data":tg?.initData||"","Content-Type":"application/json"},body:body?JSON.stringify(body):undefined});const d=await r.json();if(!r.ok)throw Error(d.detail||"Ошибка запроса");return d}
document.getElementById("load").onclick=async()=>{try{const d=await api("GET");ids.forEach(k=>document.getElementById(k).checked=!!d.methods[k]);Object.keys(d.configured).forEach(k=>{const el=document.getElementById(k+"_status");if(el)el.textContent=d.configured[k]?"Реквизиты сохранены":"Реквизиты не настроены"});document.getElementById("status").textContent="Настройки загружены"}catch(e){document.getElementById("status").textContent=e.message}};
document.getElementById("save").onclick=async()=>{try{const methods={};ids.forEach(k=>methods[k]=document.getElementById(k).checked);const credentials={yookassa:{shop_id:document.getElementById("yookassa_shop_id").value,secret_key:document.getElementById("yookassa_secret_key").value},yoomoney:{receiver:document.getElementById("yoomoney_receiver").value,notification_secret:document.getElementById("yoomoney_notification_secret").value},cryptopay:{api_token:document.getElementById("cryptopay_api_token").value}};const d=await api("PUT",{methods,credentials});Object.keys(d.configured).forEach(k=>{const el=document.getElementById(k+"_status");if(el)el.textContent=d.configured[k]?"Реквизиты сохранены":"Реквизиты не настроены"});["yookassa_secret_key","yoomoney_notification_secret","cryptopay_api_token"].forEach(k=>document.getElementById(k).value="");document.getElementById("status").textContent="Настройки сохранены"}catch(e){document.getElementById("status").textContent=e.message}};
</script></main></body></html>""")

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
    return HTMLResponse("""<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Remnaum — тарифы</title><script src="https://telegram.org/js/telegram-web-app.js"></script><body style="font:16px system-ui;max-width:760px;margin:32px auto;padding:0 16px;background:#10131a;color:#f4f6fb"><h1>Цены тарифов</h1><p>Настройте формулу стоимости отдельно для рублей и Telegram Stars.</p><a href="/admin" style="color:#9ecbff">← Панель управления</a><div id="fields"></div><button id="load">Загрузить</button> <button id="save">Сохранить</button><p id="status"></p><script>
const tg=window.Telegram?.WebApp;tg?.ready();const keys=["rub_per_day_minor","rub_per_gb_minor","rub_per_device_minor","stars_per_day","stars_per_gb","stars_per_device","currency"];
const labels={rub_per_day_minor:"Рубли: за день (копейки)",rub_per_gb_minor:"Рубли: за 1 ГБ (копейки)",rub_per_device_minor:"Рубли: за устройство (копейки)",stars_per_day:"Stars: за день",stars_per_gb:"Stars: за 1 ГБ",stars_per_device:"Stars: за устройство",currency:"Валюта внешнего магазина"};
const fields=document.getElementById("fields");keys.forEach(k=>{const l=document.createElement("label");l.style.display="block";l.style.margin="12px 0";l.textContent=labels[k];const i=document.createElement("input");i.id=k;i.type=k==="currency"?"text":"number";i.min="0";i.style.display="block";i.style.padding="9px";l.appendChild(i);fields.appendChild(l)});
async function api(method,body){const r=await fetch("/admin/api/tariff-pricing",{method,headers:{"X-Telegram-Init-Data":tg?.initData||"","Content-Type":"application/json"},body:body?JSON.stringify(body):undefined});const d=await r.json();if(!r.ok)throw Error(d.detail||"Ошибка запроса");return d}
function fill(d){keys.forEach(k=>document.getElementById(k).value=d[k])}
document.getElementById("load").onclick=async()=>{try{fill(await api("GET"));document.getElementById("status").textContent="Цены загружены"}catch(e){document.getElementById("status").textContent=e.message}};
document.getElementById("save").onclick=async()=>{try{const pricing={};keys.forEach(k=>pricing[k]=k==="currency"?document.getElementById(k).value:Number(document.getElementById(k).value));fill(await api("PUT",{pricing}));document.getElementById("status").textContent="Цены сохранены"}catch(e){document.getElementById("status").textContent=e.message}};
</script></body></html>""")


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
<header><div class="brand"><span id="store-name">Remnaum</span><span style="color:var(--tg-theme-button-color,#4b8bea)">.</span></div><div class="tag">VPN STORE</div></header>
<h1 id="store-tagline">Ваш VPN — ваши правила</h1><div class="muted" id="welcome">Выберите параметры подписки и получите доступ за пару секунд.</div>
<section class="panel" id="subscription" style="display:none"><h2>Ваша подписка</h2><div class="row"><span class="muted">Статус</span><b id="sub-status">—</b></div><div class="row"><span class="muted">Действует до</span><b id="sub-expiry">—</b></div><div class="grid"><div class="metric"><b id="sub-traffic">—</b><span>ГБ</span></div><div class="metric"><b id="sub-devices">—</b><span>устройств</span></div><div class="metric"><b id="sub-days">—</b><span>дней осталось</span></div></div><a id="sub-link" href="#" target="_blank" style="display:none">Открыть ссылку подключения</a></section>
<section class="panel" id="connection-guide">
<h2>📖 Как подключиться</h2>
<p class="muted">1. Установите клиент, поддерживающий подписку Remnawave, на телефон или компьютер.</p>
<p class="muted">2. Откройте ссылку подключения выше. Скопируйте или импортируйте её в VPN-клиент.</p>
<p class="muted">3. Обновите список серверов в клиенте и выберите подключение.</p>
<p class="muted">Если возникли проблемы, воспользуйтесь контактом поддержки магазина.</p>
</section>
<section class="panel" id="account-devices"><h2>📱 Мои устройства</h2><div class="muted" id="account-device-info">Загружаем…</div></section>
<section class="panel" id="account-purchases"><h2>🧾 Мои покупки</h2><div class="muted" id="account-purchase-info">Загружаем…</div></section>
<section class="panel" id="account-referrals"><h2>🎁 Пригласить друга</h2><div class="muted" id="account-referral-info">Загружаем…</div><a id="account-referral-link" style="display:none"></a></section>
<section class="panel" id="trial-offer" style="display:none">
<h2>🎁 Пробный доступ</h2><p class="muted" id="trial-copy"></p>
<button id="trial-claim" type="button">Получить пробный доступ</button>
<p class="status" id="trial-status"></p>
</section>
<section class="panel" id="promo-offer">
<h2>🎟 Промокод</h2><p class="muted" id="promo-balance">Ваши бонусные дни появятся здесь</p>
<input id="promo-input" maxlength="32" placeholder="Введите промокод" autocomplete="off" style="width:100%;padding:14px;background:var(--tg-theme-bg-color,#0b1018);color:inherit;border:1px solid #334155;border-radius:12px">
<button id="promo-activate" type="button">Активировать код</button>
<p class="status" id="promo-status"></p>
</section>
<section class="panel" id="fixed-plans" style="display:none"><h2>Готовые тарифы</h2><div id="plans-list"></div><p class="muted">Цены готовых тарифов задаёт владелец магазина. Для оплаты примите условия ниже.</p></section>
<section class="panel"><h2>Соберите свой тариф</h2><div class="row"><label for="days">Срок подписки</label><select id="days"></select></div><div class="row"><label for="traffic">Трафик</label><select id="traffic"></select></div><div class="row"><label for="devices">Устройства</label><select id="devices"></select></div>
<div class="row"><label for="checkout-method">Способ оплаты</label><select id="checkout-method"></select></div>
<div style="margin-top:16px"><div class="muted" id="price-label">Стоимость</div><div class="price" id="stars-price">—</div></div>
<label class="check"><input id="terms" type="checkbox"><span>Я принимаю <a id="terms-link" href="#" target="_blank">условия оказания услуги</a>.</span></label>
<button id="buy" disabled>Оплатить Telegram Stars</button><div id="status" class="status"></div></section>
<div class="muted" style="font-size:12px;text-align:center;margin-top:20px">Способы оплаты настраивает владелец магазина · Remnaum</div>
</div></main>
<script>
const tg=window.Telegram?.WebApp;tg?.ready();tg?.expand();
let data=null;
const $=id=>document.getElementById(id);
function api(path,body){return fetch(path,{method:body?"POST":"GET",headers:{"Content-Type":"application/json","X-Telegram-Init-Data":tg?.initData||""},body:body?JSON.stringify(body):undefined}).then(async r=>{const d=await r.json();if(!r.ok)throw Error(d.detail||"Ошибка запроса");return d})}
async function loadOffers(){
  try{
    const d=await api("/api/v1/webapp/offers");
    if(d.trial.enabled&&d.trial.eligible){
      $("trial-offer").style.display="block";
      $("trial-copy").textContent=d.trial.days+" дней · "+d.trial.traffic_gb+" ГБ · "+d.trial.device_limit+" устройств";
    }else{$("trial-offer").style.display="none"}
    $("promo-balance").textContent="Бонусные дни: "+d.promo_balance.days+" · Активировано кодов: "+d.promo_balance.claimed+". Бонус добавится к следующему оплаченному тарифу.";
  }catch(e){$("promo-balance").textContent="Бонусы пока недоступны"}
}
$("trial-claim").onclick=async()=>{
  $("trial-claim").disabled=true;
  try{const d=await api("/api/v1/webapp/trial",{});
    $("trial-status").textContent="Доступ активирован на "+d.days+" дней";
    location.reload();
  }catch(e){$("trial-status").textContent=e.message;$("trial-claim").disabled=false}
};
$("promo-activate").onclick=async()=>{
  const code=$("promo-input").value.trim();
  $("promo-activate").disabled=true;
  try{const d=await api("/api/v1/webapp/promos/redeem",{code});
    $("promo-status").textContent="Готово! Начислено +"+d.reward.bonus_days+" дней.";
    $("promo-input").value="";await loadOffers();
  }catch(e){$("promo-status").textContent=e.message}
  $("promo-activate").disabled=false;
};
async function loadAccountPanels(){
  const results=await Promise.allSettled([
    api("/api/v1/webapp/devices"),api("/api/v1/webapp/purchases"),api("/api/v1/webapp/referrals")
  ]);
  if(results[0].status==="fulfilled"){
    const d=results[0].value;const labels=d.items.map(x=>x.name);
    $("account-device-info").textContent="Лимит: "+(d.limit??"—")+" · "+(labels.length?labels.join(" · "):"Подключённых устройств пока нет");
  }else $("account-device-info").textContent="Информация об устройствах временно недоступна";
  if(results[1].status==="fulfilled"){
    const d=results[1].value;
    $("account-purchase-info").textContent=d.items.length?d.items.map(x=>"#"+x.id+" · "+(x.currency==="XTR"?x.amount+" ⭐":(x.amount/100).toFixed(2)+" "+x.currency)+" · "+x.status).join(" | "):"Покупок пока нет";
  }else $("account-purchase-info").textContent="История покупок временно недоступна";
  if(results[2].status==="fulfilled"){
    const d=results[2].value;
    $("account-referral-info").textContent="Приглашено: "+d.count+" · Бонусных дней: "+(d.reward?.days||0)+" · Начислено: "+(d.reward?.earned||0)+(d.rewards_enabled?" · Бонусы применятся при следующем продлении.":" · Программа пока отключена.");
    if(d.link){$("account-referral-link").href=d.link;$("account-referral-link").textContent=d.link;$("account-referral-link").style.display="block"}
  }else $("account-referral-info").textContent="Реферальная информация временно недоступна";
}
function fillOptions(id,min,max,step,selected,label){const el=$(id);el.replaceChildren();const chosen=Math.min(max,Math.max(min,selected));const values=[];for(let n=min;n<=max;n+=step)values.push(n);if(!values.includes(chosen))values.push(chosen);values.sort((a,b)=>a-b);values.forEach(n=>{const o=document.createElement("option");o.value=String(n);o.textContent=n+" "+label;el.appendChild(o)});el.value=String(chosen)}
const providerLabels={telegram_stars:"⭐ Telegram Stars",yookassa:"💳 ЮKassa",yoomoney:"🏦 ЮMoney",cryptopay:"🪙 Crypto Pay"};
function paymentMode(){return $("checkout-method").value||"telegram_stars"}
function openCheckoutUrl(url){
  if(!url.startsWith("https://"))throw Error("Некорректный адрес провайдера");
  if(tg?.openLink)tg.openLink(url);else window.open(url,"_blank","noopener,noreferrer");
}
function fillPaymentOptions(){
  const select=$("checkout-method");select.replaceChildren();
  Object.entries(data.payment_methods||{}).forEach(([key,enabled])=>{
    if(!enabled)return;
    const option=document.createElement("option");option.value=key;option.textContent=providerLabels[key]||key;
    select.append(option);
  });
  select.onchange=recalc;
}
async function loadReadyPlans(){
  const result=await api("/api/v1/plans");
  if(!result.items.length)return;
  $("fixed-plans").style.display="block";
  for(const plan of result.items){
    const item=document.createElement("div");item.className="panel";
    const title=document.createElement("b");title.textContent=plan.title;
    const details=document.createElement("div");details.className="muted";
    details.textContent=plan.duration_days+" дней · "+plan.traffic_gb+" ГБ · "+plan.device_limit+" устройств · "+plan.price_stars+" ⭐ / "+(plan.price_rub_minor/100).toFixed(2)+" ₽";
    const btn=document.createElement("button");btn.textContent="Выбрать тариф";
    btn.onclick=async()=>{try{
      if(!$("terms").checked)throw Error("Примите условия оказания услуги ниже");
      btn.disabled=true;$("status").textContent="Создаём счёт…";
      await api("/api/v1/webapp/terms/accept",{accepted:true});
      if(paymentMode()!=="telegram_stars"){
        const result=await api("/api/v1/webapp/plans/external/checkout",{plan_id:plan.id,provider:paymentMode()});
        openCheckoutUrl(result.url);$("status").textContent="Счёт №"+result.payment_id+" создан.";
        btn.disabled=false;return;
      }
      if(!tg?.openInvoice)throw Error("Откройте Mini App из Telegram");
      const invoice=await api("/api/v1/webapp/plans/checkout",{plan_id:plan.id});
      tg.openInvoice(invoice.invoice_url,status=>{$("status").textContent="Статус оплаты: "+status;if(status==="paid")setTimeout(()=>location.reload(),1800);btn.disabled=false});
    }catch(e){$("status").textContent=e.message;btn.disabled=false}};
    item.append(title,details,btn);$("plans-list").appendChild(item);
  }
}
function limits(){return {days:+$("days").value,traffic:+$("traffic").value,devices:+$("devices").value}}
function recalc(){
  if(!data)return;const v=limits(),p=data.pricing,method=paymentMode();
  const stars=v.days*p.stars_per_day+v.traffic*p.stars_per_gb+v.devices*p.stars_per_device;
  const rub=v.days*p.rub_per_day_minor+v.traffic*p.rub_per_gb_minor+v.devices*p.rub_per_device_minor;
  const external=method!=="telegram_stars";const amount=external?rub:stars;
  $("price-label").textContent=external?"Стоимость через "+(providerLabels[method]||method):"Стоимость в Telegram";
  $("stars-price").textContent=external?(rub/100).toFixed(2)+" "+(p.currency||"RUB"):stars+" ⭐";
  $("buy").textContent=external?"💳 Перейти к оплате":"⭐ Оплатить "+stars+" Stars";
  $("buy").disabled=!$("terms").checked||!data.terms_url||amount<1||!(data.payment_methods||{})[method];
}
["days","traffic","devices"].forEach(k=>$(k).addEventListener("change",recalc));$("terms").addEventListener("change",recalc);
$("buy").onclick=async()=>{try{
  $("buy").disabled=true;$("status").textContent="Создаём счёт…";
  await api("/api/v1/webapp/terms/accept",{accepted:true});
  if(paymentMode()!=="telegram_stars"){
    const result=await api("/api/v1/webapp/external/checkout",{...limits(),provider:paymentMode()});
    openCheckoutUrl(result.url);$("status").textContent="Счёт №"+result.payment_id+" создан. Подписка появится после подтверждения оплаты.";
    recalc();return;
  }
  const result=await api("/api/v1/webapp/checkout",limits());
  if(!tg?.openInvoice)throw Error("Откройте магазин из Telegram");
  tg.openInvoice(result.invoice_url,status=>{
    $("status").textContent=status==="paid"?"Оплата подтверждена":status==="cancelled"?"Отменено":"Статус: "+status;
    if(status==="paid")setTimeout(()=>location.reload(),1800);else recalc();
  });
}catch(e){$("status").textContent=e.message;recalc()}};

async function start(){try{if(!tg?.initData)throw Error("Откройте Mini App через кнопку в Telegram");data=await api("/api/v1/webapp/bootstrap");if(data.profile){$("store-name").textContent=data.profile.name;$("store-tagline").textContent=data.profile.tagline||"Ваш VPN";document.documentElement.style.setProperty("--tg-theme-button-color",data.profile.accent_color)}$("loading").style.display="none";$("app").style.display="block";$("welcome").textContent=data.user.first_name?"Здравствуйте, "+data.user.first_name+"! Настройте подписку под себя.":"Выберите параметры подписки.";if(data.terms_url){$("terms-link").href=data.terms_url}else{$("terms-link").textContent="условия не настроены";$("terms-link").removeAttribute("href");$("buy").disabled=true}
fillOptions("days",data.limits.days_min,data.limits.days_max,1,data.limits.days_default,"дней");fillOptions("traffic",data.limits.traffic_min,data.limits.traffic_max,1,data.limits.traffic_default,"ГБ");fillOptions("devices",data.limits.devices_min,data.limits.devices_max,1,data.limits.devices_default,"устройств");if(data.subscription){const s=data.subscription;$("subscription").style.display="block";$("sub-status").textContent=s.status;$("sub-expiry").textContent=s.expires_at?new Date(s.expires_at).toLocaleString():"—";$("sub-traffic").textContent=s.traffic_gb??"—";$("sub-devices").textContent=s.device_limit??"—";$("sub-days").textContent=s.days_left??"—";if(s.subscription_url){$("sub-link").href=s.subscription_url;$("sub-link").style.display="inline-block"}}
$("buy").disabled=true;fillPaymentOptions();recalc();loadAccountPanels();loadOffers();await loadReadyPlans()}catch(e){$("loading").textContent=e.message}}
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
        "profile": await get_store_profile(),
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
#plan-form input:not([type=checkbox]),#brand-form input:not([type=color]),#payment-settings-view input:not([type=checkbox]),#pricing-view input{width:100%;border:1px solid #34445c;border-radius:9px;background:#0b1018;color:#f4f7fc;padding:11px;margin-top:5px}#plan-form label,#brand-form label{display:block;margin:12px 0;font-size:12px;color:#9eacc0}#plan-form{margin-bottom:16px}
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
<nav><button class="active" data-tab="users">Клиенты</button><button data-tab="payments">Платежи</button><button data-tab="plans">Готовые тарифы</button><button data-tab="brand">Оформление</button><button data-tab="settings">Оплата</button><button data-tab="referrals">Рефералы</button><button data-tab="trial">Пробный</button><button data-tab="promos">Промокоды</button><button data-tab="tariffs">Конструктор</button></nav>
<section class="section" id="users-view"><div class="toolbar"><h2>Клиенты и подписки</h2><button class="action" id="users-refresh">Обновить</button></div><div class="table-wrap"><table><thead><tr><th>Telegram ID</th><th>Username</th><th>Подписка</th><th>До</th><th>Регистрация</th><th>Действие</th></tr></thead><tbody id="users-table"></tbody></table></div><div id="customer-detail" class="section" hidden aria-live="polite"><div class="toolbar"><h2 id="customer-title">Карточка клиента</h2><button class="action" id="customer-close">Закрыть</button></div><p class="muted" id="customer-summary"></p><h3>Подписки</h3><div class="table-wrap"><table><thead><tr><th>ID</th><th>Статус</th><th>Действует до</th><th>Создана</th></tr></thead><tbody id="customer-subscriptions"></tbody></table></div><h3>Платежи</h3><div class="table-wrap"><table><thead><tr><th>ID</th><th>Провайдер</th><th>Сумма</th><th>Статус</th><th>Дата</th></tr></thead><tbody id="customer-payments"></tbody></table></div></div><div class="toolbar" style="margin-top:12px"><button class="action" id="users-prev">Назад</button><span id="users-page" class="muted">Страница 1</span><button class="action" id="users-next">Далее</button></div></section>
<section class="section" id="payments-view" hidden><div class="toolbar"><h2>История платежей</h2><button class="action" id="payments-refresh">Обновить</button></div><div class="table-wrap"><table><thead><tr><th>ID</th><th>Клиент</th><th>Провайдер</th><th>Сумма</th><th>Статус</th><th>Дата</th></tr></thead><tbody id="payments-table"></tbody></table></div><div class="toolbar" style="margin-top:12px"><button class="action" id="payments-prev">Назад</button><span id="payments-page" class="muted">Страница 1</span><button class="action" id="payments-next">Далее</button></div></section>
<section class="section" id="plans-view" hidden>
<div class="toolbar"><h2>Готовые тарифы</h2><button class="action" id="plans-refresh">Обновить</button></div>
<p class="muted">Создайте тарифы с отдельными ценами в рублях и Telegram Stars. Неактивные планы скрываются от покупателей.</p>
<form id="plan-form">
<input type="hidden" id="plan-id">
<div class="grid" style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px">
<label>Название<input id="plan-title" required maxlength="80" placeholder="Стандарт"></label>
<label>Сортировка<input id="plan-order" type="number" value="0" min="0" max="10000"></label>
<label>Срок (дней)<input id="plan-days" type="number" value="30" min="1" required></label>
<label>Трафик (ГБ)<input id="plan-traffic" type="number" value="100" min="1" required></label>
<label>Устройств<input id="plan-devices" type="number" value="1" min="1" required></label>
<label>Цена (копейки RUB)<input id="plan-rub" type="number" value="9900" min="0" required></label>
<label>Цена (Stars)<input id="plan-stars" type="number" value="100" min="1" required></label>
<label>Активен<input id="plan-active" type="checkbox" checked></label>
</div>
<label>Описание<input id="plan-description" maxlength="2000" placeholder="Описание тарифа" style="width:100%"></label>
<div class="toolbar"><button class="action primary" type="submit">Сохранить тариф</button><button class="action" type="button" id="plan-new">Новый тариф</button></div>
</form>
<div class="table-wrap"><table><thead><tr><th>Тариф</th><th>Параметры</th><th>Рубли</th><th>Stars</th><th>Статус</th><th>Действие</th></tr></thead><tbody id="plans-table"></tbody></table></div>
</section>
<section class="section" id="referrals-view" hidden>
<h2>Реферальная программа</h2>
<p class="muted">Друг получает вашу ссылку. Бонусные дни начисляются пригласившему после первой успешно выданной платной подписки друга. Бонус автоматически продлевает следующую оплаченную подписку пригласившего.</p>
<form id="referral-form">
<label><input id="referral-enabled" type="checkbox"> Включить реферальные бонусы</label>
<label>Бонус за первого оплатившего друга (дней)<input id="referral-days" type="number" min="1" max="90" required value="3"></label>
<button class="action primary" type="submit">Сохранить настройки</button>
</form>
</section>
<section class="section" id="trial-view" hidden>
<h2>Пробный доступ</h2><p class="muted">Однократный пробный тариф до первой подписки. Выдача через Remnawave; без тестовых пользователей при установке.</p>
<form id="trial-form">
<label><input id="trial-enabled" type="checkbox"> Включить пробный доступ</label>
<label>Срок, дней<input id="trial-days" type="number" min="1" max="30" required></label>
<label>Трафик, ГБ<input id="trial-traffic" type="number" min="1" max="200" required></label>
<label>Устройства<input id="trial-devices" type="number" min="1" max="5" required></label>
<button type="submit" class="action primary">Сохранить пробный тариф</button>
</form></section>
<section class="section" id="promos-view" hidden>
<h2>Промокоды на бонусные дни</h2><p class="muted">Код активируется один раз на пользователя. Дни добавляются при следующей успешной покупке.</p>
<form id="promo-form">
<label>Код<input id="promo-code" maxlength="32" placeholder="WELCOME2026" required></label>
<label>Бонусных дней<input id="promo-days" type="number" min="1" max="90" value="7" required></label>
<label>Максимум активаций<input id="promo-uses" type="number" min="1" max="100000" value="100" required></label>
<button type="submit" class="action primary">Создать промокод</button>
</form>
<div id="promo-admin-list"></div></section>
<section class="section" id="brand-view" hidden>
<h2>Оформление магазина</h2>
<p class="muted">Настройки видны покупателям в боте и Mini App без перезапуска сервиса.</p>
<form id="brand-form">
<label>Название<input id="brand-name" maxlength="80" required></label>
<label>Заголовок<input id="brand-tagline" maxlength="300"></label>
<label>Описание<input id="brand-description" maxlength="300"></label>
<label>Контакт поддержки<input id="brand-support" maxlength="300"></label>
<label>Ссылка на условия продажи (HTTPS)<input id="brand-terms" maxlength="300" placeholder="https://..."></label>
<label>Основной цвет<input id="brand-color" type="color" value="#4b8bea"></label>
<button type="submit" class="action primary">Сохранить оформление</button>
</form>
</section>
<section class="section" id="payment-settings-view" hidden>
<h2>Способы оплаты</h2>
<p class="muted">Stars для цифровых покупок внутри Telegram; внешние провайдеры предназначены для самостоятельного веб-магазина.</p>
<div id="method-list"></div>
<h3>Реквизиты внешних провайдеров</h3>
<div class="grid" style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px">
<label>YooKassa Shop ID<input id="pay-yookassa-id"></label>
<label>YooKassa Secret<input id="pay-yookassa-secret" type="password"></label>
<label>YooMoney Receiver<input id="pay-yoomoney-receiver"></label>
<label>YooMoney Secret<input id="pay-yoomoney-secret" type="password"></label>
<label>Crypto Pay API token<input id="pay-crypto-token" type="password"></label>
</div>
<button class="action primary" id="payment-settings-save">Сохранить способы оплаты</button>
<p class="muted" id="provider-status"></p>
</section>
<section class="section" id="pricing-view" hidden>
<h2>Цены индивидуального конструктора</h2>
<p class="muted">Все суммы в рублях указываются в копейках; Stars — целыми единицами.</p>
<div id="pricing-fields" class="grid" style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px"></div>
<button class="action primary" id="pricing-save">Сохранить формулу цен</button>
</section>
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
async function adminApi(method,path,body){const r=await fetch(path,{method,headers:{"X-Telegram-Init-Data":tg?.initData||"","Content-Type":"application/json"},body:body?JSON.stringify(body):undefined});const d=await r.json();if(!r.ok)throw Error(typeof d.detail==="string"?d.detail:"Ошибка запроса");return d}
let cachedPlans=[];
async function loadPlans(){
  const d=await adminApi("GET","/admin/api/plans");cachedPlans=d.items;$("plans-table").replaceChildren();
  d.items.forEach(p=>{
    row("plans-table",[p.title,p.duration_days+" д / "+p.traffic_gb+" ГБ / "+p.device_limit+" уст.",(p.price_rub_minor/100).toFixed(2),p.price_stars,p.is_active?"Активен":"Скрыт"]);
    const td=cell("");const button=document.createElement("button");button.className="action";button.textContent="Изменить";
    button.onclick=()=>fillPlan(p);td.appendChild(button);$("plans-table").lastElementChild.appendChild(td);
  });
}
function fillPlan(p){
  $("plan-id").value=p.id;$("plan-title").value=p.title;$("plan-description").value=p.description||"";
  $("plan-days").value=p.duration_days;$("plan-traffic").value=p.traffic_gb;$("plan-devices").value=p.device_limit;
  $("plan-rub").value=p.price_rub_minor;$("plan-stars").value=p.price_stars;
  $("plan-order").value=p.sort_order;$("plan-active").checked=p.is_active;
}
$("plan-new").onclick=()=>{$("plan-form").reset();$("plan-id").value="";};
$("plans-refresh").onclick=()=>loadPlans().catch(e=>$("status").textContent=e.message);
$("plan-form").onsubmit=async e=>{
  e.preventDefault();try{
    const payload={title:$("plan-title").value,description:$("plan-description").value,
      duration_days:+$("plan-days").value,traffic_gb:+$("plan-traffic").value,device_limit:+$("plan-devices").value,
      price_rub_minor:+$("plan-rub").value,price_stars:+$("plan-stars").value,
      sort_order:+$("plan-order").value,is_active:$("plan-active").checked};
    const id=$("plan-id").value;
    await adminApi(id?"PUT":"POST",id?"/admin/api/plans/"+id:"/admin/api/plans",payload);
    $("status").textContent="Тариф сохранён";await loadPlans();
  }catch(e){$("status").textContent=e.message}
};
async function loadBrand(){
  const d=await adminApi("GET","/admin/api/store-profile");
  $("brand-name").value=d.name;$("brand-tagline").value=d.tagline;$("brand-description").value=d.description;
  $("brand-support").value=d.support_contact;$("brand-terms").value=d.terms_url||"";$("brand-color").value=d.accent_color;
}
$("brand-form").onsubmit=async e=>{
  e.preventDefault();try{
    await adminApi("PUT","/admin/api/store-profile",{
      name:$("brand-name").value,tagline:$("brand-tagline").value,
      description:$("brand-description").value,support_contact:$("brand-support").value,terms_url:$("brand-terms").value,
      accent_color:$("brand-color").value
    });$("status").textContent="Оформление сохранено";
  }catch(e){$("status").textContent=e.message}
};
const paymentNames=["telegram_stars","yookassa","yoomoney","cryptopay"];
async function loadPaymentSettings(){
  const d=await adminApi("GET","/admin/api/payment-settings");
  $("method-list").replaceChildren();
  paymentNames.forEach(name=>{
    const label=document.createElement("label");label.style.display="block";label.style.margin="10px";
    const check=document.createElement("input");check.type="checkbox";check.id="method-"+name;check.checked=!!d.methods[name];
    label.append(check,document.createTextNode(" "+name));$("method-list").appendChild(label);
  });
  $("provider-status").textContent=Object.keys(d.configured).map(k=>k+": "+(d.configured[k]?"настроен":"не настроен")).join(" · ");
}
$("payment-settings-save").onclick=async()=>{
  try{const methods={};paymentNames.forEach(n=>methods[n]=$("method-"+n).checked);
    const credentials={yookassa:{shop_id:$("pay-yookassa-id").value,secret_key:$("pay-yookassa-secret").value},
    yoomoney:{receiver:$("pay-yoomoney-receiver").value,notification_secret:$("pay-yoomoney-secret").value},
    cryptopay:{api_token:$("pay-crypto-token").value}};
    await adminApi("PUT","/admin/api/payment-settings",{methods,credentials});
    ["pay-yookassa-secret","pay-yoomoney-secret","pay-crypto-token"].forEach(k=>$(k).value="");
    $("status").textContent="Платёжные настройки сохранены";await loadPaymentSettings();
  }catch(e){$("status").textContent=e.message}
};
const pricingKeys=["rub_per_day_minor","rub_per_gb_minor","rub_per_device_minor","stars_per_day","stars_per_gb","stars_per_device","currency"];
async function loadPricing(){
  const data=await adminApi("GET","/admin/api/tariff-pricing");
  $("pricing-fields").replaceChildren();
  pricingKeys.forEach(k=>{
    const label=document.createElement("label");label.textContent=k;
    const input=document.createElement("input");input.id="pricing-"+k;input.type=k==="currency"?"text":"number";
    input.value=data[k];if(k!=="currency")input.min="0";
    label.appendChild(input);$("pricing-fields").appendChild(label);
  });
}
$("pricing-save").onclick=async()=>{try{
  const pricing={};pricingKeys.forEach(k=>pricing[k]=k==="currency"?$("pricing-"+k).value:+$("pricing-"+k).value);
  await adminApi("PUT","/admin/api/tariff-pricing",{pricing});$("status").textContent="Формула цен сохранена";
}catch(e){$("status").textContent=e.message}};
async function loadTrialSettings(){
  const d=await adminApi("GET","/admin/api/trial");
  $("trial-enabled").checked=d.enabled;$("trial-days").value=d.days;
  $("trial-traffic").value=d.traffic_gb;$("trial-devices").value=d.device_limit;
}
$("trial-form").onsubmit=async event=>{
  event.preventDefault();
  try{await adminApi("PUT","/admin/api/trial",{enabled:$("trial-enabled").checked,days:+$("trial-days").value,traffic_gb:+$("trial-traffic").value,device_limit:+$("trial-devices").value});$("status").textContent="Настройки пробного доступа сохранены"}
  catch(e){$("status").textContent=e.message}
};
async function loadPromos(){
  const d=await adminApi("GET","/admin/api/promos");
  $("promo-admin-list").replaceChildren();
  d.items.forEach(p=>{
    const line=document.createElement("div");line.className="row";line.style.margin="12px 0";
    const text=document.createElement("span");text.textContent=p.code+" · +"+p.bonus_days+" дней · "+p.used_count+"/"+p.max_uses;
    const toggle=document.createElement("button");toggle.type="button";toggle.className="action";
    toggle.textContent=p.active?"Отключить":"Включить";
    toggle.onclick=async()=>{try{await adminApi("PUT","/admin/api/promos/"+p.id,{active:!p.active});await loadPromos()}catch(e){$("status").textContent=e.message}};
    line.append(text,toggle);$("promo-admin-list").append(line);
  });
}
$("promo-form").onsubmit=async event=>{
  event.preventDefault();
  try{await adminApi("POST","/admin/api/promos",{code:$("promo-code").value,bonus_days:+$("promo-days").value,max_uses:+$("promo-uses").value});
      $("promo-code").value="";$("status").textContent="Промокод создан";await loadPromos()}
  catch(e){$("status").textContent=e.message}
};
async function loadReferrals(){
  const d=await adminApi("GET","/admin/api/referrals");
  $("referral-enabled").checked=d.enabled;$("referral-days").value=d.reward_days;
}
$("referral-form").onsubmit=async e=>{
  e.preventDefault();try{
    await adminApi("PUT","/admin/api/referrals",{enabled:$("referral-enabled").checked,reward_days:+$("referral-days").value});
    $("status").textContent="Реферальная программа сохранена";
  }catch(err){$("status").textContent=err.message}
};
async function start(){try{if(!tg?.initData)throw Error("Откройте панель через Telegram Mini App");const d=await api("/admin/api/overview");$("loading").hidden=true;$("dashboard").hidden=false;$("welcome").textContent="Здравствуйте, "+(d.admin_name||"администратор");$("users-count").textContent=d.users;$("active-count").textContent=d.active_subscriptions;$("paid-count").textContent=d.successful_payments;$("pending-count").textContent=d.pending_payments;await loadUsers()}catch(e){$("loading").textContent=e.message}}
document.querySelectorAll("nav button[data-tab]").forEach(b=>b.addEventListener("click",async()=>{document.querySelectorAll("nav button").forEach(x=>x.classList.remove("active"));b.classList.add("active");const tab=b.dataset.tab;$("users-view").hidden=tab!=="users";$("payments-view").hidden=tab!=="payments";$("plans-view").hidden=tab!=="plans";$("brand-view").hidden=tab!=="brand";$("referrals-view").hidden=tab!=="referrals";$("trial-view").hidden=tab!=="trial";$("promos-view").hidden=tab!=="promos";$("payment-settings-view").hidden=tab!=="settings";$("pricing-view").hidden=tab!=="tariffs";try{if(tab==="payments")await loadPayments();if(tab==="plans")await loadPlans();if(tab==="brand")await loadBrand();if(tab==="referrals")await loadReferrals();if(tab==="trial")await loadTrialSettings();if(tab==="promos")await loadPromos();if(tab==="settings")await loadPaymentSettings();if(tab==="tariffs")await loadPricing()}catch(e){$("status").textContent=e.message}}));
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
            title="Remnaum VPN",
            description=f"VPN: {quote.duration_days} дней, {quote.traffic_gb} ГБ, {quote.device_limit} устройств",
            payload=f"remnaum:{payment.id}", provider_token="", currency="XTR",
            prices=[LabeledPrice(label="Remnaum VPN subscription", amount=stars)],
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


@router.get("/api/v1/store-profile")
async def public_store_profile():
    return await get_store_profile()


@router.get("/admin/api/store-profile")
async def admin_get_store_profile(x_telegram_init_data: str | None = Header(default=None)):
    await _require_telegram_admin(x_telegram_init_data)
    return await get_store_profile()


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
    username = settings.telegram_bot_username.lstrip("@")
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
