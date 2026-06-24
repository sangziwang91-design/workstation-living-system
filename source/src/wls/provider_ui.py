from __future__ import annotations


PROVIDER_HUB_HTML = """<!doctype html>
<html lang='zh-CN'>
<head>
<meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<meta name='wls-session' content='{{SESSION}}'>
<title>WLS Provider Hub</title>
<link rel='stylesheet' href='/assets/provider-hub.css'>
</head>
<body>
<div class='shell'>
<header class='top'>
  <div><h1>WLS Provider Hub</h1><p>本地供应商配置、模型目录、连通性检测与候选切换页面。供应商输出仍然只是候选，不会直接写入 canonical state。</p></div>
  <span id='session-state' class='pill'>Local session</span>
</header>
<section id='security' class='guard'></section>
<section class='toolbar'>
  <input id='search' placeholder='搜索供应商、协议或用途'>
  <button id='reload' type='button'>刷新</button>
  <span id='count' class='pill'>0 个供应商</span>
</section>
<main id='provider-grid' class='grid'><div class='empty'>正在读取本地供应商注册表…</div></main>
<section class='help'>
<h2>你需要做的只有两件事</h2>
<ol>
<li>点击供应商卡片中的“注册”，在官方页面创建自己的访问凭据。</li>
<li>在终端运行卡片给出的安全录入命令，粘贴一次。之后本页负责检测、模型发现和候选切换。</li>
</ol>
<p>凭据不进入 Git、Notion、SQLite、配置文件或浏览器存储。Windows 推荐使用系统凭据库；未安装安全后端时，WLS 会拒绝持久保存。</p>
</section>
</div>
<div id='toast' class='toast'></div>
<script src='/assets/provider-hub.js' defer></script>
</body>
</html>"""


PROVIDER_HUB_CSS = """
:root{color-scheme:dark;--bg:#090d14;--panel:#121925;--line:#27344a;--text:#eff5ff;--muted:#91a0b8;--accent:#77aaff;--good:#55d69c;--warn:#f0c768;--bad:#ff7d84;--shadow:0 18px 48px rgba(0,0,0,.3)}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 15% 0,#172641 0,transparent 35%),var(--bg);font:14px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;color:var(--text)}a{color:var(--accent);text-decoration:none}.shell{max-width:1500px;margin:auto;padding:28px}.top{display:flex;justify-content:space-between;gap:24px;align-items:flex-start;margin-bottom:20px}.top h1{margin:0;font-size:30px;letter-spacing:-.03em}.top p{max-width:820px;margin:8px 0 0;color:var(--muted)}.pill{display:inline-flex;border:1px solid var(--line);background:#111827;border-radius:999px;padding:7px 11px;color:var(--muted);white-space:nowrap}.guard{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-bottom:18px}.guard article,.toolbar,.card,.help{border:1px solid var(--line);background:linear-gradient(180deg,rgba(22,30,44,.97),rgba(14,20,31,.97));border-radius:16px;box-shadow:var(--shadow)}.guard article{padding:14px}.guard strong{display:block;color:var(--muted);font-size:12px}.guard span{display:block;margin-top:5px}.ok{color:var(--good)}.warn{color:var(--warn)}.bad{color:var(--bad)}.toolbar{display:flex;gap:10px;align-items:center;padding:12px;margin-bottom:16px}.toolbar input{flex:1}.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}.card{padding:17px;display:flex;flex-direction:column;gap:13px;min-height:410px}.card.selected{border-color:#6696db;box-shadow:0 0 0 1px #6696db,var(--shadow)}.head{display:flex;justify-content:space-between;gap:12px}.provider{display:flex;gap:11px}.logo{width:43px;height:43px;display:grid;place-items:center;border-radius:12px;background:linear-gradient(135deg,#315e9f,#76549d);font-weight:800}.provider h2{font-size:17px;margin:0}.provider small,.note,.status,.label{color:var(--muted)}.badges{display:flex;gap:6px;flex-wrap:wrap;margin-top:6px}.badge{font-size:11px;border:1px solid var(--line);border-radius:999px;padding:3px 7px;color:var(--muted)}.badge.pass{color:var(--good);border-color:#2b674e}.badge.fail{color:var(--bad);border-color:#713841}.badge.selected{color:#9fc3ff;border-color:#446da6}.switch{display:flex;gap:7px;align-items:center;color:var(--muted)}.switch input{width:auto}.form{display:grid;gap:9px}.label{display:grid;gap:5px;font-size:12px}input,select,button{font:inherit}input,select{width:100%;background:#0b111b;border:1px solid var(--line);border-radius:10px;padding:10px;color:var(--text);outline:none}input:focus,select:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(119,170,255,.12)}button{border:1px solid var(--line);background:#1a2638;color:var(--text);padding:9px 11px;border-radius:10px;cursor:pointer}button:hover{border-color:#58708f}button.primary{background:#285fa9;border-color:#3b7bd2}button.good{background:#176947;border-color:#2b986a}button.danger{background:#54272e;border-color:#83404b}button:disabled{opacity:.45;cursor:not-allowed}.actions{display:flex;gap:7px;flex-wrap:wrap;margin-top:auto}.status{border-top:1px solid var(--line);padding-top:10px;font-size:12px}.status strong{color:var(--text)}.command{padding:9px;border:1px dashed #3b4d68;border-radius:9px;background:#0b111b;font:12px/1.4 ui-monospace,SFMono-Regular,Consolas,monospace;color:#b8cff7;word-break:break-all}.empty{grid-column:1/-1;padding:42px;text-align:center;color:var(--muted)}.help{margin-top:22px;padding:17px}.help h2{font-size:16px;margin:0 0 7px}.help ol{color:var(--muted);padding-left:20px}.toast{display:none;position:fixed;right:22px;bottom:22px;max-width:430px;padding:12px 15px;border:1px solid var(--line);background:#111a27;border-radius:12px;box-shadow:var(--shadow)}.toast.show{display:block}
@media(max-width:1100px){.grid{grid-template-columns:repeat(2,minmax(0,1fr))}.guard{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:700px){.shell{padding:17px}.top{display:block}.top .pill{margin-top:12px}.grid,.guard{grid-template-columns:1fr}.toolbar{align-items:stretch;flex-direction:column}}
"""


PROVIDER_HUB_JS = r"""
(() => {
  const session = document.querySelector("meta[name=wls-session]").content;
  const grid = document.querySelector("#provider-grid");
  const toast = document.querySelector("#toast");
  let payload = null;
  const esc = value => String(value ?? "").replace(/[&<>'\"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'\"':"&quot;"}[c]));
  const initials = name => name.split(/\s+/).map(part => part[0]).join("").slice(0,2).toUpperCase();
  function say(message, isBad=false){toast.textContent=message;toast.className="toast show "+(isBad?"bad":"");setTimeout(()=>toast.className="toast",3600)}
  async function api(path, options={}){
    const response = await fetch(path,{...options,headers:{"X-WLS-UI-Session":session,"Content-Type":"application/json",...(options.headers||{})}});
    const body = await response.json().catch(()=>({error:"Invalid JSON response"}));
    if(!response.ok) throw new Error(body.error||`HTTP ${response.status}`);
    return body;
  }
  function card(p){
    const models=(p.available_models||[]).map(model=>`<option value="${esc(model)}"></option>`).join("");
    const probeClass=p.last_probe_status==="PASS"?"pass":(["FAIL","ERROR"].includes(p.last_probe_status)?"fail":"");
    const baseField=p.custom_base_url?`<label class="label">Base URL<input data-field="base_url" value="${esc(p.configured_base_url)}"></label>`:"";
    const command=`wls provider-key set ${p.provider_id}`;
    return `<article class="card ${p.selected?"selected":""}" data-id="${esc(p.provider_id)}">
      <div class="head"><div class="provider"><div class="logo">${initials(p.name)}</div><div><h2>${esc(p.name)}</h2><small>${esc(p.family)} · ${esc(p.protocol)}</small><div class="badges"><span class="badge ${probeClass}">${esc(p.last_probe_status)}</span>${p.credential_configured?'<span class="badge pass">凭据已配置</span>':'<span class="badge">待配置</span>'}${p.selected?'<span class="badge selected">首选候选</span>':''}</div></div></div><label class="switch"><input data-field="enabled" type="checkbox" ${p.enabled?"checked":""}>启用</label></div>
      <div class="note">${esc(p.access_note)}<br>隐私上限：<strong>${esc(p.privacy_ceiling)}</strong></div>
      <div class="form"><label class="label">模型<input data-field="model" list="models-${esc(p.provider_id)}" value="${esc(p.model)}"><datalist id="models-${esc(p.provider_id)}">${models}</datalist></label>${baseField}<label class="label">安全录入命令<div class="command">${esc(command)}</div></label></div>
      <div class="actions"><button class="primary" data-action="save">保存配置</button><button data-action="probe" ${p.credential_configured?"":"disabled"}>检测与发现模型</button><button class="good" data-action="select" ${p.enabled&&p.credential_configured?"":"disabled"}>设为首选</button><button class="danger" data-action="remove" ${p.credential_configured?"":"disabled"}>删除凭据</button></div>
      <div class="status"><strong>${p.last_latency_ms==null?"尚未检测":esc(p.last_latency_ms)+" ms"}</strong> · 可见模型 ${esc((p.available_models||[]).length)} 个${p.last_error?" · "+esc(p.last_error):""}<br><a href="${esc(p.registration_url||"#")}" target="_blank" rel="noreferrer">注册</a> · <a href="${esc(p.docs_url||"#")}" target="_blank" rel="noreferrer">官方文档</a></div>
    </article>`;
  }
  function render(){
    const query=document.querySelector("#search").value.trim().toLowerCase();
    const rows=(payload?.providers||[]).filter(p=>!query||(p.name+" "+p.family+" "+p.protocol+" "+p.access_note).toLowerCase().includes(query));
    document.querySelector("#count").textContent=`${rows.length} 个供应商`;
    grid.innerHTML=rows.length?rows.map(card).join(""):'<div class="empty">没有匹配的供应商</div>';
  }
  async function load(){
    try{
      payload=await api("/api/providers");
      document.querySelector("#security").innerHTML=`<article><strong>凭据后端</strong><span class="${payload.secret_store.available?"ok":"warn"}">${esc(payload.secret_store.backend)}</span></article><article><strong>明文进入 WLS 文件</strong><span class="ok">禁止</span></article><article><strong>自动付费回退</strong><span class="ok">关闭</span></article><article><strong>Canonical Runtime</strong><span class="warn">未接入 · 候选层</span></article>`;
      document.querySelector("#session-state").textContent="Local session active";render();
    }catch(error){grid.innerHTML=`<div class="empty">${esc(error.message)}</div>`;document.querySelector("#session-state").textContent="Session failed"}
  }
  grid.addEventListener("click",async event=>{
    const button=event.target.closest("button[data-action]");if(!button)return;
    const cardElement=button.closest(".card");const providerId=cardElement.dataset.id;const action=button.dataset.action;button.disabled=true;
    try{
      if(action==="save"){
        const body={provider_id:providerId,enabled:cardElement.querySelector("[data-field=enabled]").checked,model:cardElement.querySelector("[data-field=model]").value};
        const base=cardElement.querySelector("[data-field=base_url]");if(base)body.base_url=base.value;
        await api("/api/providers/configure",{method:"POST",body:JSON.stringify(body)});say("供应商配置已保存");
      } else if(action==="probe") {await api("/api/providers/probe",{method:"POST",body:JSON.stringify({provider_id:providerId})});say("检测完成");}
      else if(action==="select") {await api("/api/providers/select",{method:"POST",body:JSON.stringify({provider_id:providerId})});say("已设为首选候选；尚未接入 canonical runtime");}
      else if(action==="remove") {if(confirm("从操作系统凭据库删除该供应商凭据？")){await api("/api/providers/remove-credential",{method:"POST",body:JSON.stringify({provider_id:providerId})});say("凭据已删除");}}
      await load();
    }catch(error){say(error.message,true);button.disabled=false;}
  });
  document.querySelector("#search").addEventListener("input",render);
  document.querySelector("#reload").addEventListener("click",load);
  load();
})();
"""
