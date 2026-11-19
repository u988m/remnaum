const $ = id => document.getElementById(id);
const price = new Intl.NumberFormat("ru-RU", {style: "currency", currency: "RUB"});
const providers = {yookassa: "ЮKassa", yoomoney: "ЮMoney", cryptopay: "Crypto Pay", platega: "Platega"};
let catalog;
let selectedPlan;
let signedIn = false;
let paying = false;

async function request(path, options) {
  const response = await fetch(path, options);
  const result = await response.json();
  if (!response.ok) throw Error(typeof result.detail === "string" ? result.detail : "Ошибка запроса");
  return result;
}

function setAccent(hex) {
  if (!/^#[\da-f]{6}$/i.test(hex)) return;
  const linear = [1, 3, 5].map(index => {
    const value = parseInt(hex.slice(index, index + 2), 16) / 255;
    return value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4;
  });
  const luminance = .2126 * linear[0] + .7152 * linear[1] + .0722 * linear[2];
  document.documentElement.style.setProperty("--store-action-bg", hex);
  document.documentElement.style.setProperty("--store-action-fg", luminance >= .17913 ? "#000" : "#fff");
}

function updateCheckout() {
  $("selected-plan").textContent = selectedPlan
    ? selectedPlan.title + " · " + price.format(selectedPlan.price_rub_minor / 100)
    : "Выберите тариф.";
  $("buy").disabled = paying || !signedIn || !selectedPlan || !catalog.methods.length;
}

function showPlans() {
  const list = $("plans");
  list.replaceChildren();
  for (const plan of catalog.plans) {
    if (plan.price_rub_minor <= 0) continue;
    const card = document.createElement("label");
    card.className = "store-plan";
    const name = document.createElement("strong");
    name.textContent = plan.title;
    const choice = document.createElement("input");
    choice.type = "radio";
    choice.name = "plan";
    choice.value = plan.id;
    choice.setAttribute("aria-label", plan.title);
    const specification = document.createElement("span");
    specification.className = "muted";
    const traffic = plan.traffic_gb ? plan.traffic_gb + " ГБ" : "Безлимит";
    specification.textContent = plan.duration_days + " дн. · " + traffic + " · устройств: " + plan.device_limit;
    const amount = document.createElement("strong");
    amount.className = "price";
    amount.textContent = price.format(plan.price_rub_minor / 100);
    card.append(name, choice, specification);
    if (plan.description) {
      const description = document.createElement("span");
      description.className = "muted";
      description.textContent = plan.description;
      card.append(description);
    }
    card.append(amount);
    choice.onchange = () => {
      selectedPlan = plan;
      updateCheckout();
    };
    list.append(card);
  }
  if (!list.childElementCount) list.textContent = "Тарифы для оплаты на сайте пока не опубликованы.";
}

async function init() {
  try {
    catalog = await request("/api/v1/store/catalog");
    $("name").textContent = catalog.profile.name;
    const description = catalog.profile.description || catalog.profile.tagline;
    if (description) {
      $("description").textContent = description;
      $("description").hidden = false;
    }
    setAccent(catalog.profile.accent_color);
    if (catalog.terms_url) {
      $("terms").closest("label").hidden = false;
      $("terms").required = true;
      $("terms-url").href = catalog.terms_url;
    }
    showPlans();
    for (const method of catalog.methods) $("provider").add(new Option(providers[method] || method, method));
    if (!catalog.methods.length) {
      $("checkout").hidden = true;
      $("status").textContent = "Оплата на сайте пока недоступна.";
    }
    try {
      const user = await request("/api/v1/store/me");
      signedIn = true;
      $("hello").textContent = user.first_name ? "Аккаунт: " + user.first_name : "Telegram подключён";
      $("account").hidden = false;
      updateCheckout();
    } catch {
      $("login").hidden = false;
    }
  } catch (error) {
    $("plans").textContent = error.message;
  }
}

$("checkout").onsubmit = async event => {
  event.preventDefault();
  if ($("buy").disabled) return;
  paying = true;
  $("plans").disabled = true;
  updateCheckout();
  $("status").textContent = "Создаём платёж…";
  try {
    const response = await request("/api/v1/store/checkout", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({plan_id: selectedPlan.id, provider: $("provider").value, accepted_terms: $("terms").checked}),
    });
    if (!response.url.startsWith("https://")) throw Error("Платёжный провайдер вернул неправильную ссылку");
    window.location.assign(response.url);
  } catch (error) {
    $("status").textContent = error.message;
    paying = false;
    $("plans").disabled = false;
    updateCheckout();
  }
};
init();
