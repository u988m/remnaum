const tg=window.Telegram?.WebApp;tg?.ready();tg?.expand();
let data=null;
const $=id=>document.getElementById(id);
function switchPage(page){
  document.querySelectorAll("[data-page-panel]").forEach(panel=>{
    const visible=panel.dataset.pagePanel===page;
    panel.hidden=!visible;
    panel.classList.toggle("active",visible);
  });
  document.querySelectorAll("#bottom-nav [data-page]").forEach(button=>{
    const active=button.dataset.page===page;
    button.classList.toggle("nav-active",active);
    if(active)button.setAttribute("aria-current","page");else button.removeAttribute("aria-current");
  });
  window.scrollTo({top:0,behavior:"smooth"});
  tg?.HapticFeedback?.selectionChanged();
}
document.querySelectorAll("[data-page], [data-go-page]").forEach(button=>{
  button.addEventListener("click",()=>switchPage(button.dataset.page||button.dataset.goPage));
});
$("copy-referral").onclick=async()=>{
  const link=$("account-referral-link").href;
  try{
    if(!navigator.clipboard?.writeText)throw Error("clipboard unavailable");
    await navigator.clipboard.writeText(link);
    $("copy-referral").textContent="✓ Ссылка скопирована";
    tg?.HapticFeedback?.notificationOccurred("success");
  }catch{
    $("copy-referral").textContent="Откройте ссылку выше для копирования";
  }
};

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
    $("account-purchase-info").textContent=d.items.length?d.items.map(x=>"#"+x.id+" · "+formatPaymentAmount(x.amount,x.currency)+" · "+paymentStatusLabel(x.status)).join(" | "):"Покупок пока нет";
  }else $("account-purchase-info").textContent="История покупок временно недоступна";
  if(results[2].status==="fulfilled"){
    const d=results[2].value;
    $("account-referral-info").textContent="Приглашено: "+d.count+" · Бонусных дней: "+(d.reward?.days||0)+" · Начислено: "+(d.reward?.earned||0)+(d.rewards_enabled?" · Бонусы применятся при следующем продлении.":" · Программа пока отключена.");
    if(d.link){$("account-referral-link").href=d.link;$("account-referral-link").textContent=d.link;$("account-referral-link").style.display="block";$("copy-referral").hidden=false}
  }else $("account-referral-info").textContent="Реферальная информация временно недоступна";
}
let trackedOrder = 0;

function trackPayment(paymentId, paid = false) {
  const request = ++trackedOrder;
  if (paid) $("status").textContent = "Оплата получена. Активируем подписку…";
  async function refresh(remaining) {
    if (request !== trackedOrder) return;
    try {
      const {status} = await api("/api/v1/webapp/purchases/" + paymentId);
      if (request !== trackedOrder) return;
      if (status === "succeeded") {
        $("status").textContent = "Подписка активирована.";
        try {
          const current = await api("/api/v1/webapp/bootstrap");
          if (request !== trackedOrder) return;
          renderSubscription(current.subscription, current.activation_pending);
          switchPage("vpn");
          loadAccountPanels();
        } catch {
          $("status").textContent = "Подписка активирована. Откройте магазин заново, чтобы получить ссылку.";
        }
        return;
      }
      if (status === "failed") {
        $("status").textContent = paymentStatusLabel(status);
        return;
      }
      if (status === "paid") {
        paid = true;
        $("status").textContent = "Оплата получена. Активируем подписку…";
      }
    } catch {
      // A temporary network failure should not stop tracking the order.
    }
    if (remaining > 0) setTimeout(() => refresh(remaining - 1), 3000);
    else if (paid) $("status").textContent = "Подписка ещё активируется. Сообщим о готовности в Telegram.";
  }
  refresh(20);
}

$("activation-refresh").onclick = async () => {
  $("activation-refresh").disabled = true;
  try {
    const current = await api("/api/v1/webapp/bootstrap");
    renderSubscription(current.subscription, current.activation_pending);
  } catch {
    $("activation-message").textContent = "Не удалось обновить статус. Попробуйте ещё раз.";
  } finally {
    $("activation-refresh").disabled = false;
  }
};

function renderSubscription(subscription, activating) {
  $("activation-notice").hidden = !activating;
  $("activation-message").textContent = "Оплата получена. Активируем подписку.";
  $("no-subscription").hidden = Boolean(subscription || activating);
  $("subscription").style.display = subscription ? "block" : "none";
  if (!subscription) return;
  $("sub-status").textContent = ({active:"Активна", expired:"Истекла", disabled:"Отключена"})[subscription.status] || subscription.status;
  $("sub-expiry").textContent = subscription.expires_at ? new Date(subscription.expires_at).toLocaleString("ru-RU") : "—";
  $("sub-traffic").textContent = subscription.traffic_gb ?? "—";
  $("sub-devices").textContent = subscription.device_limit ?? "—";
  $("sub-days").textContent = subscription.days_left ?? "—";
  const url = subscription.subscription_url;
  $("sub-link").style.display = url?.startsWith("https://") ? "inline-block" : "none";
  if (url?.startsWith("https://")) $("sub-link").href = url;
  else $("sub-link").removeAttribute("href");
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
        openCheckoutUrl(result.url);$("status").textContent="Счёт №"+result.payment_id+" создан.";trackPayment(result.payment_id);
        btn.disabled=false;return;
      }
      if(!tg?.openInvoice)throw Error("Откройте Mini App из Telegram");
      const invoice=await api("/api/v1/webapp/plans/checkout",{plan_id:plan.id});
      tg.openInvoice(invoice.invoice_url,status=>{
        if(status==="paid"||status==="pending") trackPayment(invoice.payment_id,status==="paid");
        else $("status").textContent=status==="cancelled"?"Оплата отменена":"Оплата не завершена";
        btn.disabled=false;
      });
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
    openCheckoutUrl(result.url);$("status").textContent="Счёт №"+result.payment_id+" создан. Подписка появится после подтверждения оплаты.";trackPayment(result.payment_id);
    recalc();return;
  }
  const result=await api("/api/v1/webapp/checkout",limits());
  if(!tg?.openInvoice)throw Error("Откройте магазин из Telegram");
  tg.openInvoice(result.invoice_url,status=>{
    if(status==="paid"||status==="pending") trackPayment(result.payment_id,status==="paid");
    else $("status").textContent=status==="cancelled"?"Оплата отменена":"Оплата не завершена";
    recalc();
  });
}catch(e){$("status").textContent=e.message;recalc()}};

async function start(){try{if(!tg?.initData)throw Error("Откройте Mini App через кнопку в Telegram");data=await api("/api/v1/webapp/bootstrap");if(data.profile){$("store-name").textContent=data.profile.name;$("store-tagline").textContent=data.profile.tagline||"Ваш VPN";document.documentElement.style.setProperty("--accent",data.profile.accent_color);document.documentElement.style.setProperty("--tg-theme-button-color",data.profile.accent_color);if(data.profile.welcome_image_url){$("hero-image").src=data.profile.welcome_image_url;$("hero-image").hidden=false}if(data.profile.support_contact){$("support-text").textContent=data.profile.support_contact;const contact=data.profile.support_contact.trim();const candidate=contact.startsWith("@")?"https://t.me/"+contact.slice(1):contact;if(candidate.startsWith("https://t.me/")||candidate.startsWith("https://telegram.me/")){$("support-link").href=candidate;$("support-link").hidden=false}}}$("loading").style.display="none";$("app").style.display="block";$("bottom-nav").hidden=false;$("profile-name").textContent=data.user.first_name||"Покупатель";$("profile-id").textContent=data.user.id;$("admin-entry").hidden=!data.is_admin;if(data.is_admin){$("admin-entry").onclick=()=>window.location.assign("/admin"+window.location.hash)}$("welcome").textContent=data.user.first_name?"Здравствуйте, "+data.user.first_name+"! Настройте подписку под себя.":"Выберите параметры подписки.";if(data.terms_url){$("terms-link").href=data.terms_url}else{$("terms-link").textContent="условия не настроены";$("terms-link").removeAttribute("href");$("buy").disabled=true}
fillOptions("days",data.limits.days_min,data.limits.days_max,1,data.limits.days_default,"дней");fillOptions("traffic",data.limits.traffic_min,data.limits.traffic_max,1,data.limits.traffic_default,"ГБ");fillOptions("devices",data.limits.devices_min,data.limits.devices_max,1,data.limits.devices_default,"устройств");renderSubscription(data.subscription,data.activation_pending);
$("buy").disabled=true;fillPaymentOptions();recalc();loadAccountPanels();loadOffers();await loadReadyPlans()}catch(e){$("loading").textContent=e.message}}
start();
