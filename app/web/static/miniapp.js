const tg = window.Telegram?.WebApp;
tg?.ready?.();
tg?.expand?.();
let data=null;
const $=id=>document.getElementById(id);
function switchPage(page){
  document.querySelectorAll("[data-page-panel]").forEach(panel=>{
    const visible=panel.dataset.pagePanel===page;
    panel.hidden=!visible;
    panel.classList.toggle("active",visible);
  });
  document.querySelectorAll("#bottom-nav [data-page]").forEach(button=>{
    const active = button.dataset.page === ({order: "shop", devices: "vpn", guide: "vpn", purchases: "account", support: "account"}[page] || page);
    if(active)button.setAttribute("aria-current","page");else button.removeAttribute("aria-current");
  });
  window.scrollTo({top:0,behavior:"smooth"});
  tg?.HapticFeedback?.selectionChanged?.();
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
      $("trial-offer").hidden = false;
      $("trial-copy").textContent=d.trial.days+" дней · "+d.trial.traffic_gb+" ГБ · "+d.trial.device_limit+" устройств";
    }else{$("trial-offer").hidden = true}
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
    const d=results[0].value;
    const box=$("account-device-info");box.replaceChildren();
    const headline=document.createElement("p");headline.className="muted";
    headline.textContent="Зарегистрировано: "+d.items.length+" / "+(d.limit??"—");
    box.append(headline);
    if(!d.items.length){const empty=document.createElement("p");empty.className="muted";empty.textContent="Подключённых устройств пока нет.";box.append(empty)}
    else{
      const list=document.createElement("div");list.className="record-list";
      d.items.forEach((device,index)=>{
        const record=document.createElement("div");record.className="record-card";
        const title=document.createElement("strong");title.textContent=(device.name||"Устройство "+(index+1));
        const details=document.createElement("span");details.className="record-meta";details.textContent=device.platform||"Подключено к подписке";
        record.append(title,details);list.append(record);
      });box.append(list);
    }
  }else $("account-device-info").textContent="Информация об устройствах временно недоступна";
  if(results[1].status==="fulfilled"){
    const d=results[1].value,box=$("account-purchase-info");box.replaceChildren();
    if(!d.items.length){box.textContent="Покупок пока нет."}
    else{
      const list=document.createElement("div");list.className="record-list";
      d.items.forEach(payment=>{
        const record=document.createElement("div");record.className="record-card";
        const top=document.createElement("div");top.className="record-heading";
        const title=document.createElement("strong");title.textContent="Заказ №"+payment.id;
        const status=document.createElement("span");status.className="record-badge";
        const statuses={succeeded:"Выдан",paid:"Оплачен, готовится",pending:"Ожидает оплаты",failed:"Не оплачен",cancelled:"Отменён"};
        status.textContent=statuses[payment.status]||payment.status;
        status.dataset.status=payment.status;top.append(title,status);
        const details=document.createElement("span");details.className="record-meta";
        details.textContent=(payment.currency==="XTR"?payment.amount+" ⭐":(payment.amount/100).toFixed(2)+" "+payment.currency)+" · "+payment.provider;
        record.append(top,details);list.append(record);
      });box.append(list);
    }
  }else $("account-purchase-info").textContent="История покупок временно недоступна";
  if(results[2].status==="fulfilled"){
    const d=results[2].value;
    $("account-referral-info").textContent="Приглашено: "+d.count+" · Бонусных дней: "+(d.reward?.days||0)+" · Начислено: "+(d.reward?.earned||0)+(d.rewards_enabled?" · Бонусы применятся при следующем продлении.":" · Программа пока отключена.");
    if(d.link){
      $("account-referral-link").href=d.link;
      $("account-referral-link").textContent=d.link;
      $("account-referral-link").hidden = false;
      $("copy-referral").hidden=false;
      $("share-referral").href="https://t.me/share/url?url="+encodeURIComponent(d.link)+"&text="+encodeURIComponent("Приглашаю тебя в мой VPN-магазин!");
      $("share-referral").hidden=false;
    }
  }else $("account-referral-info").textContent="Реферальная информация временно недоступна";
}
function fillOptions(id,min,max,step,selected,label){const el=$(id);el.replaceChildren();const chosen=Math.min(max,Math.max(min,selected));const values=[];for(let n=min;n<=max;n+=step)values.push(n);if(!values.includes(chosen))values.push(chosen);values.sort((a,b)=>a-b);values.forEach(n=>{const o=document.createElement("option");o.value=String(n);o.textContent=n+" "+label;el.appendChild(o)});el.value=String(chosen)}
const providerLabels={telegram_stars:"Telegram Stars",yookassa:"ЮKassa",yoomoney:"ЮMoney",cryptopay:"Crypto Pay"};
function paymentMode(){return $("checkout-method").value||"telegram_stars"}
const rubles = new Intl.NumberFormat("ru-RU", {style: "currency", currency: "RUB", maximumFractionDigits: 2});
const money = minor => rubles.format(minor / 100);
const trafficLabel = value => value == null ? "—" : value === 0 ? "Безлимит" : value + " ГБ";
const subscriptionStatuses = {active: "Активна", disabled: "Отключена", expired: "Истекла", deleted: "Удалена"};
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
let selectedPlan = null;
let extraDevices = 0;
let orderBusy = false;

function showOrder(plan) {
  selectedPlan = plan;
  extraDevices = 0;
  $("order-title").textContent = plan.name;
  $("order-specification").textContent = `${plan.days} дней · ${trafficLabel(plan.traffic_gb)}`;
  $("order-included").textContent = plan.devices_included;
  $("order-unit").textContent = plan.extra_device_price_rub_minor == null
    ? "Недоступно" : money(plan.extra_device_price_rub_minor);
  $("order-terms").checked = false;
  updateOrder();
  switchPage("order");
}

function orderTotals() {
  const plan = selectedPlan;
  const rub = plan.price_rub_minor + extraDevices * (plan.extra_device_price_rub_minor ?? 0);
  const stars = extraDevices && plan.extra_device_price_stars == null
    ? null : plan.price_stars + extraDevices * (plan.extra_device_price_stars ?? 0);
  return {rub, stars};
}

function updateOrder() {
  const {rub, stars} = orderTotals();
  $("order-extra").value = String(extraDevices);
  $("order-minus").disabled = extraDevices === 0;
  $("order-plus").disabled = extraDevices >= selectedPlan.max_extra_devices;
  $("order-total").textContent = rub > 0 ? money(rub) : stars == null ? "—" : stars + " ⭐";
  $("order-confirm").hidden = false;
  $("order-payment").hidden = true;
  $("order-status").textContent = "";
}

for (const [button, delta] of [["order-minus", -1], ["order-plus", 1]]) {
  $(button).onclick = () => {
    if (!selectedPlan || orderBusy) return;
    extraDevices = Math.max(0, Math.min(selectedPlan.max_extra_devices, extraDevices + delta));
    updateOrder();
  };
}

$("order-confirm").onclick = () => {
  const {rub, stars} = orderTotals();
  const select = $("order-method");
  select.replaceChildren();
  for (const [key, label] of Object.entries(providerLabels)) {
    if (!data.payment_methods?.[key]) continue;
    if (key === "telegram_stars" ? stars == null : rub <= 0) continue;
    select.add(new Option(label, key));
  }
  if (!select.options.length || !data.terms_url) {
    $("order-status").textContent = !data.terms_url ? "Условия продажи пока не опубликованы." : "Для этого заказа нет доступных способов оплаты.";
    return;
  }
  $("order-status").textContent = "";
  $("order-confirm").hidden = true;
  $("order-payment").hidden = false;
  updateProviderTotal();
  select.focus();
};

function updateProviderTotal() {
  const {rub, stars} = orderTotals();
  $("order-provider-total").textContent = "К оплате · " + (
    $("order-method").value === "telegram_stars" ? stars + " ⭐" : money(rub)
  );
}
$("order-method").onchange = updateProviderTotal;

$("order-payment").onsubmit = async event => {
  event.preventDefault();
  if (orderBusy || !$("order-terms").checked) return;
  const provider = $("order-method").value;
  const useStars = provider === "telegram_stars";
  const payload = {plan_id: selectedPlan.id, extra_devices: extraDevices, revision: selectedPlan.revision};
  orderBusy = true;
  $("order-pay").disabled = true;
  $("order-status").textContent = "Создаём счёт…";
  try {
    if (useStars && !tg?.openInvoice) throw Error("Откройте Mini App через Telegram");
    await api("/api/v1/webapp/terms/accept", {accepted: true});
    if (useStars) {
      const invoice = await api("/api/v1/webapp/plans/checkout", payload);
      const status = await new Promise(resolve => tg.openInvoice(invoice.invoice_url, resolve));
      $("order-status").textContent = status === "paid" ? "Оплата подтверждена" : status === "cancelled" ? "Оплата отменена" : "Проверьте заказ в разделе «Покупки».";
      if (status === "paid") location.reload();
    } else {
      const invoice = await api("/api/v1/webapp/plans/external/checkout", {...payload, provider});
      openCheckoutUrl(invoice.url);
      $("order-status").textContent = "Счёт №" + invoice.payment_id + " создан. Статус доступен в покупках.";
    }
  } catch (error) {
    $("order-status").textContent = error.message;
  } finally {
    orderBusy = false;
    $("order-pay").disabled = false;
  }
};

async function loadReadyPlans() {
  try {
    const result = await api("/api/v1/plans");
    const list = $("plans-list");
    list.replaceChildren();
    for (const plan of result.items) {
      const item = document.createElement("article");
      item.className = "panel plan-card";
      item.dataset.featured = plan.is_featured;
      const head = document.createElement("div");
      head.className = "section-title";
      const title = document.createElement("h2");
      title.textContent = plan.name;
      head.append(title);
      if (plan.badge || plan.is_featured) {
        const badge = document.createElement("span");
        badge.className = "status-pill";
        badge.textContent = plan.badge || "Популярный";
        head.append(badge);
      }
      const specs = document.createElement("p");
      specs.className = "plan-spec";
      specs.textContent = `${plan.days} дней · ${trafficLabel(plan.traffic_gb)} · устройств: ${plan.devices_included}`;
      item.append(head, specs);
      if (plan.description) {
        const description = document.createElement("p");
        description.className = "muted plan-description";
        description.textContent = plan.description;
        item.append(description);
      }
      const price = document.createElement("strong");
      price.className = "plan-price";
      price.textContent = plan.price_rub_minor > 0 ? money(plan.price_rub_minor) : plan.price_stars + " ⭐";
      const button = document.createElement("button");
      button.type = "button";
      button.className = (plan.is_featured ? "primary-btn" : "secondary-btn") + " full";
      button.textContent = "Выбрать";
      button.onclick = () => { if (!orderBusy) showOrder(plan); };
      item.append(price, button);
      list.append(item);
    }
    $("plans-status").hidden = Boolean(result.items.length);
    $("plans-status").textContent = "Готовых тарифов пока нет. Можно собрать свой.";
  } catch {
    $("plans-status").hidden = false;
    $("plans-status").textContent = "Не удалось загрузить тарифы. Попробуйте открыть магазин заново.";
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

function renderProfile(profile, user) {
  const name = profile?.name || "Remnaum";
  $("store-name").textContent = name;
  $("store-mark").textContent = name.trim().charAt(0).toUpperCase() || "R";
  $("store-tagline").textContent = profile?.tagline || "Ваш VPN — ваши правила";
  $("welcome").textContent = profile?.description || "Выберите подписку и подключите свои устройства.";
  for (const [id, url] of [["store-logo", profile?.logo_image_url]]) {
    if (url) {
      $(id).src = url;
      $(id).hidden = false;
    }
  }
  $("store-mark").hidden = Boolean(profile?.logo_image_url);
  const contact = profile?.support_contact?.trim();
  if (contact) {
    $("support-text").textContent = contact;
    const candidate = /^@[a-z0-9_]+$/i.test(contact) ? "https://t.me/" + contact.slice(1) : contact;
    try {
      const url = new URL(candidate);
      if (url.protocol === "https:" && !url.username && !url.password) {
        $("support-link").href = url.href;
        $("support-link").hidden = false;
      }
    } catch { /* A plain-text support contact is valid. */ }
  }
  $("profile-name").textContent = user.first_name || "Покупатель";
  $("profile-monogram").textContent = $("profile-name").textContent.trim().charAt(0).toUpperCase();
  $("profile-id").textContent = user.id;
}

function renderSubscription(subscription) {
  if (!subscription) return;
  $("no-subscription").hidden = true;
  $("subscription").hidden = false;
  const status = subscriptionStatuses[subscription.status] || subscription.status;
  $("home-subscription-status").textContent = status;
  $("sub-status").dataset.status = subscription.status;
  $("sub-status").textContent = status;
  $("sub-expiry").textContent = subscription.expires_at ? new Date(subscription.expires_at).toLocaleDateString("ru-RU") : "—";
  $("sub-traffic").textContent = trafficLabel(subscription.traffic_gb);
  $("sub-devices").textContent = subscription.device_limit ?? "—";
  $("sub-days").textContent = subscription.days_left ?? "—";
  if (subscription.subscription_url?.startsWith("https://")) {
    $("sub-link").href = subscription.subscription_url;
    $("sub-link").hidden = false;
  }
}

async function start() {
  try {
    if (!tg?.initData) throw Error("Откройте Mini App через кнопку в Telegram");
    data = await api("/api/v1/webapp/bootstrap");
    renderProfile(data.profile, data.user);
    renderSubscription(data.subscription);
    $("admin-entry").hidden = !data.is_admin;
    if (data.is_admin) $("admin-entry").onclick = () => window.location.assign("/admin" + window.location.hash);
    for (const id of ["terms-link", "order-terms-link"]) {
      if (data.terms_url) $(id).href = data.terms_url;
      else $(id).textContent = "условия не опубликованы";
    }
    fillOptions("days", data.limits.days_min, data.limits.days_max, 1, data.limits.days_default, "дней");
    fillOptions("traffic", data.limits.traffic_min, data.limits.traffic_max, 1, data.limits.traffic_default, "ГБ");
    fillOptions("devices", data.limits.devices_min, data.limits.devices_max, 1, data.limits.devices_default, "устройств");
    fillPaymentOptions();
    recalc();
    $("loading").hidden = true;
    $("app").hidden = false;
    $("bottom-nav").hidden = false;
    await Promise.all([loadAccountPanels(), loadOffers(), loadReadyPlans()]);
  } catch (error) {
    $("loading").hidden = false;
    $("loading").textContent = error.message;
  }
}
start();
