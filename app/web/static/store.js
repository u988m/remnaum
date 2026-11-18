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
 if(me){
   $("purchases").hidden=false;
   if(window.location.hash==="#purchases")$("purchases").scrollIntoView();
   loadStorePurchases();
 }
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
let purchaseRefresh;
async function loadStorePurchases(remainingChecks = 12) {
  clearTimeout(purchaseRefresh);
  try {
    const data = await request("/api/v1/store/purchases");
    const list = $("store-purchase-list");
    list.replaceChildren();
    for (const purchase of data.items) {
      const entry = document.createElement("li");
      entry.textContent = "#" + purchase.id + " · " + formatPaymentAmount(purchase.amount, purchase.currency)
        + " · " + paymentStatusLabel(purchase.status);
      list.append(entry);
    }
    const activating = data.items.some(purchase => purchase.status === "paid");
    $("store-payment-status").textContent = activating
      ? "Оплата получена. Вы получите уведомление в Telegram, когда подписка будет готова."
      : data.items.length ? "" : "Покупок пока нет.";
    const subscription = data.subscription;
    $("store-subscription").textContent = subscription?.expires_at
      ? "Срок подписки до " + new Date(subscription.expires_at).toLocaleString("ru-RU") : "";
    const subscriptionUrl = subscription?.subscription_url;
    const canConnect = subscription?.status === "active" && subscriptionUrl?.startsWith("https://");
    $("store-connect").hidden = !canConnect;
    if (canConnect) $("store-connect").href = subscriptionUrl;
    else $("store-connect").removeAttribute("href");
    if (remainingChecks > 0 && data.items.some(purchase => ["pending", "paid"].includes(purchase.status))) {
      purchaseRefresh = setTimeout(() => loadStorePurchases(remainingChecks - 1), 5000);
    }
  } catch (error) {
    $("store-payment-status").textContent = error.message;
  }
}
$("store-refresh").onclick = () => loadStorePurchases();
window.addEventListener("pageshow", event => {
  if (event.persisted && me) loadStorePurchases();
});

init();
