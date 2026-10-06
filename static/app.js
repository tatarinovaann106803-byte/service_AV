"use strict";
const form = document.querySelector("#project-form");
const $ = id => document.getElementById(id);
const fmt = (v,d=0) => v==null ? "—" : new Intl.NumberFormat("ru-RU",{maximumFractionDigits:d}).format(v);
let catalog={},result=null,inputSignature=null,currentVariant=null;
const monthNames=["Январь","Февраль","Март","Апрель","Май","Июнь","Июль","Август","Сентябрь","Октябрь","Ноябрь","Декабрь"];
for(const [id,selected] of [["season-start",4],["season-end",9]])monthNames.forEach((name,i)=>{const option=document.createElement("option");option.value=String(i+1);option.textContent=name;option.selected=i+1===selected;$(id).append(option);});
const hazards={frost:"Заморозки",drought:"Засуха",hail:"Град"};
const hazardFields={probability_pct:"Вероятность события, %/год",loss_fraction_pct:"Потеря при событии, %",probability_reduction_pct:"Предельное снижение вероятности, %",severity_reduction_pct:"Предельное снижение тяжести, %"};
for(const [key,name] of Object.entries(hazards)){
 const section=document.createElement("div");section.className="hazard";
 const title=document.createElement("h3");title.textContent=name;section.append(title);
 const fields=document.createElement("div");fields.className="fields";
 for(const [field,text] of Object.entries(hazardFields)){
  const label=document.createElement("label");label.textContent=text;
  const input=document.createElement("input");Object.assign(input,{type:"number",name:`${key}_${field}`,min:field.includes("reduction")?"-100":"0",max:"100",step:"any",value:"0"});label.append(input);fields.append(label);
 }
 section.append(fields);$("hazards").append(section);
}
function toggle(id,visible){const el=$(id);el.hidden=!visible;el.querySelectorAll("input,select").forEach(x=>{x.disabled=!visible;x.required=visible&&x.type!=="checkbox";});}
function sync(){
 const crop=$("sector").value==="crop";
 document.querySelectorAll(".crop-only").forEach(s=>{s.hidden=!crop;s.querySelectorAll("input").forEach(i=>i.disabled=!crop);});
 toggle("baseline-mode-field",crop);toggle("baseline-field",!crop||$("baseline-mode").value==="manual");
 toggle("water-fields",crop&&$("water-enabled").checked);toggle("risks-fields",crop&&$("risks-enabled").checked);
 toggle("wacc-fields",$("capital-method").value==="wacc");toggle("direct-rate",$("capital-method").value==="direct");
 const automatic=$("weather-source").value==="nasa";
 $("load-weather").hidden=!automatic;
 for(const name of ["radiation_annual","growing_season_temperature_c","growing_season_rainfall_mm"]){const field=form.elements[name];field.readOnly=automatic;field.required=!automatic;if(automatic&&!field.dataset.loaded)field.value="";}
 $("weather-status").textContent=automatic?"Погода загружается по координатам и выбранному сезону.":"Введите погодные данные за выбранный сезон.";
}
function updateCatalog(){
 const previous=$("product-list").value;
 $("product-list").replaceChildren();for(const item of catalog[$("sector").value]||[]){const op=document.createElement("option");op.value=item.name;op.textContent=item.name;$("product-list").append(op);}
 const names=(catalog[$("sector").value]||[]).map(item=>item.name);
 if(names.includes(previous))$("product-list").value=previous;
 else if(names.includes("Пшеница"))$("product-list").value="Пшеница";
}
for(const id of ["baseline-mode","water-enabled","risks-enabled","capital-method","weather-source"])$(id).addEventListener("change",sync);
$("sector").addEventListener("change",()=>{
 const s=$("sector").value;
 const cfg={crop:["Пшеница",4000,15,"Базовая продуктивность, кг/га/год","Цена продукции, руб/кг"],aqua:["Белоногая креветка",5,150,"Базовая продуктивность, т/га/год","Цена продукции, руб/кг"],forest:["Тополь",5,5000,"Базовая продуктивность, м³/га/год","Цена продукции, руб/м³"]}[s];
 ["product_name","base_productivity","product_price"].forEach((k,i)=>form.elements[k].value=cfg[i]);$("yield-label").textContent=cfg[3];$("price-label").textContent=cfg[4];
 $("baseline-note").textContent=s==="crop"?"Средняя урожайность страны — ориентир. Если есть данные вашего поля, выберите ручной ввод.":"Укажите базовую продуктивность вашего хозяйства.";
 updateCatalog();sync();
});
form.addEventListener("input",()=>{if(result){$("stale").hidden=false;$("choose").disabled=true;}});
fetch("/products").then(r=>r.json()).then(data=>{catalog=data;updateCatalog();}).catch(()=>{showError("Не удалось загрузить список культур. Обновите страницу.");$("calculate").disabled=true;});
sync();
function payload(){
 const f=new FormData(form),n=k=>Number(f.get(k));
 const r={sector:f.get("sector"),product_name:f.get("product_name"),country:f.get("country")};
 for(const k of ["lat","lon","area_ha","product_price","energy_price","project_years","weather_year","season_start_month","season_end_month","irrigation_mm"])r[k]=n(k);
 if(r.sector!=="crop"||f.get("baseline_mode")==="manual")r.base_productivity=n("base_productivity");
 if(f.get("budget_rub"))r.budget_rub=n("budget_rub");
 if(f.get("weather_source")==="manual"){for(const key of ["radiation_annual","growing_season_temperature_c","growing_season_rainfall_mm"])r[key]=n(key);}
 r.capital={method:f.get("capital_method")};
 const fields=r.capital.method==="direct"?["discount_rate_pct"]:["equity_share_pct","cost_of_equity_pct","cost_of_debt_pct","tax_rate_pct"];
 fields.forEach(k=>r.capital[k]=n(k));
 if(r.sector==="crop"&&f.has("water_enabled")){r.water={};["baseline_et_mm","soil_evaporation_mm","effective_rainfall_mm","irrigation_efficiency","water_cost_rub_m3"].forEach(k=>r.water[k]=n(k));}
 if(r.sector==="crop"&&f.has("risks_enabled")){
  r.risks={events_are_mutually_exclusive:f.has("events_are_mutually_exclusive"),include_in_cash_flow:f.has("include_in_cash_flow")};
  for(const key of Object.keys(hazards)){r.risks[key]={};for(const field of Object.keys(hazardFields))r.risks[key][field]=n(`${key}_${field}`);}
 }
 return r;
}
function errorText(detail){return Array.isArray(detail)?detail.map(e=>`${e.loc.slice(1).join(".")}: ${e.msg}`).join("; "):typeof detail==="string"?detail:"Не удалось завершить действие";}
async function call(url,body){const response=await fetch(url,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});const data=await response.json();if(!response.ok)throw new Error(errorText(data.detail));return data;}
function showError(message){$("error").textContent=message;$("error").hidden=false;}
function effect(label,value){const row=document.createElement("div"),dt=document.createElement("dt"),dd=document.createElement("dd");dt.textContent=label;dd.textContent=value;row.append(dt,dd);$("effects").append(row);}
function preview(index){
 currentVariant=result.variants[index];const v=currentVariant,e=v.economics;
 $("variant-title").textContent=`Просмотр варианта ${index+1}`;
 $("yield").textContent=fmt(v.productivity.estimated,1);$("yield-change").textContent=`${v.productivity.unit} · ${fmt(v.productivity.change_pct,1)}% к базе`;
 $("generation").textContent=fmt(v.energy.annual_generation_kwh/1000,1)+" МВт·ч";$("power").textContent=fmt(v.energy.installed_power_kw,1)+" кВт установленной мощности";
 $("npv").textContent=fmt(e.npv_rub/1e6,2)+" млн ₽";$("rate-used").textContent=`${fmt(e.discount_rate_pct,2)}% · ${e.project_years} лет`;
 $("income").textContent=fmt(e.net_income/1e6,2)+" млн ₽";
 $("effects").replaceChildren();effect("Инвестиции",fmt(e.capex)+" ₽");effect("Простая окупаемость",e.roi_years==null?"Не достигнута":fmt(e.roi_years,1)+" лет");effect("Дисконтированная окупаемость",e.discounted_payback_years==null?"Не достигнута":fmt(e.discounted_payback_years,1)+" лет");
 if(v.water.status!=="not_configured"){effect("Экономия воды",fmt(v.water.saved_m3_year)+" м³/год");effect("Снижение полива",fmt(v.water.irrigation_reduction_pct,1)+"%");effect("Экономия на поливе",fmt(v.water.annual_savings_rub)+" ₽/год");}
 $("risk-results").replaceChildren();if(v.risks.perils){for(const [key,p] of Object.entries(v.risks.perils)){const text=document.createElement("p");text.className="hint";text.textContent=`${hazards[key]}: ${fmt(p.baseline_probability_pct,2)}% → ${fmt(p.av_probability_pct,2)}%. Изменение ожидаемых потерь: ${fmt(p.avoided_expected_loss_rub)} ₽/год.`;$("risk-results").append(text);}}
 $("robustness-note").textContent=`NPV от ${fmt(v.robustness.npv_min/1e6,2)} до ${fmt(v.robustness.npv_max/1e6,2)} млн ₽. Неотрицательный NPV в ${v.robustness.positive_npv_scenarios} из ${v.robustness.scenario_count} сценариев.`;
 $("scenario-table").replaceChildren();for(const s of v.scenarios){const tr=document.createElement("tr");for(const text of [s.name,fmt(s.productivity,1),fmt(s.generation_kwh/1000,1),fmt(s.npv_rub/1e6,2)]){const td=document.createElement("td");td.textContent=text;tr.append(td);}$("scenario-table").append(tr);}
 $("selection-status").textContent=result.selected_variant_id===v.id?"Вы выбрали этот вариант.":"Просмотр не меняет ваш выбор. Нажмите кнопку, чтобы сохранить решение.";
 $("choose").textContent=result.selected_variant_id===v.id?"Этот вариант выбран":"Выбрать этот вариант";
 $("choose").disabled=JSON.stringify(payload())!==inputSignature;
 document.querySelectorAll("#variant-buttons button").forEach((b,i)=>{b.classList.toggle("active",i===index);b.setAttribute("aria-pressed",String(i===index));});
}
function draw(){
 const svg=$("pareto-chart");svg.replaceChildren();const ns="http://www.w3.org/2000/svg";
 const energy=result.variants.map(v=>v.energy.annual_generation_kwh/1000),yieldValues=result.variants.map(v=>v.productivity.estimated);
 const xmin=Math.min(...energy),xmax=Math.max(...energy),ymin=Math.min(...yieldValues),ymax=Math.max(...yieldValues);
 const x=v=>65+(v-xmin)/(xmax-xmin||1)*405,y=v=>190-(v-ymin)/(ymax-ymin||1)*150;
 const el=(tag,attrs,text)=>{const e=document.createElementNS(ns,tag);Object.entries(attrs).forEach(([k,v])=>e.setAttribute(k,String(v)));if(text)e.textContent=text;svg.append(e);return e;};
 el("path",{d:"M65 25V190H480",fill:"none",stroke:"#acbbab"});
 el("text",{x:65,y:225,"font-size":11,fill:"#66776e"},`Выработка: ${fmt(xmin)} → ${fmt(xmax)} МВт·ч/год`);
 el("text",{x:65,y:15,"font-size":11,fill:"#66776e"},`Продуктивность: ${fmt(ymin,1)} → ${fmt(ymax,1)} ${result.variants[0].productivity.unit}`);
 result.variants.forEach((v,i)=>{const point=el("circle",{cx:x(energy[i]),cy:y(yieldValues[i]),r:7,fill:v.economics.npv_rub>=0?"#266749":"#ac8048",tabindex:0,role:"button","aria-label":`Посмотреть вариант ${i+1}`});point.addEventListener("click",()=>preview(i));point.addEventListener("keydown",event=>{if(event.key==="Enter"||event.key===" "){event.preventDefault();preview(i);}});const t=document.createElementNS(ns,"title");t.textContent=`Вариант ${i+1}: NPV ${fmt(v.economics.npv_rub)} ₽`;point.append(t);});
}
function render(data){
 result=data;$("result").hidden=false;$("stale").hidden=true;
 $("result-title").textContent=`${data.product_name} · ${data.variants.length} вариантов`;
 $("result-note").textContent="Сравните продуктивность, выработку и доходность. Выберите подходящий вариант.";
 $("variant-buttons").replaceChildren();data.variants.forEach((v,i)=>{const button=document.createElement("button");button.type="button";button.textContent=String(i+1);button.setAttribute("aria-label",`Вариант ${i+1}`);button.addEventListener("click",()=>preview(i));$("variant-buttons").append(button);});
 $("comparison-table").replaceChildren();data.variants.forEach((v,i)=>{const tr=document.createElement("tr");const td=document.createElement("td"),button=document.createElement("button");button.type="button";button.className="table-variant";button.textContent=String(i+1);button.setAttribute("aria-label",`Открыть вариант ${i+1}`);button.addEventListener("click",()=>preview(i));td.append(button);tr.append(td);for(const text of [fmt(v.productivity.estimated,1),fmt(v.energy.annual_generation_kwh/1000,1),fmt(v.economics.npv_rub/1e6,2),fmt(v.economics.net_income/1e6,2)]){const cell=document.createElement("td");cell.textContent=text;tr.append(cell);}$("comparison-table").append(tr);});

 $("calculation-id").textContent=`Номер расчёта: ${data.calculation_id}`;draw();preview(0);
}
form.addEventListener("submit",async event=>{
 event.preventDefault();$("calculate").disabled=true;$("progress").hidden=false;$("error").hidden=true;
 const request=payload(),signature=JSON.stringify(request);
 try{const response=await call("/calculate",request);inputSignature=signature;render(response.data);if($("weather-source").value==="nasa"&&JSON.stringify(payload())===signature){for(const [key,value] of Object.entries(response.data.weather)){form.elements[key].value=Number(value.toFixed(2));form.elements[key].dataset.loaded="true";}}if(JSON.stringify(payload())!==signature)$("stale").hidden=false;}
 catch(error){showError(error.message);}finally{$("calculate").disabled=false;$("progress").hidden=true;}
});
$("choose").addEventListener("click",async()=>{
 if(!result||!currentVariant)return;
 if(JSON.stringify(payload())!==inputSignature){showError("Данные изменены. Сначала пересчитайте варианты.");return;}
 $("choose").disabled=true;
 try{const answer=await call(`/calculations/${result.calculation_id}/selection`,{variant_id:currentVariant.id});result.selected_variant_id=answer.selected_variant_id;preview(result.variants.findIndex(v=>v.id===currentVariant.id));}
 catch(error){showError(error.message);$("choose").disabled=false;}
});
$("download").addEventListener("click",()=>{if(!result)return;const url=URL.createObjectURL(new Blob([JSON.stringify(result,null,2)],{type:"application/json"}));const a=document.createElement("a");a.href=url;a.download="agrivoltaic-options.json";a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});

$("load-weather").addEventListener("click",async()=>{
 $("load-weather").disabled=true;$("weather-status").textContent="Загружаем погодные данные…";
 const signature=weatherSignature();
 try{const params=new URLSearchParams({lat:form.elements.lat.value,lon:form.elements.lon.value,year:form.elements.weather_year.value,start_month:$("season-start").value,end_month:$("season-end").value});
 const response=await fetch(`/weather?${params}`),data=await response.json();if(!response.ok)throw new Error(errorText(data.detail));
 if(signature!==weatherSignature()||$("weather-source").value!=="nasa")return;
 for(const [key,value] of Object.entries(data)){form.elements[key].value=Number(value.toFixed(2));form.elements[key].dataset.loaded="true";}
 $("weather-status").textContent="Погодные данные загружены.";
 }catch(error){$("weather-status").textContent=error.message;}finally{$("load-weather").disabled=false;}
});
function weatherSignature(){return ["lat","lon","weather_year","season_start_month","season_end_month"].map(k=>form.elements[k].value).join("|");}
for(const name of ["lat","lon","weather_year","season_start_month","season_end_month"])form.elements[name].addEventListener("input",()=>{if($("weather-source").value==="nasa"){for(const key of ["radiation_annual","growing_season_temperature_c","growing_season_rainfall_mm"]){form.elements[key].value="";delete form.elements[key].dataset.loaded;}$("weather-status").textContent="Условия изменены. Загрузите погоду заново или начните расчёт.";}});

// Only the configured Tilda parent may supply map coordinates.
fetch("/integration/config").then(r=>r.json()).then(config=>{
 const parents=new Set(config.parent_origins);
 window.addEventListener("message",event=>{
  if(window.parent===window||event.source!==window.parent||!parents.has(event.origin))return;
  const data=event.data;
  if(!data||data.type!=="agrivoltaic:location"||typeof data.lat!=="number"||typeof data.lon!=="number"||!Number.isFinite(data.lat)||!Number.isFinite(data.lon)||Math.abs(data.lat)>90||Math.abs(data.lon)>180)return;
  form.elements.lat.value=data.lat.toFixed(6);form.elements.lon.value=data.lon.toFixed(6);
  form.elements.lat.dispatchEvent(new Event("input",{bubbles:true}));
  if($("weather-source").value==="nasa")$("load-weather").click();
 });
 if(window.parent!==window){for(const origin of parents)window.parent.postMessage({type:"agrivoltaic:ready"},origin);}
}).catch(()=>{});
