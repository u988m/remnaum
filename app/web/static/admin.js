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
let currentPhotoPreviewUrl=null;
function renderWelcomePhoto(url){
  const image=$("brand-photo-preview");
  const placeholder=$("brand-photo-placeholder");
  if(url){image.src=url;image.hidden=false;placeholder.hidden=true}
  else{image.removeAttribute("src");image.hidden=true;placeholder.hidden=false}
}
function readImageAsDataUrl(file){
  return new Promise((resolve,reject)=>{
    const reader=new FileReader();
    reader.onload=()=>resolve(String(reader.result||""));
    reader.onerror=()=>reject(Error("Не удалось прочитать фотографию"));
    reader.readAsDataURL(file);
  });
}
$("brand-photo").addEventListener("change",()=>{
  const file=$("brand-photo").files?.[0];
  if(currentPhotoPreviewUrl)URL.revokeObjectURL(currentPhotoPreviewUrl);
  if(file){currentPhotoPreviewUrl=URL.createObjectURL(file);renderWelcomePhoto(currentPhotoPreviewUrl)}
});
$("brand-photo-upload").onclick=async()=>{
  const file=$("brand-photo").files?.[0];
  if(!file){$("brand-photo-status").textContent="Сначала выберите изображение";return}
  if(!["image/jpeg","image/png","image/webp"].includes(file.type)||file.size>5*1024*1024){
    $("brand-photo-status").textContent="Допустимы JPG, PNG или WebP до 5 МБ";return
  }
  $("brand-photo-upload").disabled=true;
  $("brand-photo-status").textContent="Обрабатываем изображение…";
  try{
    const data_base64=await readImageAsDataUrl(file);
    const result=await adminApi("PUT","/admin/api/store-profile/welcome-image",{data_base64});
    $("brand-photo-status").textContent="Обложка опубликована в боте и Mini App";
    $("brand-photo").value="";
    renderWelcomePhoto(result.url);
  }catch(e){$("brand-photo-status").textContent=e.message}
  $("brand-photo-upload").disabled=false;
};
$("brand-photo-remove").onclick=async()=>{
  $("brand-photo-remove").disabled=true;
  try{
    await adminApi("DELETE","/admin/api/store-profile/welcome-image");
    $("brand-photo").value="";
    renderWelcomePhoto(null);
    $("brand-photo-status").textContent="Обложка удалена";
  }catch(e){$("brand-photo-status").textContent=e.message}
  $("brand-photo-remove").disabled=false;
};
async function loadBotIdentity(){
  try{
    const info=await adminApi("GET","/admin/api/bot-identity");
    $("bot-identity").textContent=(info.name||"Бот")+" · "+(info.username?"@"+info.username:"username не определён");
  }catch{$("bot-identity").textContent="Не удалось получить данные бота"}
}
async function loadBrand(){
  const d=await adminApi("GET","/admin/api/store-profile");
  $("brand-name").value=d.name;$("brand-tagline").value=d.tagline;$("brand-description").value=d.description;
  $("brand-support").value=d.support_contact;$("brand-terms").value=d.terms_url||"";$("brand-color").value=d.accent_color;
  $("admin-shop-name").textContent=d.name;
  renderWelcomePhoto(d.welcome_image_url);
  await loadBotIdentity();
}
$("brand-form").onsubmit=async e=>{
  e.preventDefault();try{
    await adminApi("PUT","/admin/api/store-profile",{
      name:$("brand-name").value,tagline:$("brand-tagline").value,
      description:$("brand-description").value,support_contact:$("brand-support").value,terms_url:$("brand-terms").value,
      accent_color:$("brand-color").value
    });$("admin-shop-name").textContent=$("brand-name").value;$("status").textContent="Оформление сохранено";
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
async function start(){try{if(!tg?.initData)throw Error("Откройте панель через Telegram Mini App");const d=await api("/admin/api/overview");$("loading").hidden=true;$("dashboard").hidden=false;$("welcome").textContent="Здравствуйте, "+(d.admin_name||"администратор");$("users-count").textContent=d.users;$("active-count").textContent=d.active_subscriptions;$("paid-count").textContent=d.successful_payments;$("pending-count").textContent=d.pending_payments;await loadUsers();await loadBrand()}catch(e){$("loading").textContent=e.message}}
document.querySelectorAll("nav button[data-tab]").forEach(b=>b.addEventListener("click",async()=>{document.querySelectorAll("nav button").forEach(x=>x.classList.remove("active"));b.classList.add("active");const tab=b.dataset.tab;$("users-view").hidden=tab!=="users";$("payments-view").hidden=tab!=="payments";$("plans-view").hidden=tab!=="plans";$("brand-view").hidden=tab!=="brand";$("referrals-view").hidden=tab!=="referrals";$("trial-view").hidden=tab!=="trial";$("promos-view").hidden=tab!=="promos";$("payment-settings-view").hidden=tab!=="settings";$("pricing-view").hidden=tab!=="tariffs";try{if(tab==="payments")await loadPayments();if(tab==="plans")await loadPlans();if(tab==="brand")await loadBrand();if(tab==="referrals")await loadReferrals();if(tab==="trial")await loadTrialSettings();if(tab==="promos")await loadPromos();if(tab==="settings")await loadPaymentSettings();if(tab==="tariffs")await loadPricing()}catch(e){$("status").textContent=e.message}}));
$("users-refresh").onclick=()=>loadUsers().catch(e=>$("status").textContent=e.message);$("payments-refresh").onclick=()=>loadPayments().catch(e=>$("status").textContent=e.message);
$("users-prev").onclick=()=>{userOffset=Math.max(0,userOffset-pageSize);loadUsers()};$("users-next").onclick=()=>{userOffset+=pageSize;loadUsers()};
$("payments-prev").onclick=()=>{paymentOffset=Math.max(0,paymentOffset-pageSize);loadPayments()};$("payments-next").onclick=()=>{paymentOffset+=pageSize;loadPayments()};
start();
