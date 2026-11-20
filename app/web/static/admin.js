const tg=window.Telegram?.WebApp;tg?.ready?.();tg?.expand?.();const $=id=>document.getElementById(id);let userOffset=0,paymentOffset=0;const pageSize=20;
if (tg?.isVersionAtLeast?.("6.1") ?? true) tg?.BackButton?.hide?.();
async function api(path){const r=await fetch(path,{headers:{"X-Telegram-Init-Data":tg?.initData||""}});const d=await r.json();if(!r.ok)throw Error(d.detail||"Ошибка запроса");return d}
function cell(value){const td=document.createElement("td");td.textContent=value??"—";return td}
function row(target,values){const tr=document.createElement("tr");values.forEach(v=>tr.appendChild(cell(v)));$(target).appendChild(tr)}
const dashNumber = new Intl.NumberFormat("ru-RU");
const dashRubles = new Intl.NumberFormat("ru-RU", {style: "currency", currency: "RUB"});
const dashDate = new Intl.DateTimeFormat("ru-RU", {day: "numeric", month: "short", timeZone: "UTC"});
let revenueDays = [];
let revenuePeriod = 14;

function setDailyRevenue() {
  const item = revenueDays.find(day => day.date === $("revenue-day").value);
  $("revenue-day-amount").value = item ? dashRubles.format(item.amount_minor / 100) : "—";
}
$("revenue-day").onchange = setDailyRevenue;

function renderRevenue() {
  const points = revenueDays.slice(-revenuePeriod);
  const peak = Math.max(1, ...points.map(point => point.amount_minor));
  const chart = $("revenue-chart"), day = $("revenue-day");
  chart.replaceChildren();
  chart.style.gridTemplateColumns = `repeat(${Math.max(1, points.length)}, minmax(0, 1fr))`;
  day.replaceChildren();
  for (const point of points) {
    const label = dashDate.format(new Date(point.date + "T00:00:00Z"));
    const bar = document.createElement("span");
    bar.style.height = Math.max(2, 100 * point.amount_minor / peak) + "%";
    bar.title = label + " · " + dashRubles.format(point.amount_minor / 100);
    chart.append(bar);
    day.add(new Option(label, point.date));
  }
  if (points.length) day.value = points.at(-1).date;
  day.disabled = !points.length;
  $("overview-period-total").textContent = points.length
    ? dashRubles.format(points.reduce((sum, point) => sum + point.amount_minor, 0) / 100) : "—";
  setDailyRevenue();
}
for (const button of document.querySelectorAll("[data-revenue-period]")) {
  button.onclick = () => {
    revenuePeriod = Number(button.dataset.revenuePeriod);
    for (const item of document.querySelectorAll("[data-revenue-period]")) {
      item.setAttribute("aria-pressed", String(item === button));
    }
    renderRevenue();
  };
}

function renderOverview(data) {
  for (const [id, value] of Object.entries({
    "users-count": data.users, "active-count": data.active_subscriptions,
    "inactive-count": Math.max(0, data.users - data.active_subscriptions),
    "paid-count": data.successful_payments, "pending-count": data.pending_payments,
  })) $(id).textContent = dashNumber.format(value);
  $("stars-count").textContent = dashNumber.format(data.stars_received) + " ⭐";
  $("revenue-count").textContent = dashRubles.format(data.revenue_rub_minor / 100);
  revenueDays = data.revenue_days || [];
  renderRevenue();
  const list = $("overview-plans");
  list.replaceChildren();
  for (const plan of data.active_plans || []) {
    const row = document.createElement("div");
    row.className = "dashboard-line";
    const name = document.createElement("strong");
    name.textContent = plan.name;
    const count = document.createElement("span");
    count.textContent = dashNumber.format(plan.count);
    row.append(name, count);
    list.append(row);
  }
  if (!list.childElementCount) list.textContent = "Активных подписок пока нет.";
}

function renderRecentPayments(items) {
  const list = $("dashboard-payments");
  list.replaceChildren();
  for (const payment of items) {
    const card = document.createElement("article");
    card.className = "overview-payment";
    const heading = document.createElement("div");
    heading.className = "dashboard-line";
    const title = document.createElement("strong");
    title.textContent = "Заказ №" + payment.id;
    const status = document.createElement("span");
    status.className = "status-chip";
    status.dataset.status = payment.status;
    status.textContent = paymentStatus(payment.status);
    heading.append(title, status);
    const detail = document.createElement("p");
    detail.className = "muted";
    detail.textContent = [
      payment.username ? "@" + payment.username : payment.telegram_id ? "Telegram " + payment.telegram_id : "Аккаунт удалён",
      new Date(payment.created_at).toLocaleString("ru-RU"),
    ].join(" · ");
    const summary = document.createElement("div");
    summary.className = "dashboard-line payment-total";
    const method = document.createElement("span");
    method.className = "muted";
    method.textContent = [
      providerLabels[payment.provider] || payment.provider,
      payment.plan_name || (payment.duration_days ? payment.duration_days + " дн." : ""),
    ].filter(Boolean).join(" · ");
    const amount = document.createElement("strong");
    amount.textContent = formatPaymentAmount(payment);
    summary.append(method, amount);
    card.append(heading, detail, summary);
    list.append(card);
  }
  if (!items.length) list.textContent = "Платежей пока нет.";
}

function formatBytes(value) {
  const bytes = Number(value);
  if (value == null || value === "" || !Number.isFinite(bytes) || bytes < 0) return "—";
  const unit = Math.min(4, Math.floor(Math.log2(Math.max(1, bytes)) / 10));
  return new Intl.NumberFormat("ru-RU", {maximumFractionDigits: 2}).format(bytes / 1024 ** unit)
    + " " + ["Б", "КиБ", "МиБ", "ГиБ", "ТиБ"][unit];
}

function renderPanel(data) {
  const status = $("panel-status"), stats = $("panel-metrics"), list = $("panel-nodes");
  stats.replaceChildren();
  list.replaceChildren();
  stats.hidden = data.status !== "available";
  status.hidden = !stats.hidden;
  $("panel-version").hidden = stats.hidden || !data.version;
  $("panel-version").textContent = data.version ? "v" + data.version.replace(/^v/, "") : "";
  if (stats.hidden) {
    status.textContent = data.status === "not_configured" ? "Панель не подключена." : "Данные панели временно недоступны.";
    return;
  }
  for (const [name, value] of Object.entries({
    "Онлайн": data.online, "Активных": data.active, "Всего в панели": data.users,
    "Истекло": data.expired, "Отключено": data.disabled, "Достигли лимита": data.limited,
    "Нод онлайн": data.nodes_online, "Память": data.memory_percent == null ? null : data.memory_percent + "%",
    "Трафик за 7 дней": formatBytes(data.traffic_7_days), "Трафик за 30 дней": formatBytes(data.traffic_30_days),
  })) {
    const block = document.createElement("div");
    block.className = "overview-stat";
    const title = document.createElement("dt");
    title.textContent = name;
    const number = document.createElement("dd");
    number.textContent = value ?? "—";
    block.append(title, number);
    stats.append(block);
  }
  for (const node of data.nodes || []) {
    const line = document.createElement("div"), label = document.createElement("strong"), value = document.createElement("span");
    line.className = "dashboard-line";
    label.textContent = node.name || "Нода";
    value.textContent = "Онлайн: " + (node.online ?? "—");
    line.append(label, value);
    list.append(line);
  }
}

async function overview() {
  const refresh = $("overview-refresh");
  if (refresh.disabled) return;
  refresh.disabled = true;
  try {
    await Promise.all([
      api("/admin/api/overview").then(renderOverview).catch(error => {
        $("status").textContent = error.message;
      }),
      api("/admin/api/payments?limit=5&offset=0").then(data => renderRecentPayments(data.items)).catch(() => {
        $("dashboard-payments").textContent = "Не удалось загрузить последние платежи.";
      }),
      api("/admin/api/panel-overview").then(renderPanel).catch(() => renderPanel({status: "unavailable"})),
    ]);
  } finally {
    refresh.disabled = false;
  }
}
$("overview-refresh").onclick = overview;
let customer = null;
let customerPlans = new Map();
let openingCustomer = 0;
let savingCustomer = false;
const customerPath = id => "/admin/api/users/" + encodeURIComponent(id);
const subscriptionLabels = {active:"Активна", disabled:"Отключена", expired:"Истекла", deleted:"Удалена", pending:"Ожидает"};

let userSearch = "";
let userListRequest = 0;
let customerPhoto = null;

function putAvatar(element, url, fallback) {
  const initial = document.createElement("span");
  initial.textContent = fallback.trim().charAt(0).toUpperCase();
  element.replaceChildren(initial);
  if (url) {
    const photo = new Image();
    photo.alt = "";
    photo.referrerPolicy = "no-referrer";
    photo.onload = () => element.contains(initial) && element.replaceChildren(photo);
    photo.src = url;
  }
}

async function showCustomerAvatar(data, request, name) {
  const element = $("customer-avatar");
  if (customerPhoto) URL.revokeObjectURL(customerPhoto);
  customerPhoto = null;
  putAvatar(element, data.photo_url, name);
  if (data.photo_url) return;
  try {
    const response = await fetch(customerPath(data.telegram_id) + "/avatar", {
      headers: {"X-Telegram-Init-Data": tg?.initData || ""},
    });
    if (!response.ok) return;
    const photo = await response.blob();
    if (request !== openingCustomer) return;
    customerPhoto = URL.createObjectURL(photo);
    putAvatar(element, customerPhoto, name);
  } catch { /* An unavailable Telegram photo keeps the initial. */ }
}

async function loadUsers() {
  const request = ++userListRequest;
  const params = new URLSearchParams({limit:pageSize, offset:userOffset, q:userSearch});
  const data = await api("/admin/api/users?" + params);
  if (request !== userListRequest) return;
  if (userOffset && !data.items.length) {
    const lastOffset = Math.max(0, Math.floor((data.total - 1) / pageSize) * pageSize);
    if (lastOffset < userOffset) {
      userOffset = lastOffset;
      return loadUsers();
    }
  }
  $("users-table").replaceChildren();
  for (const user of data.items) {
    const name = user.display_name || (user.username ? "@" + user.username : "—");
    row("users-table", [
      user.telegram_id, name,
      user.blocked ? "Заблокирован" : (subscriptionLabels[user.subscription_status] || user.subscription_status),
      user.expires_at ? new Date(user.expires_at).toLocaleDateString() : "—",
      new Date(user.created_at).toLocaleDateString(),
    ]);
    const button = document.createElement("button");
    button.type = "button";
    button.className = "action";
    button.textContent = "Управлять";
    button.onclick = () => openTab("users", {panel: "customer", record: user.telegram_id});
    const identity = document.createElement("div");
    identity.className = "customer-identity";
    const avatar = document.createElement("span");
    avatar.className = "viewer-avatar table-avatar";
    avatar.setAttribute("aria-hidden", "true");
    putAvatar(avatar, user.photo_url, name.replace(/^@/, ""));
    const label = document.createElement("span");
    label.textContent = name;
    identity.append(avatar, label);
    $("users-table").lastElementChild.cells[1].replaceChildren(identity);
    const action = cell("");
    action.append(button);
    $("users-table").lastElementChild.append(action);
  }
  $("users-page").textContent = data.total
    ? (userOffset + 1) + "–" + Math.min(userOffset + data.items.length, data.total) + " из " + data.total
    : userSearch ? "Клиенты не найдены" : "Клиентов пока нет";
  $("users-prev").disabled = userOffset === 0;
  $("users-next").disabled = userOffset + pageSize >= data.total;
}

function localDateTime(date) {
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

function fillSubscriptionPlan() {
  const plan = customerPlans.get(Number($("customer-plan").value));
  if (!plan) return;
  $("customer-traffic").value = plan.traffic_gb;
  $("customer-devices").value = plan.devices_included;
  if (!$("customer-expiry").value) {
    $("customer-expiry").value = localDateTime(new Date(Date.now() + plan.days * 86400000));
  }
}
$("customer-plan").onchange = fillSubscriptionPlan;

async function showCustomer(id, {scroll = true} = {}) {
  const request = ++openingCustomer;
  if (scroll) {
    customer = null;
    $("customer-detail").hidden = true;
    $("status").textContent = "Загружаем карточку клиента…";
  }
  try {
    const [data, plans] = await Promise.all([api(customerPath(id)), api("/admin/api/plans")]);
    if (request !== openingCustomer) return;
    customer = data;
    customerPlans = new Map(plans.items.map(plan => [plan.id, plan]));
    const subscription = data.subscriptions[0];
    const name = data.display_name || (data.username ? "@" + data.username : "Клиент");
    $("customer-title").textContent = name;
    showCustomerAvatar(data, request, name.replace(/^@/, ""));
    $("customer-summary").textContent = "Telegram ID " + data.telegram_id + " · "
      + (data.blocked ? "Заблокирован" : "Доступ к магазину открыт");
    $("customer-name").value = data.display_name || "";
    $("customer-block").textContent = data.blocked ? "Разблокировать" : "Заблокировать";
    $("customer-block").hidden = data.is_admin;
    $("customer-delete-open").hidden = data.is_admin;
    $("customer-delete-form").hidden = true;
    $("customer-delete-id").value = "";
    $("customer-revoke").hidden = !subscription;
    $("customer-subscription-title").textContent = subscription ? "Редактировать подписку" : "Выдать подписку";
    $("customer-subscription-save").textContent = subscription ? "Сохранить подписку" : "Выдать подписку";
    $("customer-subscription-save").disabled = data.blocked || !customerPlans.size;
    $("customer-plan").replaceChildren(new Option("Выберите тариф", ""));
    for (const plan of customerPlans.values()) {
      $("customer-plan").add(new Option(plan.name + (plan.is_active ? "" : " · скрыт"), plan.id));
    }
    $("customer-plan").value = customerPlans.has(subscription?.plan_id) ? subscription.plan_id : "";
    const expiresAt = subscription?.expires_at && new Date(subscription.expires_at);
    $("customer-expiry").value = expiresAt && expiresAt > new Date() ? localDateTime(expiresAt) : "";
    $("customer-traffic").value = subscription?.traffic_gb ?? "";
    $("customer-devices").value = subscription?.device_limit ?? "";
    $("customer-expiry").min = localDateTime(new Date());
    $("customer-subscriptions").replaceChildren();
    $("customer-payments").replaceChildren();
    data.subscriptions.forEach(item => row("customer-subscriptions", [
      item.id, subscriptionLabels[item.status] || item.status, item.expires_at ? new Date(item.expires_at).toLocaleString() : "—",
      new Date(item.created_at).toLocaleString(),
    ]));
    data.payments.forEach(item => row("customer-payments", [
      item.id, providerLabels[item.provider] || item.provider, formatPaymentAmount(item),
      paymentStatus(item.status), new Date(item.created_at).toLocaleString(),
    ]));
    $("customer-feedback").textContent = "";
    $("customer-detail").hidden = false;
    if (scroll) {
      $("status").textContent = "";
      $("customer-detail").scrollIntoView({block:"start"});
    }
  } catch (error) {
    if (request === openingCustomer) $("status").textContent = error.message;
  }
}

$("customer-close").onclick = () => goAdminBack("users");

async function mutateCustomer(method, suffix, payload, success, deleted = false) {
  if (!customer || savingCustomer) return;
  const id = customer.telegram_id;
  savingCustomer = true;
  $("customer-detail").setAttribute("aria-busy", "true");
  $("customer-feedback").textContent = "Сохраняем…";
  try {
    await adminApi(method, customerPath(id) + suffix, payload);
    await loadUsers().catch(error => { $("status").textContent = error.message; });
    if (customer?.telegram_id === id) {
      if (deleted) {
        returnNotice = success;
        goAdminBack("users");
      } else {
        await showCustomer(id, {scroll:false});
        if (customer?.telegram_id === id) $("customer-feedback").textContent = success;
      }
    }
  } catch (error) {
    if (customer?.telegram_id === id) $("customer-feedback").textContent = error.message;
  } finally {
    savingCustomer = false;
    $("customer-detail").removeAttribute("aria-busy");
  }
}

$("customer-profile-form").onsubmit = event => {
  event.preventDefault();
  mutateCustomer("PATCH", "", {display_name:$("customer-name").value}, "Имя сохранено.");
};
$("customer-subscription-form").onsubmit = event => {
  event.preventDefault();
  mutateCustomer("PUT", "/subscription", {
    plan_id:Number($("customer-plan").value),
    expires_at:new Date($("customer-expiry").value).toISOString(),
    traffic_gb:Number($("customer-traffic").value), device_limit:Number($("customer-devices").value),
  }, "Подписка сохранена.");
};
$("customer-block").onclick = () => {
  if (!customer) return;
  const blocked = !customer.blocked;
  mutateCustomer("PUT", "/block", {blocked}, blocked ? "Клиент заблокирован." : "Клиент разблокирован.");
};
$("customer-revoke").onclick = () => {
  const prompt = "Удалить подписку и отключить VPN у клиента?";
  const id = customer?.telegram_id;
  const revoke = confirmed => {
    if (confirmed && customer?.telegram_id === id) mutateCustomer("DELETE", "/subscription", null, "Подписка удалена.");
  };
  if (tg?.showConfirm && tg.isVersionAtLeast?.("6.2")) tg.showConfirm(prompt, revoke);
  else revoke(window.confirm(prompt));
};
$("customer-delete-open").onclick = () => {
  $("customer-delete-form").hidden = false;
  $("customer-delete-id").focus();
};
$("customer-delete-cancel").onclick = () => { $("customer-delete-form").hidden = true; };
$("customer-delete-form").onsubmit = event => {
  event.preventDefault();
  const confirmation = $("customer-delete-id").value.trim();
  if (confirmation !== String(customer?.telegram_id)) {
    $("customer-feedback").textContent = "Введите Telegram ID выбранного клиента.";
    return;
  }
  mutateCustomer("DELETE", "", {confirm_telegram_id:Number(confirmation)}, "Аккаунт удалён.", true);
};
async function loadPayments(){const d=await api("/admin/api/payments?limit="+pageSize+"&offset="+paymentOffset);$("payments-table").replaceChildren();d.items.forEach(p=>row("payments-table",[p.id,p.telegram_id ?? "Аккаунт удалён",providerLabels[p.provider]||p.provider,formatPaymentAmount(p),paymentStatus(p.status),new Date(p.created_at).toLocaleString()]));$("payments-page").textContent="Показано "+(d.total?paymentOffset+1:0)+"–"+Math.min(paymentOffset+d.items.length,d.total)+" из "+d.total;$("payments-prev").disabled=paymentOffset===0;$("payments-next").disabled=paymentOffset+pageSize>=d.total}
async function adminApi(method,path,body){const r=await fetch(path,{method,headers:{"X-Telegram-Init-Data":tg?.initData||"","Content-Type":"application/json"},body:body?JSON.stringify(body):undefined});const d=await r.json();if(!r.ok)throw Object.assign(Error(typeof d.detail==="string"?d.detail:"Ошибка запроса"),{status:r.status});return d}
let squadItems = [];
let selectedSquads = [];
let modeSquads = null;
function renderSquads() {
  const query = $("squads-search").value.trim().toLocaleLowerCase();
  const selected = $("squads-selected");
  const options = $("squads-options");
  selected.replaceChildren();
  options.replaceChildren();
  $("squads-policy").textContent = selectedSquads === null
    ? "В старом тарифе не заданы сквады. Выберите сквады для новых подписок."
    : "Выбранные сквады для новых подписок";
  function button(parent, item, chosen) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "action";
    b.textContent = (chosen ? "Удалить " : "Добавить ") + item.name + " · " + item.uuid
      + (item.available ? "" : " ⚠ Недоступен");
    b.disabled = !chosen && !item.available;
    b.onclick = () => {
      selectedSquads = chosen ? selectedSquads.filter(id => id !== item.uuid)
        : [...(selectedSquads || []), item.uuid];
      renderSquads();
    };
    parent.appendChild(b);
  }
  for (const id of selectedSquads || []) {
    button(selected, squadItems.find(item => item.uuid === id) || {uuid: id, name: "Сквад"}, true);
  }
  for (const item of squadItems) {
    if (!selectedSquads?.includes(item.uuid) && item.name.toLocaleLowerCase().includes(query)) {
      button(options, item, false);
    }
  }
}
async function loadSquads(refresh = false) {
  const control = $("squads-refresh");
  control.disabled = true;
  $("squads-status").textContent = "Загрузка списка…";
  try {
    const data = await adminApi(refresh ? "POST" : "GET", "/admin/api/internal-squads" + (refresh ? "/refresh" : ""));
    squadItems = data.items;
    $("squads-status").textContent = (data.failed ? "⚠ Ошибка синхронизации. " : "")
      + (data.last_success ? "Последнее обновление: " + new Date(data.last_success).toLocaleString() : "Список ещё не получен");
    renderSquads();
    renderModePolicies();
  } catch (error) {
    renderModePolicies();
    $("squads-status").textContent = "⚠ Ошибка загрузки списка: " + error.message;
  } finally {
    control.disabled = false;
  }
}
$("squads-search").oninput = renderSquads;
$("squads-refresh").onclick = () => loadSquads(true);
function renderModePolicies() {
  if (!modeSquads) return;
  const container = $("mode-policies");
  container.replaceChildren();
  for (const [mode, title] of [["flexible", "Гибкий конструктор"], ["trial", "Пробный доступ"]]) {
    const fieldset = document.createElement("fieldset");
    const legend = document.createElement("legend");
    legend.textContent = title;
    fieldset.append(legend);
    const choices = document.createElement("div");
    choices.className = "mode-squad-options";
    const selected = modeSquads[mode];
    const keys = new Set([...selected, ...squadItems.map(item => item.uuid)]);
    if (!keys.size) choices.textContent = "Список сквадов пока пуст";
    for (const key of keys) {
      const item = squadItems.find(row => row.uuid === key);
      const label = document.createElement("label");
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = selected.has(key);
      checkbox.disabled = !item?.available && !checkbox.checked;
      checkbox.onchange = () => {
        if (checkbox.checked) selected.add(key);
        else selected.delete(key);
      };
      const name = document.createElement("span");
      name.textContent = (item?.name || "Сквад") + " · " + key + (item?.available ? "" : " ⚠ Недоступен");
      label.append(checkbox, name);
      choices.append(label);
    }
    const save = document.createElement("button");
    save.type = "button";
    save.className = "action";
    save.textContent = "Сохранить · " + title;
    save.onclick = async () => {
      save.disabled = true;
      try {
        await adminApi("PUT", "/admin/api/squad-policies/" + mode, [...selected]);
        $("status").textContent = "Настройки сохранены";
      } catch (error) {
        $("status").textContent = error.message;
      } finally {
        save.disabled = false;
      }
    };
    fieldset.append(choices, save);
    container.append(fieldset);
  }
}
let planCatalog = new Map();
async function loadPlans(){
  if (!modeSquads) {
    const policies = await adminApi("GET", "/admin/api/squad-policies");
    modeSquads = {flexible: new Set(policies.flexible), trial: new Set(policies.trial)};
  }
  await loadSquads();
  const {items} = await adminApi("GET", "/admin/api/plans");
  planCatalog = new Map(items.map(plan => [plan.id, plan]));
  const table = $("plans-table");
  table.replaceChildren();
  if (!items.length) {
    const tr = document.createElement("tr");
    const empty = cell("Тарифов пока нет");
    empty.colSpan = 6;
    tr.append(empty);
    table.append(tr);
  }
  for (const plan of items) {
    const badge = plan.badge || (plan.is_featured ? "Популярный" : "");
    row("plans-table", [
      badge ? plan.name + " · " + badge : plan.name,
      `${plan.days} дн. · ${plan.traffic_gb === 0 ? "Безлимит" : plan.traffic_gb + " ГБ"} · устройств: ${plan.devices_included}`,
      (plan.price_rub_minor / 100).toFixed(2) + " ₽", plan.price_stars + " ⭐",
      plan.is_active ? "Активен" : "Скрыт",
    ]);
    const td = cell("");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "action";
    button.textContent = "Изменить";
    button.onclick = () => openTab("plans", {panel: "plan", record: plan.id});
    td.append(button);
    table.lastElementChild.append(td);
  }
}

function fillPlan(plan = null) {
  $("plan-form").reset();
  $("plan-form-title").textContent = plan ? "Редактировать тариф" : "Новый тариф";
  $("plan-status").textContent = "";
  $("squads-search").value = "";
  selectedSquads = plan ? plan.internal_squads : [];
  renderSquads();
  if (plan) {
    const fields = {
      id: plan.id, title: plan.name, description: plan.description, days: plan.days,
      traffic: plan.traffic_gb, devices: plan.devices_included, rub: plan.price_rub_minor / 100,
      stars: plan.price_stars, order: plan.sort_order, badge: plan.badge,
      "extra-rub": plan.extra_device_price_rub_minor == null ? "" : plan.extra_device_price_rub_minor / 100,
      "extra-stars": plan.extra_device_price_stars ?? "",
    };
    for (const [field, value] of Object.entries(fields)) $("plan-" + field).value = value ?? "";
    $("plan-active").checked = plan.is_active;
    $("plan-featured").checked = plan.is_featured;
  }
  $("plan-form").hidden = false;
}
$("plan-new").onclick = () => openTab("plans", {panel: "plan"});
$("plan-cancel").onclick = () => goAdminBack("plans");
$("plans-refresh").onclick = () => loadPlans().catch(e => { $("status").textContent = e.message; });
$("plan-form").onsubmit = async event => {
  event.preventDefault();
  const button = event.submitter;
  if (button) button.disabled = true;
  try {
    const id = $("plan-id").value;
    const payload = {
      name: $("plan-title").value.trim(), description: $("plan-description").value,
      days: +$("plan-days").value, traffic_gb: +$("plan-traffic").value, devices_included: +$("plan-devices").value,
      price_rub: $("plan-rub").value, price_stars: +$("plan-stars").value,
      extra_device_price_rub: $("plan-extra-rub").value || null,
      extra_device_price_stars: $("plan-extra-stars").value === "" ? null : +$("plan-extra-stars").value,
      sort_order: +$("plan-order").value, is_active: $("plan-active").checked,
      badge: $("plan-badge").value, is_featured: $("plan-featured").checked, internal_squads: selectedSquads,
    };
    const frameId = activeFrame.id;
    const saved = await adminApi(id ? "PUT" : "POST", "/admin/api/plans" + (id ? "/" + id : ""), payload);
    if (activeFrame.id === frameId) {
      fillPlan(saved);
      $("plan-status").textContent = "Тариф сохранён";
      activeFrame = {...activeFrame, record: saved.id};
      history.replaceState({adminPanel: activeFrame}, "");
    }
    await loadPlans();
  } catch (error) {
    $("plan-status").textContent = error.message;
  } finally {
    if (button) button.disabled = false;
  }
};

function renderImage(prefix, url) {
  const image = $(prefix + "-preview");
  image.hidden = !url;
  $(prefix + "-placeholder").hidden = Boolean(url);
  if (url) image.src = url;
  else image.removeAttribute("src");
}

function renderStoreMark(name, logo) {
  $("admin-shop-name").textContent = name;
  $("admin-shop-mark").textContent = name.trim().charAt(0).toUpperCase() || "R";
  $("admin-shop-mark").hidden = Boolean(logo);
  $("admin-shop-logo").hidden = !logo;
  if (logo) $("admin-shop-logo").src = logo;
  else $("admin-shop-logo").removeAttribute("src");
}

function readImageAsDataUrl(file){
  return new Promise((resolve,reject)=>{
    const reader=new FileReader();
    reader.onload=()=>resolve(String(reader.result||""));
    reader.onerror=()=>reject(Error("Не удалось прочитать фотографию"));
    reader.readAsDataURL(file);
  });
}
function setupImageUpload(prefix, inputId, kind) {
  const input = $(inputId);
  const upload = $(prefix + "-upload");
  const remove = $(prefix + "-remove");
  const status = $(prefix + "-status");
  const path = "/admin/api/store-profile/" + kind;
  let previewUrl = null;
  input.onchange = () => {
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    const file = input.files?.[0];
    if (!file) return;
    previewUrl = URL.createObjectURL(file);
    renderImage(prefix, previewUrl);
  };
  async function update(method) {
    upload.disabled = remove.disabled = true;
    try {
      const file = input.files?.[0];
      if (method === "PUT" && !file) throw Error("Сначала выберите изображение");
      if (method === "PUT" && file.size > 5 * 1024 * 1024) throw Error("Максимальный размер изображения — 5 МБ");
      const body = method === "PUT" ? {data_base64: await readImageAsDataUrl(file)} : undefined;
      const {url} = await adminApi(method, path, body);
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      previewUrl = null;
      input.value = "";
      renderImage(prefix, url);
      if (kind === "logo-image") renderStoreMark($("admin-shop-name").textContent, url);
      status.textContent = url ? "Изображение опубликовано" : "Изображение удалено";
    } catch (error) {
      status.textContent = error.message;
    } finally {
      upload.disabled = remove.disabled = false;
    }
  }
  upload.onclick = () => update("PUT");
  remove.onclick = () => update("DELETE");
}
setupImageUpload("brand-photo", "brand-photo", "welcome-image");
setupImageUpload("brand-logo", "brand-logo-file", "logo-image");
async function loadBotIdentity(){
  try{
    const info=await adminApi("GET","/admin/api/bot-identity");
    $("bot-identity").textContent=(info.name||"Бот")+" · "+(info.username?"@"+info.username:"username не определён");
  }catch{$("bot-identity").textContent="Не удалось получить данные бота"}
}
async function loadBrand(){
  const d=await adminApi("GET","/admin/api/store-profile");
  $("brand-name").value=d.name;$("brand-tagline").value=d.tagline;$("brand-description").value=d.description;
  fillPalette(d.theme_colors || {});
  $("brand-preview-name").textContent = d.name;
  $("brand-guide").value = d.connection_instructions || "";
  $("brand-bot-guide").value = d.bot_connection_instructions || "";
  $("brand-terms").value = d.terms_url || "";
  renderStoreMark(d.name, d.logo_image_url);
  renderImage("brand-photo", d.welcome_image_url);
  renderImage("brand-logo", d.logo_image_url);
  loadBotIdentity();
}
setupPalette(colors => adminApi("PUT", "/admin/api/theme", {colors}));
$("brand-name").oninput = event => { $("brand-preview-name").textContent = event.target.value || "Remnaum"; };
$("brand-form").onsubmit=async e=>{
  e.preventDefault();try{
    await adminApi("PUT","/admin/api/store-profile",{
      name:$("brand-name").value,tagline:$("brand-tagline").value,
      description:$("brand-description").value,connection_instructions:$("brand-guide").value,
      bot_connection_instructions:$("brand-bot-guide").value,
      terms_url:$("brand-terms").value
    });$("admin-shop-name").textContent=$("brand-name").value;$("status").textContent="Оформление сохранено";
  }catch(e){$("status").textContent=e.message}
};
const providerLabels = {
  telegram_stars: "Telegram Stars", yookassa: "ЮKassa", yoomoney: "ЮMoney",
  cryptopay: "Crypto Pay", platega: "Platega",
};
function formatPaymentAmount({amount, currency}) {
  if (currency === "XTR") return amount.toLocaleString("ru-RU") + " ⭐";
  if (currency === "RUB") return (amount / 100).toLocaleString("ru-RU", {style:"currency", currency});
  return (amount / 100).toLocaleString("ru-RU") + " " + currency;
}
function paymentStatus(status) {
  return {pending:"Ждёт оплаты", paid:"Оплачен", succeeded:"Подписка выдана", failed:"Ошибка", cancelled:"Отменён"}[status] || status;
}
const providerTabs = {
  telegram_stars: "pay-stars", yookassa: "pay-yookassa", yoomoney: "pay-yoomoney",
  cryptopay: "pay-cryptopay", platega: "pay-platega",
};
const providerCredentials = {
  yookassa: {shop_id:"pay-yookassa-id", secret_key:"pay-yookassa-secret"},
  yoomoney: {receiver:"pay-yoomoney-receiver", notification_secret:"pay-yoomoney-secret"},
  cryptopay: {api_token:"pay-crypto-token"},
  platega: {merchant_id:"pay-platega-id", secret_key:"pay-platega-secret"},
};
const providerMarks = {telegram_stars:"★", yookassa:"ЮК", yoomoney:"ЮМ", cryptopay:"₿", platega:"P"};
const savingProviders = new Set();
let paymentSettingsRequest = 0;
let paymentLayout = [];
let layoutDirty = false;
let savingLayout = false;

function renderPaymentLayout() {
  const list = $("payment-layout-list");
  list.replaceChildren();
  for (const [index, entry] of paymentLayout.entries()) {
    const line = document.createElement("div");
    line.className = "payment-layout-row";
    const field = document.createElement("label");
    field.textContent = providerLabels[entry.provider];
    const input = document.createElement("input");
    input.maxLength = 24;
    input.value = entry.label;
    input.disabled = savingLayout;
    input.oninput = () => { entry.label = input.value; layoutDirty = true; $("payment-layout-save").disabled = false; };
    field.append(input);
    line.append(field);
    for (const [direction, symbol, text] of [[-1, "↑", "Поднять"], [1, "↓", "Опустить"]]) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "action admin-icon-action";
      button.textContent = symbol;
      button.setAttribute("aria-label", text + " " + providerLabels[entry.provider]);
      const next = index + direction;
      button.disabled = savingLayout || next < 0 || next >= paymentLayout.length;
      button.onclick = () => {
        const other = paymentLayout[next];
        paymentLayout[next] = entry;
        paymentLayout[index] = other;
        layoutDirty = true;
        renderPaymentLayout();
        const moved = list.children[next];
        const controls = Array.from(moved.querySelectorAll("button"));
        (controls.find(control => control.textContent === symbol && !control.disabled)
          || controls.find(control => !control.disabled))?.focus();
      };
      line.append(button);
    }
    list.append(line);
  }
  $("payment-layout-save").disabled = savingLayout || !layoutDirty;
}

$("payment-layout-form").onsubmit = async event => {
  event.preventDefault();
  if (!layoutDirty || savingLayout) return;
  savingLayout = true;
  renderPaymentLayout();
  const feedback = $("payment-layout-feedback");
  feedback.textContent = "Сохраняем…";
  try {
    const data = await adminApi("PUT", "/admin/api/payment-settings", {presentation:paymentLayout});
    paymentLayout = data.presentation;
    layoutDirty = false;
    await loadPaymentSettings();
    feedback.textContent = "Порядок и названия сохранены.";
  } catch (error) {
    feedback.textContent = error.message;
  } finally {
    savingLayout = false;
    renderPaymentLayout();
  }
};

async function loadPaymentSettings() {
  const request = ++paymentSettingsRequest;
  const data = await adminApi("GET", "/admin/api/payment-diagnostics");
  if (request !== paymentSettingsRequest) return;
  const layout = data.presentation || Object.entries(providerLabels).map(([provider,label]) => ({provider,label}));
  if (!layoutDirty) {
    paymentLayout = layout.map(entry => ({...entry}));
    renderPaymentLayout();
  }
  const list = $("method-list");
  list.replaceChildren();
  for (const {provider, label} of layout) {
    const info = data.providers[provider] || {};
    const status = !info.configured
      ? (provider === "telegram_stars" ? "Бот не подключён" : "Нужны реквизиты")
      : info.enabled ? "Включено" : "Выключено";
    if (!savingProviders.has(provider)) {
      const toggle = $("method-" + provider);
      toggle.checked = Boolean(info.enabled);
      toggle.disabled = false;
      document.querySelector(`[data-save-provider="${provider}"]`).disabled = false;
    }
    $("provider-" + provider + "-state").textContent = status;
    const webhook = $("provider-" + provider + "-webhook");
    if (webhook) {
      webhook.value = info.webhook_url || "";
      document.querySelector(`[data-copy-webhook="${provider}"]`).disabled = !webhook.value;
    }
    const button = document.createElement("button");
    button.type = "button";
    button.className = "provider-tile";
    button.dataset.openTab = providerTabs[provider];
    const mark = document.createElement("span");
    mark.className = "provider-mark";
    mark.textContent = providerMarks[provider];
    mark.setAttribute("aria-hidden", "true");
    const title = document.createElement("strong");
    title.textContent = label;
    const indicator = document.createElement("span");
    indicator.className = "provider-indicator";
    indicator.dataset.status = info.configured ? (info.enabled ? "enabled" : "disabled") : "unconfigured";
    indicator.textContent = status;
    const arrow = document.createElement("span");
    arrow.setAttribute("aria-hidden", "true");
    arrow.className = "provider-chevron";
    arrow.textContent = "›";
    button.append(mark, title, indicator, arrow);
    button.addEventListener("click", () => openTab(providerTabs[provider]));
    list.appendChild(button);
  }
}
document.querySelectorAll("[data-copy-webhook]").forEach(button=>{
  button.addEventListener("click",async()=>{
    const provider = button.dataset.copyWebhook;
    const input = $("provider-" + provider + "-webhook");
    const feedback = $("provider-" + provider + "-feedback");
    try {
      await navigator.clipboard.writeText(input.value);
      feedback.textContent = "URL скопирован.";
    } catch {
      input.focus();
      input.select();
      feedback.textContent = "Скопируйте выделенный URL.";
    }
  });
});
async function savePaymentProvider(provider) {
  if (!Object.hasOwn(providerLabels,provider)) throw Error("Неизвестная платёжная система");
  const button = document.querySelector('[data-save-provider="' + provider + '"]');
  const feedback = $("provider-" + provider + "-feedback");
  const fields = Object.entries(providerCredentials[provider] || {});
  const toggle = $("method-" + provider);
  if (savingProviders.has(provider) || button.disabled) return;
  savingProviders.add(provider);
  button.disabled = true;
  toggle.disabled = true;
  feedback.textContent = "Сохраняем…";
  try {
    const values = {};
    for (const [key,id] of fields) {
      // Sending an empty field never overwrites an encrypted secret in the API.
      const value = $(id).value.trim();
      if (value) values[key] = value;
    }
    const credentials = Object.keys(values).length ? {[provider]:values} : {};
    const saved = await adminApi("PUT","/admin/api/payment-settings",{
      methods:{[provider]:toggle.checked},
      credentials,
    });
    for (const [key, id] of fields) {
      const input = $(id);
      if (input.type === "password" && input.value.trim() === values[key]) input.value = "";
    }
    toggle.checked = Boolean(saved.methods[provider]);
    feedback.textContent = toggle.checked ? "Способ оплаты включён." : "Способ оплаты выключен.";
    await loadPaymentSettings().catch(() => {});
  } catch(error) {
    feedback.textContent = error.message;
  } finally {
    savingProviders.delete(provider);
    toggle.disabled = false;
    button.disabled = false;
  }
}
document.querySelectorAll("[data-provider-form]").forEach(form=>{
  form.addEventListener("submit", event => {
    event.preventDefault();
    savePaymentProvider(form.dataset.providerForm);
  });
});
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
const notificationRulesForm = $("notification-rules-form");
const notificationRuleFields = {
  subscription: {title: "Срок подписки", leadLabel: "Напоминать за сколько часов до окончания", max: 720},
  trial: {title: "Пробный доступ", leadLabel: "Напоминать за сколько часов до окончания", max: 720},
  traffic: {title: "Трафик", leadLabel: "Напоминать при остатке трафика, %", max: 99},
};
for (const [kind, {title, leadLabel, max}] of Object.entries(notificationRuleFields)) {
  const fields = $("notification-rule-template").content.firstElementChild.cloneNode(true);
  fields.dataset.rule = kind;
  fields.querySelector("legend").textContent = title;
  fields.querySelector("[data-lead-label]").textContent = leadLabel;
  fields.querySelector('[name="lead"]').max = max;
  $("notification-rules").append(fields);
}
const notificationRuleGroups = [...notificationRulesForm.querySelectorAll("fieldset")];
const notificationRulesStatus = $("notification-rules-status");
let notificationRulesRequest;

function loadNotificationRules() {
  if (!notificationRulesRequest) {
    notificationRulesStatus.textContent = "Загружаем настройки…";
    notificationRulesRequest = adminApi("GET", "/admin/api/notifications").then(rules => {
      for (const group of notificationRuleGroups) {
        const rule = rules[group.dataset.rule];
        group.querySelector('[name="lead"]').value = rule.lead;
        group.querySelector('[name="reminderEnabled"]').checked = rule.reminder.enabled;
        group.querySelector('[name="reminderText"]').value = rule.reminder.text;
        group.querySelector('[name="expiredEnabled"]').checked = rule.expired.enabled;
        group.querySelector('[name="expiredText"]').value = rule.expired.text;
      }
      notificationRulesForm.hidden = false;
      notificationRulesStatus.textContent = "";
    }).catch(error => {
      notificationRulesStatus.textContent = error.message;
      notificationRulesRequest = undefined;
    });
  }
  return notificationRulesRequest;
}

notificationRulesForm.onsubmit = async event => {
  event.preventDefault();
  const submit = notificationRulesForm.querySelector("button");
  if (submit.disabled) return;
  const rules = Object.fromEntries(notificationRuleGroups.map(group => [group.dataset.rule, {
    lead: Number(group.querySelector('[name="lead"]').value),
    reminder: {
      enabled: group.querySelector('[name="reminderEnabled"]').checked,
      text: group.querySelector('[name="reminderText"]').value.trim(),
    },
    expired: {
      enabled: group.querySelector('[name="expiredEnabled"]').checked,
      text: group.querySelector('[name="expiredText"]').value.trim(),
    },
  }]));
  if (Object.values(rules).some(rule => !rule.reminder.text || !rule.expired.text)) {
    notificationRulesStatus.textContent = "Введите тексты всех сообщений.";
    return;
  }
  submit.disabled = true;
  for (const group of notificationRuleGroups) group.disabled = true;
  notificationRulesStatus.textContent = "Сохраняем…";
  try {
    await adminApi("PUT", "/admin/api/notifications", rules);
    notificationRulesStatus.textContent = "Настройки уведомлений сохранены.";
  } catch (error) {
    notificationRulesStatus.textContent = error.message;
  } finally {
    submit.disabled = false;
    for (const group of notificationRuleGroups) group.disabled = false;
  }
};

const documentForms = document.querySelectorAll("[data-document-kind]");
let documentsRequest;

function loadDocuments() {
  documentsRequest ??= adminApi("GET", "/admin/api/documents").then(({documents}) => {
    for (const form of documentForms) {
      const body = documents[form.dataset.documentKind];
      form.querySelector("textarea").value = body ?? "";
      form.querySelector("button").textContent = body ? "Сохранить изменения" : "Опубликовать";
      form.querySelector('[role="status"]').textContent = body ? "Опубликован" : "Не опубликован";
      form.querySelector("fieldset").disabled = false;
    }
  }).catch(error => {
    documentsRequest = undefined;
    throw error;
  });
  return documentsRequest;
}

for (const form of documentForms) {
  form.addEventListener("submit", async event => {
    event.preventDefault();
    const fieldset = form.querySelector("fieldset");
    const input = form.querySelector("textarea");
    const feedback = form.querySelector('[role="status"]');
    if (fieldset.disabled) return;
    fieldset.disabled = true;
    feedback.textContent = "Сохраняем…";
    try {
      const {body} = await adminApi("PUT", `/admin/api/documents/${form.dataset.documentKind}`, {body: input.value});
      input.value = body;
      form.querySelector("button").textContent = "Сохранить изменения";
      feedback.textContent = "Документ опубликован";
    } catch (error) {
      feedback.textContent = error.message;
    } finally {
      fieldset.disabled = false;
    }
  });
}

const sidebarIconNames = {
  overview:"grid", plans:"tag", users:"users", payments:"card",
  bonuses:"ticket", referrals:"gift", configuration:"wallet", documents:"document", brand:"palette", tickets:"support",
};
for(const button of document.querySelectorAll(".admin-tabs [data-tab]")){
  const icon=document.createElementNS("http://www.w3.org/2000/svg","svg");
  icon.classList.add("admin-sidebar-icon");
  icon.setAttribute("aria-hidden","true");
  const use=document.createElementNS("http://www.w3.org/2000/svg","use");
  use.setAttribute("href","#ai-"+sidebarIconNames[button.dataset.tab]);
  icon.append(use);
  button.prepend(icon);
}
const adminSidebar=$("admin-sidebar"), adminMenuOpen=$("admin-menu-open"), adminBackdrop=$("admin-menu-backdrop");
let adminReturnFocus=null;
function setAdminMenu(open) {
  adminSidebar.classList.toggle("is-open",open);
  document.body.classList.toggle("drawer-open", open);
  document.querySelector(".admin-workspace").inert = open;
  adminBackdrop.hidden=!open;
  adminMenuOpen.setAttribute("aria-expanded",String(open));
  if(open){adminReturnFocus=document.activeElement; adminSidebar.querySelector('[data-tab][aria-current="page"]')?.focus();}
  else if(adminReturnFocus?.isConnected){adminReturnFocus.focus({preventScroll:true});adminReturnFocus=null;}
}
adminMenuOpen.onclick=()=>setAdminMenu(true);
$("admin-menu-close").onclick=()=>setAdminMenu(false);
adminBackdrop.onclick=()=>setAdminMenu(false);
matchMedia("(min-width: 851px)").addEventListener("change", event => {
  if (event.matches && adminSidebar.classList.contains("is-open")) setAdminMenu(false);
});
document.addEventListener("keydown",e=>{if(e.key==="Escape"&&adminSidebar.classList.contains("is-open"))setAdminMenu(false);});
const operationsForm = $("operations-form");
const secretFields = new Set(["panel_token", "panel_webhook_secret"]);
async function loadOperations() {
  const data = await api("/admin/api/operations");
  for (const field of operationsForm.elements) {
    if (field.type === "checkbox") field.checked = data[field.name];
    else if (field.name) field.value = secretFields.has(field.name) ? "" : data[field.name] ?? "";
  }
  $("panel-configuration-status").textContent = "API " + (data.panel_configured ? "настроен" : "требует реквизиты") +
    " · Вебхук " + (data.panel_webhook_configured ? "настроен" : "не настроен");
}
operationsForm.onsubmit = async event => {
  event.preventDefault();
  event.submitter.disabled = true;
  try {
    const data = {};
    for (const field of operationsForm.elements) {
      if (!field.name) continue;
      data[field.name] = field.type === "checkbox" ? field.checked : field.type === "number" ? field.valueAsNumber : field.value.trim();
    }
    await adminApi("PUT", "/admin/api/operations", data);
    await loadOperations();
    $("operations-status").textContent = "Настройки сохранены.";
  } catch (error) {
    $("operations-status").textContent = error.message;
  } finally {
    event.submitter.disabled = false;
  }
};

function setupLoginForm(provider, fillDetails) {
  const form = $("login-" + provider + "-form");
  const status = $("login-" + provider + "-status");

  const fill = data => {
    form.elements.enabled.checked = data[provider + "_login"];
    fillDetails(data, form);
  };
  async function load() {
    const data = await api("/admin/api/operations");
    fill(data);
  }
  form.onsubmit = async event => {
    event.preventDefault();
    const payload = Object.fromEntries(new FormData(form));
    payload.enabled = form.elements.enabled.checked;
    event.submitter.disabled = true;
    try {
      await adminApi("PUT", "/admin/api/login/" + provider, payload);
      if (form.elements.client_secret) form.elements.client_secret.value = "";
      status.textContent = "Настройки входа сохранены.";
    } catch (error) {
      status.textContent = error.message;
    } finally {
      event.submitter.disabled = false;
    }
  };
  return load;
}
const loadTelegramLogin = setupLoginForm("telegram", data => {
  $("telegram-browser-domain").value = data.browser_origin ? new URL(data.browser_origin).hostname : "";
  $("telegram-login-state").textContent = data.telegram_configured ? "Бот подключён." : "Подключите Telegram-бота.";
});
const loadGoogleLogin = setupLoginForm("google", (data, form) => {
  form.elements.client_id.value = data.google_client_id;
  form.elements.client_secret.value = "";
  $("google-callback").value = data.google_callback_url;
  $("google-login-state").textContent = data.google_configured
    ? "Реквизиты сохранены. Пустое поле сохраняет текущий Client Secret."
    : "Добавьте Redirect URI в настройках OAuth-клиента Google.";
});

async function launchCampaign(button, payload, prompt) {
  if (!window.confirm(prompt)) return;
  button.disabled = true;
  try {
    await adminApi("POST", "/admin/api/campaigns", payload);
    $("campaign-status").textContent = "Задача запущена. Итог придёт в Telegram.";
    if (payload.action === "broadcast") $("broadcast-form").reset();
  } catch (error) {
    $("campaign-status").textContent = error.message;
  } finally {
    button.disabled = false;
  }
}
$("broadcast-form").onsubmit = event => {
  event.preventDefault();
  launchCampaign(event.submitter, {action: "broadcast", text: event.currentTarget.elements.text.value},
    "Отправить это сообщение всем доступным пользователям?");
};
$("refresh-all-menus").onclick = event =>
  launchCampaign(event.currentTarget, {action: "refresh"}, "Заменить текущее меню у всех доступных пользователей?");
const loadAdminTickets = ticketPanel($("tickets-view"), (path, data, method) =>
  adminApi(method || "GET", "/admin/api/tickets" + path, data), {
  admin: true,
  openTicket: id => openTab("tickets", {panel: "ticket", record: id, replace: activeFrame.panel === "ticket"}),
  openHistory: () => {
    if (activeFrame.panel === "ticket" && activeFrame.depth) goAdminBack();
    else openTab("tickets", {replace: true});
  },
});

const contactForm = $("support-contact-form");
async function loadSupportContact() {
  if (contactForm.dataset.loaded) return;
  const profile = await api("/admin/api/store-profile");
  $("support-contact").value = profile.support_contact || "";
  contactForm.dataset.loaded = "true";
}
contactForm.onsubmit = async event => {
  event.preventDefault();
  event.submitter.disabled = true;
  try {
    const saved = await adminApi("PUT", "/admin/api/support-settings", {
      support_contact: $("support-contact").value,
    });
    $("support-contact").value = saved.support_contact;
    $("support-contact-status").textContent = "Контакт сохранён.";
  } catch (error) {
    $("support-contact-status").textContent = error.message;
  } finally {
    event.submitter.disabled = false;
  }
};

const buttonColors = [
  ["", "Стандартный"], ["primary", "Синий"], ["success", "Зелёный"], ["danger", "Красный"],
];
const buttonForm = $("bot-buttons-form");
let buttonEditors = [];

async function loadBotButtons() {
  const list = $("bot-buttons-list");
  $("save-bot-buttons").disabled = $("reset-bot-buttons").disabled = true;
  $("bot-buttons-status").textContent = "";
  list.replaceChildren();
  const data = await api("/admin/api/bot-buttons");
  const groups = new Map();
  buttonEditors = data.items.map(item => {
    if (!groups.has(item.section)) {
      const group = document.createElement("details");
      const summary = document.createElement("summary");
      summary.textContent = item.section;
      group.open = groups.size === 0;
      group.append(summary);
      list.append(group);
      groups.set(item.section, group);
    }
    const row = document.createElement("div");
    row.className = "bot-button-row";
    const preview = document.createElement("span");
    preview.className = "bot-button-preview";
    const emojiLabel = document.createElement("label");
    emojiLabel.textContent = "Эмодзи";
    const emoji = document.createElement("input");
    emoji.value = item.emoji;
    emoji.maxLength = 24;
    emoji.autocomplete = "off";
    emoji.setAttribute("aria-label", "Эмодзи · " + item.label);
    emojiLabel.append(emoji);
    const colorLabel = document.createElement("label");
    colorLabel.textContent = "Цвет";
    const color = document.createElement("select");
    color.setAttribute("aria-label", "Цвет · " + item.label);
    for (const [value, name] of buttonColors) color.add(new Option(name, value));
    color.value = item.style;
    colorLabel.append(color);
    const render = () => {
      preview.textContent = [emoji.value.trim(), item.label].filter(Boolean).join(" ");
      preview.dataset.color = color.value;
    };
    emoji.addEventListener("input", render);
    color.addEventListener("change", render);
    render();
    row.append(preview, emojiLabel, colorLabel);
    groups.get(item.section).append(row);
    return {item, emoji, color, render};
  });
  $("save-bot-buttons").disabled = $("reset-bot-buttons").disabled = false;
}

$("reset-bot-buttons").onclick = () => {
  for (const {item, emoji, color, render} of buttonEditors) {
    emoji.value = item.default_emoji;
    color.value = "";
    render();
  }
  $("bot-buttons-status").textContent = "Сохраните, чтобы применить стандартные кнопки.";
};

buttonForm.onsubmit = async event => {
  event.preventDefault();
  const save = $("save-bot-buttons");
  save.disabled = true;
  try {
    const buttons = Object.fromEntries(buttonEditors.map(({item, emoji, color}) =>
      [item.key, {emoji: emoji.value.trim(), style: color.value}]));
    await adminApi("PUT", "/admin/api/bot-buttons", {buttons});
    $("bot-buttons-status").textContent = "Оформление кнопок сохранено.";
  } catch (error) {
    $("bot-buttons-status").textContent = error.status === 422 ? "Проверьте эмодзи и повторите сохранение." : error.message;
  } finally {
    save.disabled = false;
  }
};

const tabs = {
  "bot-buttons": {sections: ["bot-buttons-view"], load: loadBotButtons},
  login: {sections: ["login-view"]},
  "login-telegram": {sections: ["login-telegram-view"], load: loadTelegramLogin},
  "login-google": {sections: ["login-google-view"], load: loadGoogleLogin},
  operations: {sections: ["operations-view"], load: loadOperations},
  campaigns: {sections: ["campaigns-view"]},
  tickets: {sections: ["tickets-view"], load: frame => Promise.all([
    loadAdminTickets(frame.record), loadSupportContact(),
  ])},
  overview: {sections: ["overview-view"], load: overview},
  plans: {sections: ["plans-view"], load: loadPlans},
  users: {sections: ["users-view"], load: loadUsers},
  payments: {sections: ["payments-view"], load: loadPayments},
  bonuses: {sections: ["promos-view"], load: loadPromos},
  referrals: {sections: ["referrals-view"], load: loadReferrals},
  brand: {sections: ["brand-view"], load: loadBrand},
  trial: {sections: ["trial-view"], load: loadTrialSettings},
  notifications: {sections: ["notifications-view"], load: loadNotificationRules},
  configuration: {sections: ["configuration-view"]},
  documents: {sections: ["documents-view"], load: loadDocuments},
  settings: {sections: ["payment-settings-view"], load: loadPaymentSettings},
};
const providerByTab = Object.fromEntries(Object.entries(providerTabs).map(([name, tab]) => [tab, name]));
for (const [tab, provider] of Object.entries(providerByTab)) {
  tabs[tab] = {sections: [`payment-${provider}-view`], load: loadPaymentSettings};
}

const legacyTabs = {promos: "bonuses", tariffs: "plans", pricing: "plans", payment_methods: "settings"};
const fromProfile = new URLSearchParams(location.search).get("from") === "profile";
const adminScroll = new Map();
let activeFrame = null;
let frameSequence = 0;
let lastPlanFrame = null;
let returnNotice = "";

function canonicalTab(requested) {
  const key = Object.hasOwn(legacyTabs, requested) ? legacyTabs[requested] : requested;
  return Object.hasOwn(tabs, key) ? key : "overview";
}

function parentFor({tab, panel}) {
  if (panel) return tab;
  if (providerByTab[tab]) return "settings";
  if (["login-telegram", "login-google"].includes(tab)) return "login";
  if (["settings", "login", "trial", "notifications", "operations", "campaigns", "bot-buttons"].includes(tab)) return "configuration";
  return null;
}

function syncAdminBack() {
  if (!activeFrame) return;
  const hasParent = Boolean(parentFor(activeFrame));
  const canGoBack = Boolean(activeFrame.depth || hasParent || fromProfile);
  $("admin-back").hidden = !canGoBack || hasParent;
}

async function renderAdminFrame(frame) {
  const previous = activeFrame;
  frameSequence = Math.max(frameSequence, frame.id);
  activeFrame = frame;
  const tab = tabs[frame.tab];
  const customerOpen = frame.panel === "customer";
  const planOpen = frame.panel === "plan";
  const mainTab = parentFor(frame) && !frame.panel ? "configuration" : frame.tab;

  $("status").textContent = returnNotice;
  returnNotice = "";
  for (const item of Object.values(tabs)) {
    for (const section of item.sections) $(section).hidden = tab !== item;
  }
  for (const button of document.querySelectorAll("nav [data-tab]")) {
    if (button.dataset.tab === mainTab) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  }
  if (adminSidebar.classList.contains("is-open")) setAdminMenu(false);
  syncAdminBack();

  $("customers-list").hidden = customerOpen;
  if (!customerOpen) {
    openingCustomer++;
    if (customerPhoto) URL.revokeObjectURL(customerPhoto);
    customerPhoto = null;
    customer = null;
    $("customer-detail").hidden = true;
  }
  $("plans-list-view").hidden = planOpen;
  $("squad-settings").hidden = planOpen;
  $("plan-form").hidden = !planOpen || lastPlanFrame !== frame.id;
  if (previous?.id !== frame.id) window.scrollTo(0, adminScroll.get(frame.id) || 0);
  try {
    if (customerOpen) {
      await showCustomer(frame.record);
    } else if (planOpen) {
      if (lastPlanFrame !== frame.id) {
        if (frame.record && !planCatalog.has(frame.record)) await loadPlans();
        if (activeFrame !== frame) return;
        const plan = planCatalog.get(frame.record);
        if (frame.record && !plan) throw Error("Тариф больше недоступен.");
        fillPlan(plan);
        lastPlanFrame = frame.id;
      }
    } else {
      if (frame.tab === "tickets") await tab.load(frame);
      else await tab.load?.();
    }
  } catch (error) {
    if (activeFrame === frame) $("status").textContent = error.message;
  }
}

function openTab(requested, {panel = null, record = null, replace = false} = {}) {
  const tab = canonicalTab(requested);
  if (!activeFrame || (activeFrame.tab === tab && activeFrame.panel === panel && activeFrame.record === record)) {
    if (adminSidebar.classList.contains("is-open")) setAdminMenu(false);
    return;
  }
  adminScroll.set(activeFrame.id, window.scrollY);
  const frame = {tab, panel, record, depth: activeFrame.depth + (replace ? 0 : 1), id: ++frameSequence};
  const url = new URL(location.href);
  url.searchParams.set("tab", tab);
  if (tab === "tickets" && panel === "ticket") url.searchParams.set("ticket", record);
  else url.searchParams.delete("ticket");
  if (replace) history.replaceState({adminPanel: frame}, "", url);
  else history.pushState({adminPanel: frame}, "", url);
  renderAdminFrame(frame);
}

function goAdminBack(fallback = null) {
  if (adminSidebar.classList.contains("is-open")) {
    setAdminMenu(false);
    return;
  }
  if (!activeFrame) return;
  const parent = fallback || parentFor(activeFrame);
  if (activeFrame.depth) history.back();
  else if (parent) openTab(parent, {replace: true});
  else if (fromProfile) {
    if (document.referrer && ["/", "/app"].includes(new URL(document.referrer).pathname)) history.back();
    else location.assign("/?section=account" + location.hash);
  }
}

$("admin-back").onclick = () => goAdminBack();
window.addEventListener("popstate", event => {
  if (!activeFrame || !event.state?.adminPanel) return;
  adminScroll.set(activeFrame.id, window.scrollY);
  renderAdminFrame(event.state.adminPanel);
});

async function start() {
  try {
    try {
      const profile = await api("/admin/api/store-profile");
      renderStoreMark(profile.name, profile.logo_image_url);
      if (profile.viewer?.photo_url) {
        const photo = $("admin-viewer-photo");
        photo.onload = () => { $("admin-viewer").hidden = false; };
        photo.src = profile.viewer.photo_url;
      }
    } catch {
      await api("/admin/api/overview");
    }
    $("loading").hidden = true;
    $("dashboard").hidden = false;
    adminMenuOpen.hidden = false;
    document.querySelector(".admin-return-link").href = "/" + location.hash;
    api("/admin/api/release-version").then(({version}) => {
      if (version) $("remnaum-version").textContent = "Релиз Remnaum " + version;
    }).catch(() => {});
    const params = new URLSearchParams(location.search);
    const tab = canonicalTab(params.get("tab"));
    const record = tab === "tickets" ? ticketId(params.get("ticket")) : null;
    const initial = history.state?.adminPanel || {
      tab, panel: record ? "ticket" : null, record, depth: 0, id: 0,
    };
    frameSequence = initial.id;
    history.replaceState({adminPanel: initial}, "");
    history.scrollRestoration = "manual";
    await renderAdminFrame(initial);
  } catch (error) {
    $("loading").textContent = error.message;
    if (!tg?.initData) {
      const link = document.createElement("a");
      const requested = new URLSearchParams(location.search);
      const id = requested.get("tab") === "tickets" ? ticketId(requested.get("ticket")) : null;
      link.href = "/auth?admin=true" + (id ? "&ticket=" + id : "");
      link.textContent = "Войти через Telegram";
      $("loading").append(document.createElement("br"), link);
    }
  }
}

document.querySelectorAll("[data-tab], [data-open-tab]").forEach(button => {
  button.addEventListener("click", () => openTab(button.dataset.tab || button.dataset.openTab));
});
for (const button of document.querySelectorAll("[data-back-tab]")) {
  button.onclick = () => goAdminBack(button.dataset.backTab);
}
$("customers-search").onsubmit = event => {
  event.preventDefault();
  userSearch = $("customers-query").value.trim();
  userOffset = 0;
  loadUsers().catch(error => { $("status").textContent = error.message; });
};
$("users-refresh").onclick=()=>loadUsers().catch(e=>$("status").textContent=e.message);$("payments-refresh").onclick=()=>loadPayments().catch(e=>$("status").textContent=e.message);
$("users-prev").onclick=()=>{userOffset=Math.max(0,userOffset-pageSize);loadUsers()};$("users-next").onclick=()=>{userOffset+=pageSize;loadUsers()};
$("payments-prev").onclick=()=>{paymentOffset=Math.max(0,paymentOffset-pageSize);loadPayments()};$("payments-next").onclick=()=>{paymentOffset+=pageSize;loadPayments()};
start();
