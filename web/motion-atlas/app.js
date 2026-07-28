const $=id=>document.getElementById(id);
const state={runType:"path",activeJob:null,started:0,frames:[]};
const numeric=id=>Number($(id).value);
function toast(message){$("toast").textContent=message;$("toast").classList.add("show");setTimeout(()=>$("toast").classList.remove("show"),2600)}
function drawMap(){
  const c=$("mapCanvas"),d=devicePixelRatio||1,r=c.getBoundingClientRect();c.width=r.width*d;c.height=r.height*d;
  const x=c.getContext("2d");x.scale(d,d);x.strokeStyle="#55e7ee22";x.lineWidth=.7;
  for(let i=0;i<18;i++){x.beginPath();x.ellipse(r.width/2,r.height/2,r.width*(.12+i*.027),r.height*(.42-i*.009),0,0,Math.PI*2);x.stroke()}
  for(let i=0;i<15;i++){x.beginPath();x.moveTo(i*r.width/14,0);x.quadraticCurveTo(r.width/2,r.height/2,(14-i)*r.width/14,r.height);x.stroke()}
}
function updateRange(){
  const start=numeric("indexStart"),cells=numeric("cells"),end=Math.min(65536,start+cells);
  $("indexStartOut").value=start.toLocaleString();$("cellsOut").value=`${cells.toLocaleString()} frames`;
  $("rangeLabel").value=`${start.toLocaleString()}—${end.toLocaleString()}`;$("progressText").textContent=`0 / ${cells.toLocaleString()}`;
  document.querySelector(".mapCursor").style.top=`${Math.min(94,start/65536*100)}%`;
}
function payload(dryRun=false){const start=numeric("indexStart"),cells=numeric("cells");return{
  prompt:$("prompt").value.trim(),id:$("id").value.trim(),backend:$("backend").value,run_type:state.runType,
  index_start:start,index_end:Math.min(65536,start+cells),sample_mode:state.runType==="path"?"contiguous":"nested_sparse",cells,
  size:numeric("size"),steps:numeric("steps"),guidance:numeric("guidance"),seed:$("seed").value,
  shell_scale:numeric("shellScale"),seed_lock:numeric("seedLock"),shell_coupling:numeric("shellCoupling"),
  mode:$("mode").value,traversal_order:$("order").value,adapter:$("adapter").value,
  cache_threshold:numeric("cacheThreshold"),cache_downsample:1,cache_warmup:numeric("cacheWarmup"),dry_run:dryRun}}
async function submit(dryRun){
  const body=payload(dryRun);if(!body.prompt){toast("A motion prompt is required");return}
  const button=dryRun?$("planButton"):$("launchButton");button.disabled=true;
  try{const r=await fetch("/api/atlas/submit",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});const j=await r.json();if(!r.ok||!j.ok)throw Error(j.error||"Atlas request failed");
    if(dryRun)showManifest(j.plan);else{state.activeJob=j.job?.id||j.plan?.id;state.started=Date.now();$("stageMessage").innerHTML="<strong>ATLAS IN MOTION</strong><span>The resident worker is traversing the planned sphere.</span>";toast("Atlas queued on "+String(j.plan?.backend||"worker").toUpperCase());refreshJobs();watchAtlas(j.plan?.id)}
  }catch(e){toast(e.message)}finally{button.disabled=false}
}
function showManifest(p){const entries=[["Backend",p.backend],["Sequence",`${p.cells} cells`],["Frame",`${p.width}² / ${p.steps} steps`],["Orbit",p.mode],["Traversal",p.traversal_order],["Cache",`${p.adapter} / ${p.cache_threshold}`]];
  $("manifest").innerHTML=entries.map(([k,v])=>`<div class="manifestItem"><span>${k}</span><strong>${v}</strong></div>`).join("")+`<div class="manifestItem manifestPrompt"><span>Motion direction</span><strong>${escapeHTML(p.prompt)}</strong></div>`;$("manifestDialog").showModal()}
function escapeHTML(s){const d=document.createElement("div");d.textContent=s??"";return d.innerHTML}
async function health(){try{const r=await fetch("/api/health"),j=await r.json();$("workerState").textContent=j.worker_ok?"WORKER LIVE":"SUITE LIVE";$("device").textContent=String(j.backend||"CUDA · B300").toUpperCase();document.querySelector(".status").classList.toggle("offline",!j.ok)}catch{$("workerState").textContent="OFFLINE";document.querySelector(".status").classList.add("offline")}}
async function refreshJobs(){try{const r=await fetch("/api/jobs"),j=await r.json(),jobs=(j.jobs||[]).filter(x=>String(x.id||"").includes("atlas")||x.viewer_url);$("sessions").innerHTML=jobs.length?jobs.slice(0,6).map(x=>`<button class="session ${x.id===state.activeJob?"active":""}" data-job="${escapeHTML(x.id)}"><span>${escapeHTML(x.id)}</span><small>${escapeHTML(x.status||"queued")} · ${escapeHTML(x.backend||"")}</small></button>`).join(""):'<div class="session empty"><span>NO ACTIVE RUNS</span><small>Launch an atlas to begin</small></div>'}catch{}}
function watchAtlas(id){if(!id)return;const es=new EventSource(`/api/atlas/events/${encodeURIComponent(id)}`);es.onmessage=e=>{try{const d=JSON.parse(e.data);ingestFrame(d)}catch{}}}
function ingestFrame(d){const url=d.url||d.output_url||d.image_url;if(url&&!state.frames.includes(url)){state.frames.push(url);$("liveFrame").src=url;$("liveFrame").style.display="block";$("sphere").style.display="none";$("stageMessage").style.display="none";const img=document.createElement("img");img.src=url;$("filmstrip").querySelector(".filmEmpty")?.remove();$("filmstrip").appendChild(img);$("filmstrip").scrollLeft=999999}
  const done=Number(d.completed??d.index??state.frames.length),total=numeric("cells"),pct=Math.min(100,done/total*100);$("progressBar").style.width=pct+"%";$("progressText").textContent=`${done.toLocaleString()} / ${total.toLocaleString()}`;$("currentCell").textContent=String(d.cell??d.index??done).padStart(5,"0");if(state.started&&done){const fps=done/((Date.now()-state.started)/1000);$("rate").textContent=fps.toFixed(2)+" fps";$("eta").textContent=Math.max(0,(total-done)/fps/60).toFixed(1)+" min"}}
document.querySelectorAll("[data-run]").forEach(b=>b.onclick=()=>{document.querySelectorAll("[data-run]").forEach(x=>x.classList.remove("active"));b.classList.add("active");state.runType=b.dataset.run});
document.querySelectorAll(".groupTitle").forEach(b=>b.onclick=()=>{b.parentElement.classList.toggle("open");b.querySelector("i").textContent=b.parentElement.classList.contains("open")?"−":"+"});
document.querySelectorAll("[data-view]").forEach(b=>b.onclick=()=>{document.querySelectorAll("[data-view]").forEach(x=>x.classList.remove("active"));b.classList.add("active");const frame=b.dataset.view==="frame"&&state.frames.length;$("liveFrame").style.display=frame?"block":"none";$("sphere").style.display=frame?"none":"block"});
$("indexStart").oninput=$("cells").oninput=updateRange;$("planButton").onclick=()=>submit(true);$("launchButton").onclick=()=>submit(false);$("launchFromPlan").onclick=()=>{$("manifestDialog").close();submit(false)};$("refreshJobs").onclick=refreshJobs;
document.querySelectorAll(".dialogClose,.dialogCloseButton").forEach(b=>b.onclick=()=>$("manifestDialog").close());
$("helpButton").onclick=()=>toast("Plan a coherent latent path, then launch it into the resident FLUX worker.");
drawMap();updateRange();health();refreshJobs();setInterval(health,10000);setInterval(refreshJobs,5000);window.addEventListener("resize",drawMap);
