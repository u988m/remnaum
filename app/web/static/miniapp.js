const tg = window.Telegram?.WebApp;
tg?.ready?.();
tg?.expand?.();
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
  if (page === "purchases") loadPurchases();
  if (page === "devices") loadDevices();
  if (page === "vpn") refreshSubscription().catch(() => {});
  if (page === "bonuses") { loadOffers(); loadReferrals(); }
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
    $("referral-copy-status").textContent="Ссылка скопирована.";
    tg?.HapticFeedback?.notificationOccurred("success");
  }catch{
    $("referral-copy-status").textContent="Нажмите и удерживайте ссылку для копирования.";
    $("account-referral-link").hidden = false;
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
async function loadDevices() {
  const box = $("account-device-info");
  try {
    const data = await api("/api/v1/webapp/devices");
    box.replaceChildren();
    const headline = document.createElement("p");
    headline.className = "muted";
    headline.textContent = "Зарегистрировано: " + data.items.length + " / " + (data.limit ?? "—");
    box.append(headline);
    const list = document.createElement("div");
    list.className = "record-list";
    for (const [index, device] of data.items.entries()) {
      const record = document.createElement("div");
      record.className = "record-card";
      const title = document.createElement("strong");
      title.textContent = device.name || "Устройство " + (index + 1);
      record.append(title);
      list.append(record);
    }
    if (!data.items.length) list.textContent = "Подключённых устройств пока нет.";
    box.append(list);
  } catch {
    box.textContent = "Информация об устройствах временно недоступна.";
  }
}

async function loadPurchases() {
  const box = $("account-purchase-info");
  try {
    const data = await api("/api/v1/webapp/purchases");
    purchaseHistory = data.items;
    renderRenewalActions();
    box.replaceChildren();
    const list = document.createElement("div");
    list.className = "record-list";
    for (const payment of purchaseHistory) {
      const record = document.createElement("article");
      record.className = "record-card";
      const top = document.createElement("div");
      top.className = "record-heading";
      const title = document.createElement("strong");
      title.textContent = payment.plan_name || "Заказ №" + payment.id;
      const status = document.createElement("span");
      status.className = "record-badge";
      const statuses = {succeeded:"Выдан", paid:"Оплачен", pending:"Ожидает оплаты", failed:"Ошибка", cancelled:"Отменён"};
      status.textContent = statuses[payment.status] || payment.status;
      status.dataset.status = payment.status;
      top.append(title, status);
      const details = document.createElement("span");
      details.className = "record-meta";
      const amount = payment.currency === "XTR" ? payment.amount + " ⭐"
        : payment.currency === "RUB" ? money(payment.amount) : (payment.amount / 100).toFixed(2) + " " + payment.currency;
      details.textContent = [amount, providerLabels[payment.provider] || payment.provider,
        new Date(payment.created_at).toLocaleDateString("ru-RU")].join(" · ");
      record.append(top, details);
      list.append(record);
    }
    if (!purchaseHistory.length) list.textContent = "Покупок пока нет.";
    box.append(list);
  } catch {
    box.textContent = "История покупок временно недоступна.";
  }
}

async function loadReferrals() {
  try {
    const d = await api("/api/v1/webapp/referrals");
    $("account-referral-info").textContent = "Приглашено: " + d.count + " · Бонусных дней: " + (d.reward?.days || 0)
      + (d.rewards_enabled ? " · Дни добавятся при продлении." : " · Бонусы отключены.");
    if (d.link) {
      $("account-referral-link").href = d.link;
      $("account-referral-link").textContent = d.link;
      $("copy-referral").hidden = false;
      $("share-referral").href = "https://t.me/share/url?url=" + encodeURIComponent(d.link);
      $("share-referral").hidden = false;
    }
  } catch {
    $("account-referral-info").textContent = "Реферальная информация временно недоступна.";
  }
}
const providerLabels={telegram_stars:"Telegram Stars",yookassa:"ЮKassa",yoomoney:"ЮMoney",cryptopay:"Crypto Pay",platega:"Platega"};
const rubles = new Intl.NumberFormat("ru-RU", {style: "currency", currency: "RUB", maximumFractionDigits: 2});
const money = minor => rubles.format(minor / 100);
const trafficLabel = value => value == null ? "—" : value === 0 ? "Безлимит" : value + " ГБ";
const subscriptionStatuses = {active: "Активна", disabled: "Отключена", expired: "Истекла", deleted: "Удалена"};
function openCheckoutUrl(url){
  const address = new URL(url);
  if (address.protocol !== "https:") throw Error("Некорректный адрес провайдера");
  if (address.hostname === "t.me" && tg?.openTelegramLink) tg.openTelegramLink(url);
  else if (tg?.openLink) tg.openLink(url);
  else window.open(url,"_blank","noopener,noreferrer");
}
let availablePlans = new Map();
let currentSubscription = null;
let purchaseHistory = [];
let selectedPlan = null;
let extraDevices = 0;
let orderBusy = false;
let checkout = null;
let pendingPayment = null;

async function showOrder(plan, {devices = 0, renewal = false} = {}) {
  if (orderBusy) return;
  const currentCheckout = {};
  checkout = currentCheckout;
  selectedPlan = plan;
  extraDevices = Math.min(Math.max(0, devices), plan.max_extra_devices);
  $("order-title").textContent = plan.name;
  $("order-specification").textContent = `${plan.days} дней · ${trafficLabel(plan.traffic_gb)}`;
  $("order-renewal-note").hidden = !renewal;
  $("order-renewal-note").textContent = currentSubscription?.status === "active"
    ? `Добавим ${plan.days} дн. к действующей подписке.` : `Новый срок: ${plan.days} дн.`;
  $("order-terms").checked = false;
  updateOrder();
  switchPage("order");
  try {
    const {methods, terms_url: termsUrl} = await api("/api/v1/payment-methods");
    if (checkout !== currentCheckout) return;
    Object.assign(checkout, {methods, termsUrl});
    extraDevices = Math.min(extraDevices, availableExtraDevices());
    if (extraDevices < devices) {
      $("order-renewal-note").hidden = false;
      $("order-renewal-note").textContent += " Проверьте число устройств: условия тарифа изменились.";
    }
    updateOrder();
  } catch {
    if (checkout === currentCheckout) $("order-status").textContent =
      "Не удалось загрузить способы оплаты. Вернитесь к тарифам и откройте заказ ещё раз.";
  }
}

function availableExtraDevices() {
  const methods = checkout.methods;
  if (!methods) return 0;
  const external = Object.keys(methods).some(method => method !== "telegram_stars" && methods[method]);
  return Math.max(
    external ? (selectedPlan.max_extra_devices_rub ?? selectedPlan.max_extra_devices) : 0,
    methods.telegram_stars ? (selectedPlan.max_extra_devices_stars ?? selectedPlan.max_extra_devices) : 0,
  );
}

function orderTotals() {
  const plan = selectedPlan;
  const rub = extraDevices > (plan.max_extra_devices_rub ?? plan.max_extra_devices)
    ? 0 : plan.price_rub_minor + extraDevices * (plan.extra_device_price_rub_minor ?? 0);
  const stars = extraDevices > (plan.max_extra_devices_stars ?? plan.max_extra_devices)
    || (extraDevices && plan.extra_device_price_stars == null)
    ? null : plan.price_stars + extraDevices * (plan.extra_device_price_stars ?? 0);
  return {rub, stars};
}

function updateOrder() {
  const {rub, stars} = orderTotals();
  const extraLimit = checkout.methods ? availableExtraDevices() : selectedPlan.max_extra_devices;
  $("order-extra").value = String(extraDevices);
  $("order-device-total").textContent = extraDevices
    ? `${selectedPlan.devices_included + extraDevices} · ${selectedPlan.devices_included} в тарифе`
    : String(selectedPlan.devices_included);
  $("order-unit-row").hidden = extraLimit === 0;
  $("order-extra-row").hidden = extraLimit === 0;
  $("order-minus").disabled = !checkout.methods || extraDevices === 0;
  $("order-plus").disabled = !checkout.methods || extraDevices >= extraLimit;
  const unit = $("order-unit");
  const prices = [];
  if (selectedPlan.extra_device_price_rub_minor != null) {
    prices.push(money(selectedPlan.extra_device_price_rub_minor));
  }
  if (selectedPlan.extra_device_price_stars != null) prices.push(selectedPlan.extra_device_price_stars + " ⭐");
  unit.textContent = prices.join(" / ") || "Недоступно";
  $("order-open-invoice").hidden = true;
  $("order-show-purchases").hidden = true;
  const select = $("order-method");
  const previous = select.value;
  select.replaceChildren();
  for (const [key, label] of Object.entries(providerLabels)) {
    if (!checkout.methods?.[key]) continue;
    if (key === "telegram_stars" ? stars == null || stars < 1 : rub <= 0) continue;
    select.add(new Option(label, key));
  }
  if ([...select.options].some(option => option.value === previous)) select.value = previous;
  const hasMethods = select.options.length > 0;
  $("order-payment").hidden = !hasMethods;
  $("order-consent").hidden = !checkout.termsUrl;
  $("order-terms").required = Boolean(checkout.termsUrl);
  if (checkout.termsUrl) $("order-terms-link").href = checkout.termsUrl;
  $("order-status").textContent = !checkout.methods ? "Загружаем способы оплаты…"
    : hasMethods ? "" : "Для этого заказа нет способов оплаты. Проверьте тариф или число устройств.";
  if (hasMethods) updateProviderTotal();
}

for (const [button, delta] of [["order-minus", -1], ["order-plus", 1]]) {
  $(button).onclick = () => {
    if (!selectedPlan || orderBusy || !checkout.methods) return;
    extraDevices = Math.max(0, Math.min(availableExtraDevices(), extraDevices + delta));
    updateOrder();
  };
}

function updateProviderTotal() {
  const {rub, stars} = orderTotals();
  const inStars = $("order-method").value === "telegram_stars";
  const base = inStars ? selectedPlan.price_stars : selectedPlan.price_rub_minor;
  const total = inStars ? stars : rub;
  const format = inStars ? amount => amount + " ⭐" : money;
  $("order-base-amount").textContent = format(base);
  $("order-extra-cost").hidden = extraDevices === 0;
  $("order-extra-amount").textContent = format(total - base);
  $("order-total").textContent = format(total);
}
$("order-method").onchange = updateProviderTotal;

$("order-payment").onsubmit = async event => {
  event.preventDefault();
  if (orderBusy || !selectedPlan || (checkout.termsUrl && !$("order-terms").checked)) return;
  const provider = $("order-method").value;
  if (!provider) return;
  const useStars = provider === "telegram_stars";
  const payload = {plan_id: selectedPlan.id, extra_devices: extraDevices, revision: selectedPlan.revision};
  orderBusy = true;
  $("order-pay").disabled = true;
  $("order-status").textContent = "Создаём счёт…";
  try {
    if (useStars && !tg?.openInvoice) throw Error("Откройте Mini App через Telegram");
    if (checkout.termsUrl) await api("/api/v1/webapp/terms/accept", {accepted: true});
    if (useStars) {
      const invoice = await api("/api/v1/webapp/plans/checkout", payload);
      const status = await new Promise(resolve => tg.openInvoice(invoice.invoice_url, resolve));
      $("order-status").textContent = {
        paid: "Telegram принял оплату. Статус подписки доступен в покупках.",
        cancelled: "Оплата отменена.",
        failed: "Telegram не подтвердил оплату. Попробуйте ещё раз.",
        pending: "Telegram обрабатывает платёж. Статус доступен в покупках.",
      }[status] || "Проверьте статус заказа в покупках.";
      if (status === "paid" || status === "pending") {
        pendingPayment = invoice.payment_id;
        $("order-payment").hidden = true;
        $("order-show-purchases").hidden = false;
        refreshAfterPayment();
      }
    } else {
      const invoice = await api("/api/v1/webapp/plans/external/checkout", {...payload, provider});
      if (!invoice.url.startsWith("https://")) throw Error("Некорректный адрес провайдера");
      const link = $("order-open-invoice");
      link.href = invoice.url;
      link.onclick = event => {
        if (!tg?.openLink) return;
        event.preventDefault();
        openCheckoutUrl(invoice.url);
      };
      link.hidden = false;
      $("order-payment").hidden = true;
      $("order-show-purchases").hidden = false;
      pendingPayment = invoice.payment_id;
      $("order-status").textContent = "Счёт №" + invoice.payment_id + " создан.";
      if (tg?.openLink) openCheckoutUrl(invoice.url);
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
    availablePlans = new Map(result.items.map(plan => [plan.id, plan]));
    renderRenewalActions();
    const list = $("plans-list");
    list.replaceChildren();
    for (const plan of result.items) {
      const item = document.createElement("button");
      item.type = "button";
      item.className = "panel plan-card";
      item.dataset.featured = plan.is_featured;
      const head = document.createElement("span");
      head.className = "section-title";
      const title = document.createElement("strong");
      title.className = "plan-title";
      title.textContent = plan.name;
      head.append(title);
      if (plan.badge) {
        const badge = document.createElement("span");
        badge.className = "status-pill";
        badge.textContent = plan.badge;
        head.append(badge);
      }
      const specs = document.createElement("span");
      specs.className = "plan-spec";
      specs.textContent = `${plan.days} дней · ${trafficLabel(plan.traffic_gb)} · устройств: ${plan.devices_included}`;
      item.append(head, specs);
      if (plan.description) {
        const description = document.createElement("span");
        description.className = "muted plan-description";
        description.textContent = plan.description;
        item.append(description);
      }
      const price = document.createElement("strong");
      price.className = "plan-price";
      price.textContent = plan.price_rub_minor > 0 ? money(plan.price_rub_minor) : plan.price_stars + " ⭐";
      const total = document.createElement("span");
      total.className = "plan-total";
      const arrow = document.createElement("span");
      arrow.textContent = "→";
      arrow.setAttribute("aria-hidden", "true");
      total.append(price, arrow);
      item.append(total);
      item.setAttribute("aria-label", `${plan.name}, ${price.textContent}. Открыть заказ`);
      item.onclick = () => { if (!orderBusy) showOrder(plan); };
      list.append(item);
    }
    $("plans-status").hidden = Boolean(result.items.length);
    $("plans-status").textContent = "Тарифы ещё не опубликованы. Администратор добавит варианты в панели управления.";
  } catch {
    $("plans-status").hidden = false;
    $("plans-status").textContent = "Не удалось загрузить тарифы. Попробуйте открыть магазин заново.";
  }
}
function renderProfile(profile, user) {
  const name = profile?.name || "Remnaum";
  $("store-name").textContent = name;
  $("store-mark").textContent = name.trim().charAt(0).toUpperCase() || "R";
  const description = profile?.description?.trim() || profile?.tagline?.trim();
  if (description) {
    $("store-description").textContent = description;
    $("store-description").hidden = false;
  }
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
        $("support-text").hidden = true;
      }
    } catch { /* A plain-text support contact is valid. */ }
  }
  $("profile-name").textContent = user.first_name || "Покупатель";
  $("profile-monogram").textContent = $("profile-name").textContent.trim().charAt(0).toUpperCase();
  $("profile-id").textContent = user.id;
}

function renderRenewalActions() {
  const plan = availablePlans.get(currentSubscription?.plan_id);
  $("subscription-name").textContent = plan?.name || "Доступ VPN";
  $("renew-plan").hidden = !plan;
  $("renew-plan").onclick = plan ? () => showOrder(plan, {
    devices: (currentSubscription.device_limit ?? plan.devices_included) - plan.devices_included,
    renewal: true,
  }) : null;
  const purchase = purchaseHistory.find(item => item.status === "succeeded" && availablePlans.has(item.plan_id));
  $("repeat-plan").hidden = !purchase;
  $("repeat-plan").onclick = purchase
    ? () => showOrder(availablePlans.get(purchase.plan_id), {devices: purchase.extra_devices || 0, renewal: true})
    : null;
}

async function refreshSubscription() {
  const data = await api("/api/v1/webapp/subscription");
  renderSubscription(data.subscription);
}

let refreshingPayment = false;
async function refreshAfterPayment() {
  if (!pendingPayment || refreshingPayment) return;
  refreshingPayment = true;
  try {
    const paymentId = pendingPayment;
    for (const delay of [0, 1000, 2500]) {
      if (delay) await new Promise(resolve => setTimeout(resolve, delay));
      if (pendingPayment !== paymentId) return;
      await loadPurchases();
      const payment = purchaseHistory.find(item => item.id === paymentId);
      if (payment?.status === "succeeded") {
        await refreshSubscription();
        if (pendingPayment === paymentId) pendingPayment = null;
        return;
      }
      if (["failed", "cancelled"].includes(payment?.status)) {
        pendingPayment = null;
        return;
      }
    }
  } catch {
    // The subscription and purchase screens reload on navigation.
  } finally {
    refreshingPayment = false;
  }
}
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") refreshAfterPayment();
});

function renderSubscription(subscription) {
  currentSubscription = subscription;
  $("no-subscription").hidden = Boolean(subscription);
  $("subscription").hidden = !subscription;
  $("sub-link").hidden = true;
  renderRenewalActions();
  if (!subscription) return;
  const status = subscriptionStatuses[subscription.status] || subscription.status;
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
    const data = await api("/api/v1/webapp/bootstrap");
    renderProfile(data.profile, data.user);
    renderSubscription(data.subscription);
    $("admin-entry").hidden = !data.is_admin;
    if (data.is_admin) $("admin-entry").onclick = () => window.location.assign("/admin" + window.location.hash);
    $("loading").hidden = true;
    $("app").hidden = false;
    $("bottom-nav").hidden = false;
    await loadReadyPlans();
  } catch (error) {
    $("loading").hidden = false;
    $("loading").textContent = error.message;
  }
}
start();
