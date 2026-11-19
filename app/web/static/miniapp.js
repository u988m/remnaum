const tg = window.Telegram?.WebApp;
tg?.ready?.();
tg?.expand?.();
if (tg?.isVersionAtLeast?.("6.1") ?? true) tg?.BackButton?.hide?.();
const $=id=>document.getElementById(id);
const orderDialog = $("page-order");
const deviceEditor = $("device-editor");
let awaitingBack = false;
const rootPage = new URLSearchParams(location.search).get("section") === "account" ? "account" : "vpn";
const primaryPages = new Set(["vpn", "bonuses", "devices", "account"]);
const orderScreens = new Map();
const scrollPositions = new Map();
let screen = {page: rootPage, tab: rootPage, depth: 0, id: 0};
let screenId = 0;
let navigationReady = false;

function renderScreen(next) {
  const previous = screen;
  screenId = Math.max(screenId, next.id);
  const order = next.page === "order" ? orderScreens.get(next.id) : null;
  if (next.page === "order" && !order) {
    next = {...next, page: next.origin || next.tab};
  }
  screen = next;
  const visiblePage = order ? next.origin : next.page;
  const topbar = document.querySelector(".topbar");
  topbar.hidden = visiblePage === "vpn";
  $("shop-back").hidden = !next.depth;
  if (deviceEditor.open && next.page !== "devices") deviceEditor.close();
  for (const panel of document.querySelectorAll("[data-page-panel]")) {
    panel.hidden = panel.dataset.pagePanel !== visiblePage;
  }
  for (const button of document.querySelectorAll("#bottom-nav [data-page]")) {
    if (button.dataset.page === next.tab) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  }
  if (order) {
    renderOrder(order);
    if (!orderDialog.open) orderDialog.showModal();
    $("order-body").scrollTop = 0;
  } else if (orderDialog.open) {
    orderDialog.close();
  }
  if (!order && previous.page !== next.page) {
    window.scrollTo(0, scrollPositions.get(next.id) || 0);
    if (previous.origin !== next.page) {
      if (next.page === "purchases") loadPurchases();
      if (next.page === "devices") loadDevices();
      if (next.page === "vpn") refreshSubscription().catch(() => {});
      if (next.page === "bonuses") { loadOffers(); loadReferrals(); }
    }
  }
}

function switchPage(page, order = null) {
  if (!navigationReady || (page === screen.page && !order)) return;
  if (!orderDialog.open) scrollPositions.set(screen.id, window.scrollY);
  const next = {
    page, id: ++screenId, depth: screen.depth + 1, tab: primaryPages.has(page) ? page : screen.tab,
  };
  if (order) {
    next.origin = screen.page;
    orderScreens.set(next.id, order);
  }
  history.pushState({miniShop: next}, "");
  renderScreen(next);
  tg?.HapticFeedback?.selectionChanged?.();
}

function goBack() {
  if (!screen.depth || awaitingBack) return;
  awaitingBack = true;
  history.back();
}

for (const button of document.querySelectorAll("[data-page], [data-go-page]")) {
  button.addEventListener("click", () => switchPage(button.dataset.page || button.dataset.goPage));
}
$("shop-back").onclick = $("order-close").onclick = goBack;
orderDialog.addEventListener("cancel", event => { event.preventDefault(); goBack(); });
orderDialog.addEventListener("click", event => { if (event.target === orderDialog) goBack(); });
window.addEventListener("popstate", event => {
  awaitingBack = false;
  if (!navigationReady || !event.state?.miniShop) return;
  if (!orderDialog.open) scrollPositions.set(screen.id, window.scrollY);
  renderScreen(event.state.miniShop);
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

async function api(path, body, method = body ? "POST" : "GET") {
  const response = await fetch(path, {
    method,
    headers: {"Content-Type":"application/json", "X-Telegram-Init-Data":tg?.initData || ""},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await response.json();
  if (!response.ok) throw Error(typeof data.detail === "string" ? data.detail : "Ошибка запроса");
  return data;
}
async function loadOffers(){
  try{
    const d=await api("/api/v1/webapp/offers");
    if(d.trial.enabled&&d.trial.eligible){
      $("trial-offer").hidden = false;
      $("trial-copy").textContent=d.trial.days+" дней · "+d.trial.traffic_gb+" ГБ · "+d.trial.device_limit+" устройств";
    }else{$("trial-offer").hidden = true}
    $("promo-balance").textContent = d.promo_balance.days
      ? countWith(d.promo_balance.days, dayWords) + " к следующей подписке"
      : "Добавляет дни к следующей оплате";
  }catch(e){$("promo-balance").textContent="Бонусы пока недоступны"}
}
$("trial-claim").onclick=async()=>{
  $("trial-claim").disabled=true;
  try{const d=await api("/api/v1/webapp/trial",{});
    $("trial-status").textContent="Доступ активирован на "+d.days+" дней";
    location.reload();
  }catch(e){$("trial-status").textContent=e.message;$("trial-claim").disabled=false}
};
$("promo-form").onsubmit=async event=>{
  event.preventDefault();
  const code=$("promo-input").value.trim();
  $("promo-activate").disabled=true;
  try{const d=await api("/api/v1/webapp/promos/redeem",{code});
    $("promo-status").textContent="Готово! Начислено +"+d.reward.bonus_days+" дней.";
    $("promo-input").value="";await loadOffers();
  }catch(e){$("promo-status").textContent=e.message}
  $("promo-activate").disabled=false;
};
let deviceAction = null;
let savingDevice = false;
let deviceListRequest = 0;

function openDeviceEditor(device, remove) {
  if (savingDevice || deviceEditor.open) return;
  deviceAction = {device, remove};
  $("device-editor-title").textContent = remove ? "Удалить устройство?" : "Переименовать устройство";
  $("device-editor-description").textContent = remove
    ? "Устройство «" + device.name + "» будет удалено из списка. При новом подключении оно может появиться снова."
    : "Пустое поле возвращает название модели.";
  $("device-name-field").hidden = remove;
  $("device-name-input").value = device.alias || "";
  $("device-name-input").placeholder = device.model || device.name;
  $("device-editor-status").textContent = "";
  $("device-editor-submit").textContent = remove ? "Удалить" : "Сохранить";
  $("device-editor-submit").className = remove ? "secondary-btn danger-btn" : "primary-btn";
  deviceEditor.showModal();
  if (!remove) $("device-name-input").focus();
}

$("device-editor-cancel").onclick = () => deviceEditor.close();
deviceEditor.addEventListener("close", () => {
  deviceAction = null;
});

$("device-form").onsubmit = async event => {
  event.preventDefault();
  if (!deviceAction || savingDevice) return;
  const {device, remove} = deviceAction;
  const path = "/api/v1/webapp/devices/" + encodeURIComponent(device.id);
  savingDevice = true;
  $("device-editor-submit").disabled = true;
  $("device-editor-status").textContent = "";
  try {
    if (remove) await api(path, undefined, "DELETE");
    else await api(path, {name:$("device-name-input").value.trim()}, "PATCH");
    if (deviceEditor.open) deviceEditor.close();
    $("device-status").textContent = remove ? "Устройство удалено." : "Имя сохранено.";
    await loadDevices();
  } catch (error) {
    $(deviceEditor.open ? "device-editor-status" : "device-status").textContent = error.message;
  } finally {
    savingDevice = false;
    $("device-editor-submit").disabled = false;
  }
};

async function loadDevices() {
  const box = $("account-device-info");
  const refresh = $("devices-refresh"), capacity = $("device-capacity");
  const request = ++deviceListRequest;
  refresh.disabled = true;
  try {
    const data = await api("/api/v1/webapp/devices");
    if (request !== deviceListRequest) return;
    box.replaceChildren();
    const count = data.total ?? data.items.length;
    $("device-count").textContent = data.limit ? count + " из " + data.limit : count + " подключено";
    capacity.hidden = !data.limit;
    if (data.limit) {
      capacity.max = data.limit;
      capacity.value = count;
    }
    for (const device of data.items) {
      const record = document.createElement("article");
      record.className = "panel device-card";
      const title = document.createElement("strong");
      title.textContent = device.name;
      record.append(title);
      const platform = [device.platform, device.os_version].filter(Boolean).join(" ");
      const details = [device.alias && device.model !== device.name ? device.model : "", platform].filter(Boolean);
      if (details.length) {
        const detail = document.createElement("p");
        detail.className = "muted";
        detail.textContent = details.join(" · ");
        record.append(detail);
      }
      const client = document.createElement("p");
      client.className = "device-client";
      client.textContent = device.client ? "Клиент · " + device.client : "Клиент не определён";
      record.append(client);
      if (device.id) {
        const actions = document.createElement("div");
        actions.className = "device-actions";
        for (const [label, remove] of [["Переименовать", false], ["Удалить", true]]) {
          const button = document.createElement("button");
          button.type = "button";
          button.className = remove ? "secondary-btn danger-btn" : "secondary-btn";
          button.textContent = label;
          button.setAttribute("aria-label", label + " " + device.name);
          button.onclick = () => openDeviceEditor(device, remove);
          actions.append(button);
        }
        record.append(actions);
      }
      box.append(record);
    }
    if (!data.items.length) box.textContent = data.has_subscription === false
      ? "Устройства появятся после подключения VPN." : "Подключённых устройств пока нет.";
  } catch (error) {
    if (request !== deviceListRequest) return;
    capacity.hidden = true;
    box.replaceChildren();
    $("device-count").textContent = error.message || "Информация об устройствах временно недоступна.";
  } finally {
    if (request === deviceListRequest) refresh.disabled = false;
  }
}
$("devices-refresh").onclick = () => {
  $("device-status").textContent = "";
  loadDevices();
};

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
    $("account-referral-info").textContent = d.rewards_enabled
      ? (d.reward_days ? "За первую оплату друга начисляется " + d.reward_days + " дн." : "Дни начисляются после первой оплаты друга.")
      : "Реферальные бонусы отключены.";
    $("referral-count").textContent = d.count;
    $("referral-earned").textContent = d.reward?.earned || 0;
    $("referral-days").textContent = d.reward?.days || 0;
    $("referral-metrics").hidden = false;
    if (d.link) {
      $("account-referral-link").href = d.link;
      $("account-referral-link").textContent = d.link;
      $("account-referral-link").hidden = false;
      $("copy-referral").hidden = false;
      $("share-referral").href = "https://t.me/share/url?url=" + encodeURIComponent(d.link);
      $("share-referral").hidden = false;
    }
  } catch {
    $("account-referral-info").textContent = "Реферальная информация временно недоступна.";
  }
}
const providerLabels={telegram_stars:"Telegram Stars",yookassa:"ЮKassa",yoomoney:"ЮMoney",cryptopay:"Crypto Pay",platega:"Platega"};
const rubles = new Intl.NumberFormat("ru-RU", {style: "currency", currency: "RUB", minimumFractionDigits: 0});
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
let pendingPayment = null;

const plurals = new Intl.PluralRules("ru");
const deviceWords = {one: "устройство", few: "устройства", many: "устройств", other: "устройства"};
const dayWords = {one: "день", few: "дня", many: "дней", other: "дня"};
const countWith = (count, words) => count + " " + words[plurals.select(count)];
const providerMarks = {telegram_stars: "★", yookassa: "ЮК", yoomoney: "ЮМ", cryptopay: "₿", platega: "P"};
const providerAmount = (provider, amount) => provider === "telegram_stars"
  ? amount.toLocaleString("ru-RU") + " ⭐" : money(amount);
const currentOrder = () => screen.page === "order" ? orderScreens.get(screen.id) : null;

function showOrder(plan, {devices = 0, renewal = false, upgrade = false} = {}) {
  const order = {
    plan, devices: Math.min(Math.max(0, devices), plan.max_extra_devices), renewal, upgrade,
    minDevices: upgrade ? devices : 0, termsAccepted: false, provider: "",
  };
  if (renewal) {
    const extendsActive = currentSubscription?.status === "active";
    const duration = countWith(plan.days, dayWords);
    order.note = extendsActive ? "+" + duration + " к текущему сроку" : "Новый срок — " + duration;
  }
  $("order-method").open = false;
  switchPage("order", order);
  loadOrderMethods(order);
}

async function loadOrderMethods(order) {
  order.methodError = false;
  if (order === currentOrder()) renderOrder(order);
  try {
    const {methods, terms_url: termsUrl, presentation} = await api("/api/v1/payment-methods");
    order.methods = methods;
    order.labels = presentation ? Object.fromEntries(presentation.map(item => [item.provider, item.label])) : providerLabels;
    order.termsUrl = termsUrl;
    const limit = availableExtraDevices(order);
    if (!order.upgrade && order.devices > limit) {
      order.devices = limit;
      order.note = [order.note, "Лимит изменился. Проверьте число устройств."].filter(Boolean).join(". ");
    }
  } catch {
    order.methodError = true;
  }
  if (order === currentOrder()) renderOrder(order);
}

function availableExtraDevices({plan, methods}) {
  if (!methods) return plan.max_extra_devices;
  const rublesAvailable = Object.keys(providerLabels).some(key => key !== "telegram_stars" && methods[key]);
  return Math.max(
    rublesAvailable ? (plan.max_extra_devices_rub ?? plan.max_extra_devices) : 0,
    methods.telegram_stars ? (plan.max_extra_devices_stars ?? plan.max_extra_devices) : 0,
  );
}

function orderTotals({plan, devices}) {
  const rub = devices > (plan.max_extra_devices_rub ?? plan.max_extra_devices)
    ? 0 : plan.price_rub_minor + devices * (plan.extra_device_price_rub_minor ?? 0);
  const stars = devices > (plan.max_extra_devices_stars ?? plan.max_extra_devices)
    || (devices && plan.extra_device_price_stars == null)
    ? null : plan.price_stars + devices * (plan.extra_device_price_stars ?? 0);
  return {rub, stars};
}

function renderPaymentMethods(order, methods, totals) {
  const options = $("order-method-options");
  const signature = methods.join(",");
  if (options.dataset.methods !== signature) {
    options.dataset.methods = signature;
    options.replaceChildren();
    for (const provider of methods) {
      const option = document.createElement("label");
      option.className = "payment-choice";
      const radio = document.createElement("input");
      radio.type = "radio";
      radio.name = "payment-provider";
      radio.value = provider;
      const mark = document.createElement("span");
      mark.className = "payment-choice-mark";
      mark.textContent = providerMarks[provider];
      mark.setAttribute("aria-hidden", "true");
      const name = document.createElement("strong");
      name.className = "payment-choice-name";
      const price = document.createElement("span");
      price.className = "payment-choice-price";
      option.append(mark, name, price, radio);
      radio.onchange = () => {
        const active = currentOrder();
        active.provider = provider;
        renderOrder(active);
        $("order-method").open = false;
        $("order-method").querySelector("summary").focus({preventScroll:true});
      };
      options.append(option);
    }
  }
  for (const option of options.children) {
    const radio = option.querySelector("input");
    option.querySelector(".payment-choice-name").textContent = order.labels[radio.value] || providerLabels[radio.value];
    radio.checked = radio.value === order.provider;
    radio.disabled = Boolean(order.busy);
    option.querySelector(".payment-choice-price").textContent =
      providerAmount(radio.value, radio.value === "telegram_stars" ? totals.stars : totals.rub);
  }
}

function renderOrder(order) {
  const {plan, devices} = order;
  const limit = availableExtraDevices(order);
  const totals = orderTotals(order);
  const methods = limit < order.minDevices ? [] : Object.keys(order.labels || providerLabels).filter(key =>
    order.methods?.[key] && (key === "telegram_stars" ? totals.stars >= 1 : totals.rub > 0)
  );
  if (!methods.includes(order.provider)) order.provider = methods[0] || "";
  const canPay = Boolean(order.provider);
  const unit = order.provider === "telegram_stars" ? plan.extra_device_price_stars : plan.extra_device_price_rub_minor;
  const newSlots = order.upgrade ? devices - order.minDevices + 1 : devices;
  const deviceLimit = plan.devices_included + devices;
  $("order-kind").textContent = order.upgrade ? "Продление с устройствами" : order.renewal ? "Продление" : "Подписка";
  $("order-title").textContent = plan.name;
  $("order-specification").textContent = countWith(plan.days, dayWords) + " · " + trafficLabel(plan.traffic_gb);
  $("order-renewal-note").textContent = order.note || "";
  $("order-renewal-note").hidden = !order.note;
  $("order-device-total").textContent = order.upgrade
    ? `${plan.devices_included + order.minDevices - 1} → ${deviceLimit}` : String(deviceLimit);
  $("order-extra-row").hidden = !limit || Boolean(order.invoice);
  $("order-devices-label").textContent = order.upgrade ? "Новые устройства" : "Доп. устройства";
  $("order-extra").value = newSlots ? "+" + countWith(newSlots, deviceWords) : "0";
  $("order-minus").disabled = !order.methods || order.busy || devices <= order.minDevices;
  $("order-plus").disabled = !order.methods || order.busy || devices >= limit;
  $("order-unit").hidden = !canPay || unit == null;
  if (canPay && unit != null) {
    $("order-unit").textContent = order.upgrade
      ? "Все доп. места в заказе · " + providerAmount(order.provider, unit * devices)
      : providerAmount(order.provider, unit) + " за устройство";
  }
  renderPaymentMethods(order, methods, totals);
  $("order-payment").hidden = !canPay || Boolean(order.invoice);
  $("order-method").classList.toggle("single-choice", methods.length === 1);
  if (methods.length === 1) $("order-method").open = false;
  $("order-method-mark").textContent = providerMarks[order.provider] || "";
  $("order-method-name").textContent = order.labels?.[order.provider] || "";
  $("order-total").textContent = canPay ? providerAmount(order.provider,
    order.provider === "telegram_stars" ? totals.stars : totals.rub) : "";
  $("order-consent").hidden = !order.termsUrl;
  $("order-terms").required = Boolean(order.termsUrl);
  $("order-terms").checked = order.termsAccepted;
  $("order-terms").disabled = Boolean(order.busy);
  $("order-pay").disabled = Boolean(order.busy) || Boolean(order.termsUrl && !order.termsAccepted);
  if (order.termsUrl) $("order-terms-link").href = order.termsUrl;
  $("order-status").textContent = order.message || (order.methodError ? "Не удалось загрузить способы оплаты."
    : !order.methods ? "Загружаем способы оплаты…"
    : canPay ? "" : order.upgrade ? "Для увеличения лимита нет доступного способа оплаты."
    : "Для этого заказа нет доступного способа оплаты.");
  $("order-retry-methods").hidden = !order.methodError;
  $("invoice-actions").hidden = !order.invoice;
  $("order-open-invoice").hidden = !order.invoice?.url;
  if (order.invoice?.url) $("order-open-invoice").href = order.invoice.url;
}

for (const [button, delta] of [["order-minus", -1], ["order-plus", 1]]) {
  $(button).onclick = () => {
    const order = currentOrder();
    if (!order?.methods || order.busy) return;
    order.devices = Math.max(order.minDevices, Math.min(availableExtraDevices(order), order.devices + delta));
    order.message = "";
    renderOrder(order);
  };
}
$("order-method").querySelector("summary").onclick = event => {
  if ($("order-method").classList.contains("single-choice") || currentOrder()?.busy) event.preventDefault();
};
$("order-terms").onchange = event => {
  const order = currentOrder();
  order.termsAccepted = event.target.checked;
  renderOrder(order);
};
$("order-retry-methods").onclick = () => loadOrderMethods(currentOrder());
$("order-open-invoice").onclick = event => {
  if (!tg?.openLink) return;
  event.preventDefault();
  openCheckoutUrl(currentOrder().invoice.url);
};

$("order-payment").onsubmit = async event => {
  event.preventDefault();
  const order = currentOrder();
  if (!order?.provider || order.busy || order.invoice || (order.termsUrl && !order.termsAccepted)) return;
  const {provider, plan, devices} = order;
  const payload = {plan_id: plan.id, extra_devices: devices, revision: plan.revision};
  order.busy = true;
  order.message = "Создаём счёт…";
  $("order-method").open = false;
  renderOrder(order);
  try {
    if (provider === "telegram_stars" && !tg?.openInvoice) throw Error("Оплата Stars доступна в Telegram.");
    if (order.termsUrl) await api("/api/v1/webapp/terms/accept", {accepted: true});
    if (provider === "telegram_stars") {
      const invoice = await api("/api/v1/webapp/plans/checkout", payload);
      if (order !== currentOrder()) {
        order.message = "Заказ закрыт. Для оплаты откройте его снова.";
        return;
      }
      const status = await new Promise(resolve => tg.openInvoice(invoice.invoice_url, resolve));
      order.message = {
        paid: "Платёж принят. Статус заказа появится в покупках.",
        cancelled: "Оплата отменена.",
        failed: "Telegram не подтвердил оплату. Попробуйте ещё раз.",
        pending: "Telegram обрабатывает платёж.",
      }[status] || "Проверьте статус заказа в покупках.";
      if (status === "paid" || status === "pending") {
        order.invoice = invoice;
        pendingPayment = invoice.payment_id;
        refreshAfterPayment();
      }
    } else {
      const invoice = await api("/api/v1/webapp/plans/external/checkout", {...payload, provider});
      if (new URL(invoice.url).protocol !== "https:") throw Error("Некорректный адрес провайдера");
      order.invoice = invoice;
      pendingPayment = invoice.payment_id;
      order.message = "Счёт №" + invoice.payment_id + " создан.";
      if (order === currentOrder() && tg?.openLink) openCheckoutUrl(invoice.url);
    }
  } catch (error) {
    order.message = error.message;
  } finally {
    order.busy = false;
    if (order === currentOrder()) renderOrder(order);
  }
};

async function loadReadyPlans() {
  try {
    const [result, paymentInfo] = await Promise.all([
      api("/api/v1/plans"), api("/api/v1/payment-methods").catch(() => null),
    ]);
    const methods = paymentInfo?.methods;
    const rublesFirst = paymentInfo?.presentation?.[0]?.provider !== "telegram_stars";
    const rublesAvailable = !methods || Object.keys(providerLabels).some(key => key !== "telegram_stars" && methods[key]);
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
      specs.textContent = `${countWith(plan.days, dayWords)} · ${trafficLabel(plan.traffic_gb)} · ${countWith(plan.devices_included, deviceWords)}`;
      item.append(head, specs);
      if (plan.description) {
        const description = document.createElement("span");
        description.className = "muted plan-description";
        description.textContent = plan.description;
        item.append(description);
      }
      const prices = [];
      if (rublesAvailable && plan.price_rub_minor > 0) prices.push(money(plan.price_rub_minor));
      if ((!methods || methods.telegram_stars) && plan.price_stars > 0) {
        const stars = providerAmount("telegram_stars", plan.price_stars);
        if (rublesFirst) prices.push(stars);
        else prices.unshift(stars);
      }
      const total = document.createElement("span");
      total.className = "plan-total";
      if (!prices.length) {
        const notice = document.createElement("span");
        notice.textContent = "Оплата недоступна";
        total.append(notice);
      }
      prices.forEach((amount, index) => {
        const price = document.createElement(index ? "span" : "strong");
        if (!index) price.className = "plan-price";
        price.textContent = amount;
        total.append(price);
      });
      const arrow = document.createElement("span");
      arrow.textContent = "→";
      arrow.setAttribute("aria-hidden", "true");
      total.append(arrow);
      item.append(total);
      item.onclick = () => showOrder(plan);
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
  $("landing-name").textContent = name;
  const mark = name.trim().charAt(0).toUpperCase() || "R";
  $("store-mark").textContent = mark;
  $("landing-mark").textContent = mark;
  const tagline = profile?.tagline?.trim();
  $("home-message").textContent = tagline || "";
  $("home-message").hidden = !tagline;
  const instructions = profile?.connection_instructions?.trim();
  $("custom-guide").textContent = instructions || "";
  $("custom-guide").hidden = !instructions;
  $("default-guide").hidden = Boolean(instructions);
  const description = profile?.description?.trim();
  if (description) {
    $("store-description").textContent = description;
    $("store-description").hidden = false;
  }
  for (const [id, url] of [["store-logo", profile?.logo_image_url], ["landing-logo", profile?.logo_image_url]]) {
    if (url) {
      $(id).src = url;
      $(id).hidden = false;
    }
  }
  $("store-mark").hidden = Boolean(profile?.logo_image_url);
  $("landing-mark").hidden = Boolean(profile?.logo_image_url);
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
  if (user.photo_url) {
    const photo = $("profile-photo");
    photo.onload = () => { photo.hidden = false; $("profile-monogram").hidden = true; };
    photo.onerror = () => { photo.hidden = true; $("profile-monogram").hidden = false; };
    photo.src = user.photo_url;
  }
}

function renderRenewalActions() {
  const plan = availablePlans.get(currentSubscription?.plan_id);
  $("subscription-name").textContent = plan?.name || "Доступ VPN";
  $("renew-plan").hidden = !plan;
  $("renew-plan").onclick = plan ? () => showOrder(plan, {
    devices: (currentSubscription.device_limit ?? plan.devices_included) - plan.devices_included,
    renewal: true,
  }) : null;
  const purchasedSlots = plan ? Math.max(0, (currentSubscription.device_limit ?? plan.devices_included) - plan.devices_included) : 0;
  $("device-upgrade").hidden = !plan || currentSubscription.status !== "active" || purchasedSlots >= plan.max_extra_devices;
  $("device-upgrade").onclick = plan ? () => showOrder(plan, {devices: purchasedSlots + 1, renewal: true, upgrade: true}) : null;
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
    if (data.is_admin) $("admin-entry").onclick = () => window.location.assign("/admin?from=profile" + window.location.hash);
    $("loading").hidden = true;
    $("app").hidden = false;
    $("bottom-nav").hidden = false;
    const restoredScreen = history.state?.miniShop;
    if (restoredScreen) screenId = restoredScreen.id;
    else history.replaceState({miniShop: screen}, "");
    history.scrollRestoration = "manual";
    navigationReady = true;
    renderScreen(restoredScreen || screen);
    await loadReadyPlans();
  } catch (error) {
    $("loading").hidden = false;
    $("loading").textContent = error.message;
  }
}
start();
