// submit.js
function updateRange(){
  const start=numeric("indexStart"),cells=numeric("cells"),end=Math.min(ATLAS_FIELD,start+cells);
  $("indexStartOut").value=start.toLocaleString();$("cellsOut").value=`${cells.toLocaleString()} cells`;
  $("rangeLabel").value=`${start.toLocaleString()}—${end.toLocaleString()}`;if(!state.progress)$("progressText").textContent="— / —";
  document.querySelector(".mapCursor").style.top=`${Math.min(94,start/ATLAS_FIELD*100)}%`;
  updateContinue();
}
function payload(dryRun=false){const start=numeric("indexStart"),cells=numeric("cells");return{
  prompt:$("prompt").value.trim(),id:$("id").value.trim(),backend:$("backend").value,model:$("model").value,precision:$("precision").value,batch_size:numeric("batchSize"),study_type:state.studyType,run_type:state.runType,
  index_start:start,index_end:Math.min(65536,start+cells),sample_mode:state.studyType==="loop"?"loop":state.runType==="path"?"contiguous":"nested_sparse",cells,
  size:numeric("size"),steps:numeric("steps"),guidance:numeric("guidance"),seed:$("seed").value,
  shell_scale:numeric("latentDistance"),seed_lock:numeric("seedLock"),shell_coupling:numeric("shellCoupling"),
  mode:$("mode").value,traversal_order:$("order").value,dimension_rates:["dimXY","dimXZ","dimXW","dimYZ","dimYW","dimZW"].map(numeric),adapter:$("adapter").value,
  cache_threshold:numeric("cacheThreshold"),cache_downsample:1,cache_warmup:numeric("cacheWarmup"),dry_run:dryRun}}
async function submit(dryRun){
  if(!state.model.loaded){toast("Load the FLUX worker before starting");return}
  const body=payload(dryRun);if(!body.prompt){toast("A motion prompt is required");return}
  if(!dryRun)state.discovery.stopped=true;
  const button=dryRun?$("planButton"):$("launchButton");button.disabled=true;
  if(!dryRun){state.submitting=true;updateContinue()}
  try{const r=await fetch("/api/atlas/submit",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});const j=await r.json();if(!r.ok||!j.ok)throw Error(j.error||"Atlas request failed");
    if(dryRun)showManifest(j.plan);else{state.activeJob=j.job?.id||j.plan?.id;acceptAssetJob(state.activeJob);state.started=Date.now();rememberRange(body);checkpoint("planned");checkpoint("dispatched");if(j.nexus?.ok)checkpoint("nexus");else checkpoint("nexus","active");checkpoint("producing","active");$("productionSummary").textContent=`0 / ${Number(j.plan?.cells||0).toLocaleString()} cells · starting now`;$("progressText").textContent=`0 / ${Number(j.plan?.cells||0).toLocaleString()}`;$("progressBar").style.width="0%";$("stageMessage").innerHTML="<strong>ATLAS IN MOTION</strong><span>The resident worker is traversing the planned sphere.</span>";toast("Atlas queued on "+String(j.plan?.backend||"worker").toUpperCase())}
  }catch(e){toast(e.message)}finally{button.disabled=false;state.submitting=false;updateContinue()}
}
function rememberRange(body){
  const start=Number(body.index_start)||0,end=Number(body.index_end)||0;
  if(!(end>start))return;
  state.lastRange={start,end,cells:Number(body.cells)||end-start};
  try{sessionStorage.setItem("motionAtlasRange",JSON.stringify(state.lastRange))}catch{}
  updateContinue();
}
// Next origin is where the last dispatch ended, snapped to the slider's step and
// held inside the field so the tail chunk still fits.
function nextOrigin(){
  const last=state.lastRange;
  if(!last)return null;
  const input=$("indexStart"),step=Number(input.step)||1,max=Number(input.max)||ATLAS_FIELD;
  if(last.end>=ATLAS_FIELD)return null;
  // Range inputs snap the assigned value to their step, so round up: overshooting
  // a few cells beats handing back an origin that never advances.
  const origin=Math.min(Math.ceil(last.end/step)*step,Math.floor(max/step)*step);
  return origin>last.start?origin:null;
}
function updateContinue(){
  const button=$("continueButton");
  if(!button)return;
  const origin=nextOrigin();
  const ready=origin!==null&&state.model.loaded&&!state.submitting;
  button.disabled=!ready;
  button.title=!state.lastRange?"Start an atlas first"
    :origin===null?"The 65,536-cell field is fully traversed"
    :!state.model.loaded?"Load the FLUX worker first"
    :`Next: ${origin.toLocaleString()}—${Math.min(ATLAS_FIELD,origin+(numeric("cells")||state.lastRange.cells)).toLocaleString()}`;
}
function continueAtlas(){
  const origin=nextOrigin();
  if(origin===null){toast(state.lastRange?"The atlas field is fully traversed":"Start an atlas before continuing");return}
  $("indexStart").value=String(origin);
  updateRange();
  submit(false);
}
async function previewFlavors(){const prompt=$("prompt").value.trim();if(!prompt){toast("A motion prompt is required");return}state.preview.clear();state.selectedFlavor=null;$("previewGrid").innerHTML=Array.from({length:32},()=>'<div class="flavorPending"></div>').join("");$("previewStatus").textContent="Dispatching 32 unique seeds";$("previewCount").textContent="0 / 32";$("previewProgressBar").style.width="0%";$("launchFlavor").disabled=true;$("previewDialog").showModal();checkpoint("planned");const base=String(Date.now()%800000000+10000000);try{const r=await fetch("/api/render",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({prompt,model:"dev",backend:$("backend").value,width:384,height:384,steps:12,guidance:numeric("guidance"),seed:base,filename:`atlas-flavor-${Date.now()}.png`,iterations:32})}),j=await r.json();if(!r.ok||!j.ok)throw Error(j.error||"Preview batch failed");(j.jobs||[]).forEach((job,i)=>state.preview.set(job.id,{seed:String(j.plans?.[i]?.seed??Number(base)+i),job}));$("previewStatus").textContent="Generating flavor batch on the resident FLUX worker";checkpoint("planned")}catch(e){$("previewStatus").textContent=e.message;toast(e.message)}}
async function renderSeedBatch(){if(!state.model.loaded){toast("Load the FLUX worker before generating");return}const prompt=$("prompt").value.trim();if(!prompt){toast("A motion prompt is required");return}const button=$("planButton"),base=String(Date.now()%800000000+10000000);button.disabled=true;button.textContent="Dispatching 32…";try{const r=await fetch("/api/atlas/preview",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({prompt,model:"dev",backend:$("backend").value,width:numeric("size"),height:numeric("size"),steps:numeric("steps"),guidance:numeric("guidance"),latent_distance:numeric("latentDistance"),seed:base,filename:`seed-ramp-${Date.now()}.png`})}),j=await r.json();if(!r.ok||!j.ok)throw Error(j.error||"Generation failed");if(j.job?.id){state.activeJob=j.job.id;sessionStorage.setItem("motionAtlasJob",j.job.id);acceptAssetJob(j.job.id)}checkpoint("dispatched");checkpoint("nexus",j.nexus?.ok?"done":"active");$("assetSummary").textContent="One coherent 32-image batch is rendering";toast("32-image continuity batch is running")}catch(e){toast(e.message)}finally{button.textContent="Generate 32";updateJobReady()}}
function updatePreview(jobs){if(!state.preview.size)return;for(const job of jobs){const item=state.preview.get(job.id);if(item)item.job=job}const ready=[...state.preview.values()].filter(x=>x.job.output_url&&String(x.job.status).toLowerCase()==="done");$("previewCount").textContent=`${ready.length} / 32`;$("previewProgressBar").style.width=`${ready.length/32*100}%`;$("previewStatus").textContent=ready.length===32?"Select one flavor to anchor the atlas":`Generating flavor ${Math.min(32,ready.length+1)} of 32`;$("previewGrid").innerHTML=[...state.preview.values()].map(x=>x.job.output_url?`<button class="flavor ${state.selectedFlavor?.seed===x.seed?"selected":""}" data-seed="${x.seed}"><img src="${escapeHTML(x.job.output_url)}" alt="Seed ${x.seed}"><span>SEED ${x.seed}</span></button>`:'<div class="flavorPending"></div>').join("");document.querySelectorAll(".flavor").forEach(b=>b.onclick=()=>{state.selectedFlavor={seed:b.dataset.seed,url:b.querySelector("img").src};$("seed").value=b.dataset.seed;document.querySelectorAll(".flavor").forEach(x=>x.classList.remove("selected"));b.classList.add("selected");$("launchFlavor").disabled=false;$("launchButton").disabled=false;$("launchButton").querySelector("span").textContent="Launch selected flavor";$("previewStatus").textContent=`Seed ${b.dataset.seed} selected · ready for full atlas`})}
async function startDiscoveryBatch(){
  const d=state.discovery;if(d.stopped||d.level>=7||d.jobs.size)return;
  const count=2**d.level++,prompt=$("prompt").value.trim();if(!prompt){d.started=false;return}
  $("stageMessage").innerHTML=`<strong>DISCOVERY STREAM · ${count}</strong><span>Generating a ${count}-image seed batch while you shape the atlas.</span>`;
  const base=String(Date.now()%800000000+10000000);
  try{const r=await fetch("/api/render",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({prompt,model:"dev",backend:$("backend").value,width:384,height:384,steps:12,guidance:numeric("guidance"),seed:base,filename:`atlas-discovery-${Date.now()}.png`,iterations:count})}),j=await r.json();if(!r.ok||!j.ok)throw Error(j.error||"Discovery batch failed");(j.jobs||[]).forEach((job,i)=>d.jobs.set(job.id,{seed:String(j.plans?.[i]?.seed??Number(base)+i),job}));$("assetSummary").textContent=`Discovery batch ${count} queued · Piper standing by`}catch(e){d.started=false;$("stageMessage").innerHTML=`<strong>DISCOVERY PAUSED</strong><span>${escapeHTML(e.message)}</span>`}
}
function updateDiscovery(jobs){
  const d=state.discovery;if(!d.started||d.stopped)return;
  for(const job of jobs){const item=d.jobs.get(job.id);if(!item)continue;item.job=job;if(job.output_url&&String(job.status).toLowerCase()==="done"&&!d.ready.has(job.id)){d.ready.add(job.id);ingestFrame({url:job.output_url,seed:item.seed,discovery:true})}}
  if(d.jobs.size&&[...d.jobs.values()].every(x=>["done","error","cancelled"].includes(String(x.job.status).toLowerCase()))){d.jobs.clear();d.ready.clear();startDiscoveryBatch()}
}
