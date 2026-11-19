const tg=window.Telegram?.WebApp;tg?.ready?.();tg?.expand?.();const $=id=>document.getElementById(id);let userOffset=0,paymentOffset=0;const pageSize=20;
async function api(path){const r=await fetch(path,{headers:{"X-Telegram-Init-Data":tg?.initData||""}});const d=await r.json();if(!r.ok)throw Error(d.detail||"Ошибка запроса");return d}
function cell(value){const td=document.createElement("td");td.textContent=value??"—";return td}
function row(target,values){const tr=document.createElement("tr");values.forEach(v=>tr.appendChild(cell(v)));$(target).appendChild(tr)}
async function overview(){
  const d=await api("/admin/api/overview");
  $("users-count").textContent=d.users;
  $("active-count").textContent=d.active_subscriptions;
  $("paid-count").textContent=d.successful_payments;
  $("pending-count").textContent=d.pending_payments;
  $("revenue-count").textContent=(d.revenue_rub_minor/100).toLocaleString("ru-RU",{style:"currency",currency:"RUB"});
  $("stars-count").textContent=d.stars_received.toLocaleString("ru-RU")+" ⭐";
  try {
    const payments=await api("/admin/api/payments?limit=5&offset=0");
    const list=$("dashboard-payments");list.replaceChildren();
    if(!payments.items.length){list.textContent="Платежей пока нет.";return}
    for(const payment of payments.items){
      const card=document.createElement("div");card.className="overview-payment";
      const title=document.createElement("strong");title.textContent="#"+payment.id+" · "+payment.provider;
      const summary=document.createElement("span");
      summary.textContent=(payment.currency==="XTR"?payment.amount+" ⭐":(payment.amount/100).toFixed(2)+" "+payment.currency)+" · "+payment.status;
      card.append(title,summary);list.append(card);
    }
  } catch { $("dashboard-payments").textContent="Не удалось загрузить последние платежи."; }
}
async function loadUsers(){const d=await api("/admin/api/users?limit="+pageSize+"&offset="+userOffset);$("users-table").replaceChildren();d.items.forEach(u=>{row("users-table",[u.telegram_id,u.username?"@"+u.username:"—",u.subscription_status,u.expires_at?new Date(u.expires_at).toLocaleDateString():"—",new Date(u.created_at).toLocaleDateString()]);const tr=$("users-table").lastElementChild;const button=document.createElement("button");button.className="action";button.textContent="Подробнее";button.onclick=()=>showCustomer(u.telegram_id);tr.appendChild(cell(button.textContent));tr.lastElementChild.replaceChildren(button)});$("users-page").textContent="Показано "+(d.total?userOffset+1:0)+"–"+Math.min(userOffset+d.items.length,d.total)+" из "+d.total;$("users-prev").disabled=userOffset===0;$("users-next").disabled=userOffset+pageSize>=d.total}
async function showCustomer(id){$("status").textContent="";try{const d=await api("/admin/api/users/"+encodeURIComponent(id));$("customer-title").textContent="Клиент "+d.telegram_id;$("customer-summary").textContent=(d.username?"@"+d.username+" · ":"")+"Зарегистрирован: "+new Date(d.created_at).toLocaleString();$("customer-subscriptions").replaceChildren();$("customer-payments").replaceChildren();d.subscriptions.forEach(x=>row("customer-subscriptions",[x.id,x.status,x.expires_at?new Date(x.expires_at).toLocaleString():"—",new Date(x.created_at).toLocaleString()]));d.payments.forEach(x=>row("customer-payments",[x.id,x.provider,x.amount+" "+x.currency,x.status,new Date(x.created_at).toLocaleString()]));$("customer-detail").hidden=false;$("customer-detail").scrollIntoView({behavior:"smooth",block:"nearest"})}catch(e){$("status").textContent=e.message}}
$("customer-close").onclick=()=>{$("customer-detail").hidden=true};
async function loadPayments(){const d=await api("/admin/api/payments?limit="+pageSize+"&offset="+paymentOffset);$("payments-table").replaceChildren();d.items.forEach(p=>row("payments-table",[p.id,p.telegram_id,p.provider,p.amount+" "+p.currency,p.status,new Date(p.created_at).toLocaleString()]));$("payments-page").textContent="Показано "+(d.total?paymentOffset+1:0)+"–"+Math.min(paymentOffset+d.items.length,d.total)+" из "+d.total;$("payments-prev").disabled=paymentOffset===0;$("payments-next").disabled=paymentOffset+pageSize>=d.total}
async function adminApi(method,path,body){const r=await fetch(path,{method,headers:{"X-Telegram-Init-Data":tg?.initData||"","Content-Type":"application/json"},body:body?JSON.stringify(body):undefined});const d=await r.json();if(!r.ok)throw Error(typeof d.detail==="string"?d.detail:"Ошибка запроса");return d}
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
async function loadPlans(){
  if (!modeSquads) {
    const policies = await adminApi("GET", "/admin/api/squad-policies");
    modeSquads = {flexible: new Set(policies.flexible), trial: new Set(policies.trial)};
  }
  await loadSquads();
  const {items} = await adminApi("GET", "/admin/api/plans");
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
    button.onclick = () => fillPlan(plan);
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
  $("plan-title").focus();
}
$("plan-new").onclick = () => fillPlan();
$("plan-cancel").onclick = () => { $("plan-form").hidden = true; };
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
    const saved = await adminApi(id ? "PUT" : "POST", "/admin/api/plans" + (id ? "/" + id : ""), payload);
    fillPlan(saved);
    $("plan-status").textContent = "Тариф сохранён";
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
  $("brand-support").value = d.support_contact;
  $("brand-terms").value = d.terms_url || "";
  renderStoreMark(d.name, d.logo_image_url);
  renderImage("brand-photo", d.welcome_image_url);
  renderImage("brand-logo", d.logo_image_url);
  loadBotIdentity();
}
$("brand-form").onsubmit=async e=>{
  e.preventDefault();try{
    await adminApi("PUT","/admin/api/store-profile",{
      name:$("brand-name").value,tagline:$("brand-tagline").value,
      description:$("brand-description").value,support_contact:$("brand-support").value,terms_url:$("brand-terms").value
    });$("admin-shop-name").textContent=$("brand-name").value;$("status").textContent="Оформление сохранено";
  }catch(e){$("status").textContent=e.message}
};
const providerLabels = {
  telegram_stars: "Telegram Stars", yookassa: "YooKassa",
  yoomoney: "YooMoney", cryptopay: "Crypto Pay",
};
const providerTabs = {
  telegram_stars: "pay-stars", yookassa: "pay-yookassa",
  yoomoney: "pay-yoomoney", cryptopay: "pay-cryptopay",
};
const providerCredentials = {
  yookassa: {shop_id:"pay-yookassa-id", secret_key:"pay-yookassa-secret"},
  yoomoney: {receiver:"pay-yoomoney-receiver", notification_secret:"pay-yoomoney-secret"},
  cryptopay: {api_token:"pay-crypto-token"},
};
const providerSecretInputs = ["pay-yookassa-secret","pay-yoomoney-secret","pay-crypto-token"];
async function loadPaymentSettings() {
  const d = await adminApi("GET", "/admin/api/payment-settings");
  const grid = $("method-list");
  grid.replaceChildren();
  for (const [provider, label] of Object.entries(providerLabels)) {
    const enabled = Boolean(d.methods?.[provider]);
    const configured = Boolean(d.configured?.[provider]);
    const status = !configured ? "Нужны реквизиты" : enabled ? "Активен" : "Выключен";
    $("method-" + provider).checked = enabled;
    $("provider-" + provider + "-state").textContent =
      configured ? (enabled ? "Подключён и включён" : "Настроен, но выключен") : "Не настроен";
    const button = document.createElement("button");
    button.type = "button";
    button.className = "provider-tile";
    button.dataset.openTab = providerTabs[provider];
    const nameElement = document.createElement("strong");
    nameElement.textContent = label;
    const statusElement = document.createElement("span");
    statusElement.textContent = status;
    const arrow = document.createElement("span");
    arrow.setAttribute("aria-hidden", "true");
    arrow.textContent = "→";
    button.append(nameElement, statusElement, arrow);
    button.addEventListener("click", () => openTab(providerTabs[provider]));
    grid.appendChild(button);
  }
}
async function savePaymentProvider(provider) {
  if (!Object.hasOwn(providerLabels,provider)) throw Error("Неизвестная платёжная система");
  const button = document.querySelector('[data-save-provider="' + provider + '"]');
  button.disabled = true;
  try {
    const values = {};
    for (const [key,id] of Object.entries(providerCredentials[provider] || {})) {
      // Sending an empty field never overwrites an encrypted secret in the API.
      const value = $(id).value.trim();
      if (value) values[key] = value;
    }
    const credentials = Object.keys(values).length ? {[provider]:values} : {};
    await adminApi("PUT","/admin/api/payment-settings",{
      methods:{[provider]:$("method-" + provider).checked},
      credentials,
    });
    for (const id of providerSecretInputs) $(id).value = "";
    $("status").textContent = providerLabels[provider] + ": настройки сохранены";
    await loadPaymentSettings();
  } catch(error) {
    $("status").textContent = error.message;
  } finally {
    button.disabled = false;
  }
}
document.querySelectorAll("[data-save-provider]").forEach(button=>{
  button.addEventListener("click",()=>savePaymentProvider(button.dataset.saveProvider));
});
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
const sidebarIconNames = {
  overview:"grid", plans:"tag", users:"users", payments:"card",
  bonuses:"ticket", referrals:"gift", brand:"palette", trial:"clock",
  pricing:"calc", settings:"wallet", "pay-stars":"star",
  "pay-yookassa":"card", "pay-yoomoney":"coins", "pay-cryptopay":"crypto",
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
  adminBackdrop.hidden=!open;
  adminMenuOpen.setAttribute("aria-expanded",String(open));
  if(open){adminReturnFocus=document.activeElement; adminSidebar.querySelector('[data-tab][aria-current="page"]')?.focus();}
  else if(adminReturnFocus?.isConnected){adminReturnFocus.focus({preventScroll:true});adminReturnFocus=null;}
}
adminMenuOpen.onclick=()=>setAdminMenu(true);
$("admin-menu-close").onclick=()=>setAdminMenu(false);
adminBackdrop.onclick=()=>setAdminMenu(false);
document.addEventListener("keydown",e=>{if(e.key==="Escape"&&adminSidebar.classList.contains("is-open"))setAdminMenu(false);});
const tabs = {
  overview: {sections: ["overview-view"], load: overview},
  plans: {sections: ["plans-view"], load: loadPlans},
  users: {sections: ["users-view"], load: loadUsers},
  payments: {sections: ["payments-view"], load: loadPayments},
  bonuses: {sections: ["promos-view"], load: loadPromos},
  referrals: {sections: ["referrals-view"], load: loadReferrals},
  brand: {sections: ["brand-view"], load: loadBrand},
  trial: {sections: ["trial-view"], load: loadTrialSettings},
  pricing: {sections: ["pricing-view"], load: loadPricing},
  settings: {sections: ["payment-settings-view"], load: loadPaymentSettings},
  "pay-stars": {sections: ["payment-telegram_stars-view"], load: loadPaymentSettings},
  "pay-yookassa": {sections: ["payment-yookassa-view"], load: loadPaymentSettings},
  "pay-yoomoney": {sections: ["payment-yoomoney-view"], load: loadPaymentSettings},
  "pay-cryptopay": {sections: ["payment-cryptopay-view"], load: loadPaymentSettings},
};

async function openTab(requested) {
  const folder=$("settings-accordion");
  if (requested==="settings" || requested==="trial" || requested==="pricing" || requested?.startsWith("pay-")) folder.open=true;
  const legacy = {promos: "bonuses", tariffs: "pricing", payment_methods: "settings"};
  const key = Object.hasOwn(legacy, requested) ? legacy[requested] : requested;
  const tab = Object.hasOwn(tabs, key) ? tabs[key] : tabs.overview;
  $("status").textContent = "";
  for (const [name, item] of Object.entries(tabs)) {
    for (const section of item.sections) $(section).hidden = tab !== item;
    const button = document.querySelector(`nav [data-tab="${name}"]`);
    if (tab === item) {
      button.setAttribute("aria-current", "page");
      $("admin-mobile-title").textContent=button.textContent.trim();
      if(window.matchMedia("(min-width: 851px)").matches)button.scrollIntoView({block: "nearest", inline: "nearest"});
    } else button.removeAttribute("aria-current");
  }
  if(adminSidebar.classList.contains("is-open"))setAdminMenu(false);
  try { await tab.load?.(); }
  catch (error) { $("status").textContent = error.message; }
}

async function start() {
  try {
    if (!tg?.initData) throw Error("Откройте панель через Telegram Mini App");
    const d = await api("/admin/api/overview");
    $("welcome").textContent = d.admin_name ? "Здравствуйте, " + d.admin_name : "Состояние магазина";
    $("loading").hidden = true;
    $("dashboard").hidden = false;
    const initial = new URLSearchParams(window.location.search).get("tab") || "overview";
    await openTab(initial);
    if (initial !== "brand") {
      try {
        const profile = await api("/admin/api/store-profile");
        renderStoreMark(profile.name, profile.logo_image_url);
      } catch { /* Keep the default wordmark if the profile is unavailable. */ }
    }
  } catch (error) {
    $("loading").textContent = error.message;
  }
}

document.querySelectorAll("[data-tab], [data-open-tab]").forEach(button => {
  button.addEventListener("click", () => openTab(button.dataset.tab || button.dataset.openTab));
});
$("users-refresh").onclick=()=>loadUsers().catch(e=>$("status").textContent=e.message);$("payments-refresh").onclick=()=>loadPayments().catch(e=>$("status").textContent=e.message);
$("users-prev").onclick=()=>{userOffset=Math.max(0,userOffset-pageSize);loadUsers()};$("users-next").onclick=()=>{userOffset+=pageSize;loadUsers()};
$("payments-prev").onclick=()=>{paymentOffset=Math.max(0,paymentOffset-pageSize);loadPayments()};$("payments-next").onclick=()=>{paymentOffset+=pageSize;loadPayments()};
start();
