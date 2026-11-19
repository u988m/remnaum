const $=id=>document.getElementById(id);let selected=null;let store=null;let me=null;
async function request(path,options){const r=await fetch(path,options);const data=await r.json();if(!r.ok)throw Error(data.detail||"Ошибка");return data}
async function init(){
try{
 store=await request("/api/v1/store/catalog");
 $("name").textContent=store.profile.name;$("tagline").textContent=store.profile.tagline||"Выберите тариф";
 $("description").textContent=store.profile.description;
 document.documentElement.style.setProperty("--accent",store.profile.accent_color);
 $("terms").closest("label").hidden=!store.terms_url;
 if(store.terms_url)$("terms-url").href=store.terms_url;
 const list=$("plans");list.replaceChildren();
 if(!store.plans.length){list.textContent="Тарифы пока не опубликованы."}
 for(const p of store.plans){
   const card=document.createElement("article");card.className="card";
   const name=document.createElement("h3");name.textContent=p.title;
   const desc=document.createElement("p");desc.className="small";desc.textContent=p.description||"Выберите подходящую подписку";
   const spec=document.createElement("p");spec.className="small";spec.textContent=p.duration_days+" дней · "+(p.traffic_gb===0?"Безлимит":p.traffic_gb+" ГБ")+" · "+p.device_limit+" устройств";
   const price=document.createElement("div");price.className="price";price.textContent=(p.price_rub_minor/100).toFixed(2)+" ₽";
   const choose=document.createElement("button");choose.textContent="Выбрать тариф";
   choose.onclick=()=>{selected=p;document.querySelectorAll(".card button").forEach(b=>b.textContent="Выбрать тариф");choose.textContent="✓ Выбрано";$("buy").disabled=!me||!store.methods.length||p.price_rub_minor<=0;$("account").scrollIntoView({behavior:"smooth"})};
   card.append(name,desc,spec,price,choose);list.append(card);
 }
 const provider=$("provider");provider.replaceChildren();
 store.methods.forEach(p=>{const el=document.createElement("option");el.value=p;el.textContent=({"yookassa":"ЮKassa","yoomoney":"ЮMoney","cryptopay":"Crypto Pay","platega":"Platega"})[p]||p;provider.append(el)});
 try{me=await request("/api/v1/store/me");$("login").style.display="none";$("account").style.display="block";$("hello").textContent="Здравствуйте, "+(me.first_name||"покупатель")+"!"}catch(e){$("login").style.display="block"}
 if(!store.methods.length){$("status").textContent="Внешние способы оплаты пока не настроены владельцем магазина."}
}catch(e){$("plans").textContent=e.message}}
$("buy").onclick=async()=>{try{
 if(!selected||!me)throw Error("Сначала войдите в аккаунт и выберите тариф");
 if(store.terms_url&&!$("terms").checked)throw Error("Прочитайте и примите условия продажи");
 $("buy").disabled=true;$("status").textContent="Создаём платёж…";
 const response=await request("/api/v1/store/checkout",{method:"POST",headers:{"Content-Type":"application/json"},
   body:JSON.stringify({plan_id:selected.id,provider:$("provider").value,accepted_terms:$("terms").checked})});
 if(!response.url.startsWith("https://"))throw Error("Платёжный провайдер вернул неправильную ссылку");
 window.location.assign(response.url);
}catch(e){$("status").textContent=e.message;$("buy").disabled=false}};
init();
