const $=id=>document.getElementById(id);
const esc=s=>{const d=document.createElement("div");d.textContent=s??"";return d.innerHTML};
let assets=[];
const diagnostic=v=>/(\bproof\b|motion-probe)/i.test(String(v||""));
function render(){
 const q=$("registrySearch").value.trim().toLowerCase(),visible=assets.filter(a=>!q||String(a.path||a.access_url).toLowerCase().includes(q));
 $("assetWall").innerHTML=visible.length?visible.map(a=>`<a href="${esc(a.access_url)}" target="_blank"><img src="${esc(a.access_url)}" loading="lazy"><span>${esc((a.path||a.access_url).split("/").pop())}</span><small>${esc(a.path||a.access_url)}</small></a>`).join(""):'<div class="detailEmpty">No generated assets yet.</div>';
 $("assetCount").textContent=assets.length.toLocaleString();$("ledgerStamp").textContent=`LIVE · ${new Date().toLocaleTimeString()}`
}
async function load(){try{const r=await fetch("/api/atlas/catalog"),j=await r.json();if(!r.ok||!j.ok)throw Error(j.error||"Assets unavailable");assets=(j.assets||[]).filter(a=>!diagnostic(a.job_id)&&!diagnostic(a.path));$("jobCount").textContent=(j.jobs||[]).filter(x=>!diagnostic(x.id)&&!diagnostic(x.prompt)).length.toLocaleString();$("seedCount").textContent=(j.seeds||[]).filter(x=>!diagnostic(x.source_job_id)).length.toLocaleString();$("registryState").textContent="LIVE ASSETS";render()}catch{$("registryState").textContent="ASSET FEED ATTENTION"}}
$("registrySearch").oninput=render;$("assetVault").onclick=()=>window.scrollTo({top:document.body.scrollHeight,behavior:"smooth"});
const stream=new EventSource("/api/assets/events");stream.addEventListener("asset",e=>{const event=JSON.parse(e.data),a=event.asset||{};if(diagnostic(event.job_id)||diagnostic(a.path))return;if(!assets.some(x=>x.id===a.id)){assets.unshift({...a,job_id:event.job_id});render()}});
const model=new EventSource("/api/model/events");model.addEventListener("model",e=>{const m=JSON.parse(e.data),top=$("modelTopState");top.className="modelTopState "+(m.loaded?"loaded":m.downloaded?"downloaded":"");top.lastChild.textContent=m.loaded?" MODEL LOADED":m.downloaded?" MODEL READY":" MODEL MISSING"});
load();
