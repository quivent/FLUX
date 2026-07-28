// core.js
const $=id=>document.getElementById(id);
const FUEL_CAPACITY_SEC=6*3600;
const FUEL_LOW_SEC=30*60;
const ATLAS_FIELD=65536;
const resumedJob=sessionStorage.getItem("motionAtlasJob");
// Where the last dispatched atlas stopped, so Continue can pick the path back up
// across a reload. Shape: {start,end,cells}.
const resumedRange=(()=>{try{const r=JSON.parse(sessionStorage.getItem("motionAtlasRange")||"null");return r&&Number.isFinite(r.end)?r:null}catch{return null}})();
const state={studyType:null,runType:"path",activeJob:resumedJob,lastRange:resumedRange,started:0,frames:[],assetSide:"A",acceptedAssetJobs:new Set(),hydratedJobs:new Set(),pendingAssets:new Map(),gpuProcesses:new Map(),preview:new Map(),selectedFlavor:null,model:{known:false,downloaded:false,loaded:false,pendingPreview:!resumedJob},discovery:{started:false,stopped:false,level:0,jobs:new Map(),ready:new Set()}};
const pageStudies=[

// studies.js
  ["Lanterns Across the Salt Observatory","At blue hour, a solitary white stag crosses a flooded marble observatory suspended above the sea; each measured step sends constellations trembling through reflected water while brass instruments, salt-worn columns, and distant storm light recede in exact cinematic perspective. A continuous lateral tracking shot, anatomically precise, luminous but restrained, quiet awe, coherent physical space, no text or ornament on the animal."],
  ["The Orchard Remembers the Wind","An ancient pear orchard bends beneath a lucid summer storm as a red fox runs between silvered trunks, loose petals and rain moving in layered parallax. Low tracking camera, precise animal anatomy, wet earth reflecting intermittent sky fire, restrained painterly realism, one continuous physical world, stable identity and graceful sequential motion."],
  ["Procession Through the Glass Tides","A small procession of pale horses walks across translucent tidal flats at dawn, their reflections descending into a submerged city of arches and gardens. Slow elevated camera drift, measured hoof movement, atmospheric depth, exact perspective, quiet ceremonial scale, consistent anatomy and identity, continuous cinematic motion without cuts."],
  ["Night Train Beneath the Amber Ice","A midnight train glides beneath a vast ceiling of amber ice while distant forests and signal lamps pass through deep lateral parallax. The camera travels beside one illuminated carriage, frost breathing across its windows, physically coherent reflections, controlled lens compression, patient momentum, elegant sequential continuity."],
  ["The Astronomer’s Mechanical Garden","A lone astronomer walks through a moonlit garden of slowly unfolding brass flowers, glass leaves collecting rain as observatory domes rotate beyond dark cypress trees. Deliberate dolly movement, interlocking foreground and background motion, intricate but stable geometry, subdued gold and indigo light, one continuous dreamlike space."],
  ["River of Quiet Machines","Graceful ceramic machines wade through a shallow black river between monumental concrete pylons, sending precise concentric ripples through reflected clouds. Wide cinematic tracking, controlled mechanical articulation, deep environmental parallax, soft overcast luminance, consistent forms, solemn and physically coherent motion."],
  ["The Blue Heron and the Falling City","A blue heron flies steadily above terraced rooftops descending through morning fog toward an immense inland sea, laundry lines, antennas, trees, and stone balconies sliding below in distinct depth layers. Stable bird anatomy, fluid wing cycles, aerial tracking camera, luminous realism and uninterrupted spatial continuity."],
  ["After Rain in the Copper Arcade","A dark mare moves at a measured canter through a long copper arcade after rain, warm shop light folding across the pavement as columns, hanging gardens, and distant pedestrians pass in elegant parallax. Ground-level tracking, precise anatomy, restrained cinematic color, consistent identity, no rider or tack, continuous motion."]
];
function seedPage(){const saved=[sessionStorage.getItem("motionAtlasTitle"),sessionStorage.getItem("motionAtlasPrompt"),sessionStorage.getItem("motionAtlasSeed")];if(saved.every(Boolean)){[$("id").value,$("prompt").value,$("seed").value]=saved;return}const bytes=new Uint32Array(2);crypto.getRandomValues(bytes);const index=bytes[0]%pageStudies.length,study=pageStudies[index],seed=String(10000000+bytes[1]%890000000);$("id").value=study[0];$("prompt").value=study[1];$("seed").value=seed;sessionStorage.setItem("motionAtlasTitle",study[0]);sessionStorage.setItem("motionAtlasPrompt",study[1]);sessionStorage.setItem("motionAtlasSeed",seed)}
const numeric=id=>Number($(id).value);

// assets.js
function toast(message){$("toast").textContent=message;$("toast").classList.add("show");setTimeout(()=>$("toast").classList.remove("show"),2600)}
function acceptAssetJob(id){if(!id)return;state.acceptedAssetJobs.add(id);for(const event of state.pendingAssets.get(id)||[])ingestAssetEvent(event);state.pendingAssets.delete(id);hydrateJobAssets(id)}
async function hydrateJobAssets(id){if(state.hydratedJobs.has(id))return;state.hydratedJobs.add(id);try{const r=await fetch("/api/atlas/catalog"),j=await r.json(),all=j.assets||[];let assets=all.filter(a=>a.job_id===id),source=id;if(!assets.length){const prompt=(j.jobs||[]).find(x=>x.id===id)?.prompt,prior=(j.jobs||[]).filter(x=>x.id!==id&&x.prompt===prompt&&all.some(a=>a.job_id===x.id)).sort((a,b)=>Number(b.updated_at)-Number(a.updated_at))[0];if(prior){source=prior.id;assets=all.filter(a=>a.job_id===source);$("assetSummary").textContent="Previous horse collection visible · live batch rendering"}}assets.sort((a,b)=>Number(a.cell_index)-Number(b.cell_index)||Number(a.created_at)-Number(b.created_at)).slice(-64).forEach(a=>ingestAssetEvent({job_id:source,asset:a}))}catch{state.hydratedJobs.delete(id)}}
function ingestAssetEvent(event){
  const shown=state.pinnedJob||state.shownJob||state.activeJob;
  if(shown&&event.job_id!==shown)return;
  clearPrefilled();
  const a=event.asset||{};
  ingestFrame({url:a.access_url,index:a.index,seed:a.seed,total:Number(a.total)||0,live:true,updateProgress:!!state.activeJob&&event.job_id===state.activeJob});
}
function renderSpineState(){const producing=document.querySelector('[data-step="producing"]');$("spineState").textContent=producing?.classList.contains("active")?"PRODUCING":document.querySelector('[data-step="assets"]')?.classList.contains("done")?"LIVE":"READY"}

// map.js
function checkpoint(name,status="done"){const row=document.querySelector(`[data-step="${name}"]`);if(!row)return;const current=row.classList.contains("active")?"active":row.classList.contains("done")?"done":"";if(current!==status){row.classList.remove("active","done");if(status)row.classList.add(status);renderSpineState()}}
function updateJobReady(){const configured=state.studyType&&$("prompt").value.trim()&&$("id").value.trim()&&numeric("cells")>0&&numeric("batchSize")>0&&numeric("size")>0&&numeric("steps")>0&&numeric("guidance")>0,loaded=state.model.loaded;checkpoint("planned",configured?"done":"");$("planButton").disabled=!loaded||!configured;$("launchButton").disabled=state.model.known&&loaded&&!configured;$("launchButton").querySelector("span").textContent=!state.model.known?"Checking worker":!loaded?(state.model.downloaded?"Load worker":"Download model"):configured?`Start ${state.studyType}`:"Choose loop or atlas";updateContinue();return !!configured&&loaded}
function drawMap(){
  const c=$("mapCanvas"),d=devicePixelRatio||1,r=c.getBoundingClientRect();c.width=r.width*d;c.height=r.height*d;
  const x=c.getContext("2d");x.scale(d,d);x.strokeStyle="#55e7ee22";x.lineWidth=.7;
  for(let i=0;i<18;i++){x.beginPath();x.ellipse(r.width/2,r.height/2,r.width*(.12+i*.027),r.height*(.42-i*.009),0,0,Math.PI*2);x.stroke()}

// submit.js
  for(let i=0;i<15;i++){x.beginPath();x.moveTo(i*r.width/14,0);x.quadraticCurveTo(r.width/2,r.height/2,(14-i)*r.width/14,r.height);x.stroke()}
}
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

// gpu.js
function renderGPU(g){if(!g)return;const card=document.querySelector(`.gpuCard[data-gpu="${g.index}"]`);if(!card)return;const memPct=g.memory_total?g.memory_used/g.memory_total*100:0;card.querySelector(".gpuCardName").textContent=g.name.replace(/^NVIDIA /,"");card.querySelector(".gpuCardPhase").textContent=g.utilization>10?"RENDERING":"IDLE";card.querySelector(".gpuCardVram").textContent=`${(g.memory_used/1024).toFixed(1)}/${(g.memory_total/1024).toFixed(0)} GB`;card.querySelector(".gpuCardVramBar").style.width=Math.min(100,memPct)+"%";card.querySelector(".gpuCardCompute").textContent=Math.round(g.utilization)+"%";card.querySelector(".gpuCardComputeBar").style.width=Math.min(100,g.utilization)+"%";card.querySelector(".gpuCardThermal").textContent=Math.round(g.temperature)+"°";card.querySelector(".gpuCardPower").textContent=Math.round(g.power_draw)+"W";card.classList.toggle("active",g.utilization>10)}
function renderGPUProcess(p){state.gpuProcesses.set(p.pid,p)}
function renderModel(m){const pendingPreview=state.model.pendingPreview,justLoaded=!state.model.loaded&&!!m.loaded;state.model={known:true,downloaded:!!m.downloaded,loaded:!!m.loaded,pendingPreview};$("downloadState").textContent=m.downloaded?"READY":m.downloading?"DOWNLOADING":"NOT FOUND";$("loadState").textContent=m.loaded?"RESIDENT":"NOT LOADED";$("downloadDot").classList.toggle("on",!!m.downloaded);$("loadDot").classList.toggle("on",!!m.loaded);$("downloadModel").disabled=!!m.downloaded||!!m.downloading;$("loadModel").disabled=!m.downloaded||!!m.loaded;$("modelProgressBar").classList.toggle("busy",!!m.downloading);$("modelProgressBar").style.width=m.loaded?"100%":m.downloaded&&!m.downloading?"62%":"";$("modelMessage").textContent=m.message||m.device||(m.loaded?"Model is resident on the GPU.":m.downloaded?"BF16 weights are ready. Load the worker to continue.":"FLUX.1 Dev weights are not on disk.");const top=$("modelTopState");top.className="modelTopState "+(m.loaded?"loaded":m.downloaded?"downloaded":"");top.lastChild.textContent=m.loaded?" MODEL LOADED":m.downloaded?" LOAD WORKER":" MODEL MISSING";checkpoint("model",m.loaded?"done":"");checkpoint("worker",m.loaded?"done":"");if(!m.loaded&&state.frames.length===0){$("stageMessage").style.display="grid";$("stageMessage").innerHTML="<strong>FLUX WORKER NOT LOADED</strong><span>Load the resident worker before generating previews or starting the atlas.</span>"}updateJobReady();if(justLoaded&&pendingPreview){state.model.pendingPreview=false}}

// jobs.js
function showJobProgress(job){
  if(!job)return;
  state.activeJob=job.id;
  const preview=String(job.kind||"")==="seed_preview",atlas=String(job.kind||"")==="atlas_sphere";
  const delivered=Number(preview?job.images_done:(job.atlas_done??job.step??0));
  const total=Number(preview?job.images_total:(job.atlas_total??job.total_steps??0));
  state.batchSize=Number(job.batch_size||job.batch_plan?.[0])||state.batchSize||64;
  const batchRendering=atlas&&String(job.phase||"").startsWith("batch ");
  const batchSize=Number(job.batch_size||1),batchStep=Number(job.cell_step||0),batchSteps=Number(job.cell_total_steps||job.steps||0);
  const effectiveDone=delivered+(batchRendering&&batchSteps?batchSize*batchStep/batchSteps:0);
  const pct=total?Math.min(100,effectiveDone/total*100):0;
  const batchLabel=batchRendering?` · ${String(job.phase).toUpperCase()} · STEP ${batchStep}/${batchSteps}`:"";
  if($("experimentPhase"))$("experimentPhase").textContent=`${String(job.status||"queued").toUpperCase()}${batchLabel||` · ${String(job.phase||"WAITING").toUpperCase()}`}`;
  if($("stageTitle")){
    const label=String(job.id||"").replace(/[-_]+/g," ").replace(/\b\w/g,c=>c.toUpperCase());
    $("stageTitle").textContent=label||"Motion continuity, mapped.";
    $("stageTitle").title=job.prompt||"";
  }
  if($("stageJobMeta")){
    const bits=[String(job.kind||"").replace("_"," "),job.width?`${job.width}px`:"",job.steps?`${job.steps} steps`:"",job.seed?`seed ${job.seed}`:"",job.mode||""].filter(Boolean);
    $("stageJobMeta").textContent=bits.join(" · ");
  }
  if($("stagePrompt"))$("stagePrompt").textContent=job.prompt||"";
  $("progressText").textContent=total?`${Math.floor(effectiveDone).toLocaleString()} / ${total.toLocaleString()}`:"— / —";
  $("progressBar").style.width=pct+"%";
  $("productionSummary").textContent=total?`${delivered.toLocaleString()} / ${total.toLocaleString()} ${preview?"images":"cells"}${batchLabel}`:String(job.phase||job.status);
  if(["running"].includes(String(job.status).toLowerCase()))checkpoint("producing","active");
  if(delivered>=total&&total>0)checkpoint("producing");
  const elapsed=Number(job.started)?Date.now()/1000-Number(job.started):0,rate=effectiveDone>0&&elapsed>0?effectiveDone/elapsed:0,eta=rate>0?(total-effectiveDone)/rate:0;
  if(rate>0)$("rate").textContent=rate.toFixed(2)+" fps";
  if(eta>0)$("eta").textContent=eta<90?Math.ceil(eta)+" sec":(eta/60).toFixed(1)+" min";
  state.progress=String(job.status).toLowerCase()==="running"&&total>0
    ?{done:effectiveDone,total:total,rate:rate,at:Date.now(),preview:preview}
    :null;
  if(job.cache_hit_rate!=null)$("cacheState").textContent=`XFRAME / ${Math.round(Number(job.cache_hit_rate)*100)}% HIT`;
}
function renderJobFeed(j){
  const allJobs=j.jobs||[];
  try{updatePreview(allJobs)}catch{}
  try{updateDiscovery(allJobs)}catch{}
  try{renderFuel(allJobs)}catch{}
  const atlasJobs=allJobs.filter(x=>String(x.kind||"")==="atlas_sphere"||x.viewer_url);
  const previewJobs=allJobs.filter(x=>String(x.kind||"")==="seed_preview");
  const running=x=>["running","queued","cancelling"].includes(String(x.status).toLowerCase());
  const active=x=>String(x.status).toLowerCase()==="running";
  const newest=rows=>[...rows].sort((a,b)=>Number(b.created||0)-Number(a.created||0))[0];
  const resumed=allJobs.find(x=>x.id===state.activeJob);
  const pinned=state.pinnedJob?allJobs.find(x=>x.id===state.pinnedJob):null;
  const sticky=state.shownJob?allJobs.find(x=>x.id===state.shownJob&&active(x)):null;
  const chosen=pinned||sticky||newest(atlasJobs.filter(active))||newest(previewJobs.filter(active))||(resumed&&running(resumed)?resumed:null)||newest(atlasJobs.filter(running))||newest(previewJobs.filter(running))||resumed||newest(previewJobs)||newest(atlasJobs);
  const switched=state.shownJob!==(chosen&&chosen.id);
  $("workerState").textContent=j.worker_running?"WORKER LIVE":"SUITE LIVE";
  if(chosen){
    state.activeJob=chosen.id;
    if(switched&&state.shownJob){
      state.frames=[];state.playIndex=null;
      state.playQueue=[];
      if(state.playPump){clearTimeout(state.playPump);state.playPump=null}
      const strip=$("filmstrip");
      if(strip)strip.querySelectorAll("img").forEach(x=>x.remove());
      state.hydratedJobs.delete(chosen.id);
    }
    state.shownJob=chosen.id;
    sessionStorage.setItem("motionAtlasJob",chosen.id);
    if(switched&&chosen.prompt){
      $("prompt").value=chosen.prompt;
      sessionStorage.setItem("motionAtlasPrompt",chosen.prompt);
    }
    const setValue=(id,value)=>{if(switched&&value!==undefined&&value!==null&&value!==""&&$(id))$(id).value=String(value)};
    setValue("backend",chosen.requested_backend||chosen.backend);
    setValue("size",chosen.width);
    setValue("steps",chosen.steps||chosen.total_steps);
    setValue("guidance",chosen.guidance);
    setValue("latentDistance",chosen.latent_distance||chosen.shell_scale);
    setValue("seed",chosen.seed);
    setValue("batchSize",chosen.batch_size||chosen.batch_plan?.[0]);
    const count=String(chosen.kind||"")==="seed_preview"?chosen.images_total:(chosen.atlas_total??chosen.cells);
    if(count){setValue("cells",count);setValue("cellsNumber",count)}
    acceptAssetJob(chosen.id);
    allJobs.filter(x=>["running","queued"].includes(String(x.status).toLowerCase())).forEach(x=>acceptAssetJob(x.id));
    // A job that exists was planned by definition. Without this, "Job ready"
    // stays grey for anything the composer form did not submit (API, CLI,
    // socket) even while the job is visibly running.
    checkpoint("planned");
    checkpoint("dispatched");
    if(chosen.nexus_accepted)checkpoint("nexus");
    checkpoint("worker");
    showJobProgress(chosen);
  }
  $("sessions").innerHTML=atlasJobs.length?atlasJobs.map(x=>`<button class="session ${x.id===state.activeJob?"active":""}" data-job="${escapeHTML(x.id)}"><span>${escapeHTML(x.prompt||"Motion atlas")}</span><small>${escapeHTML(x.status||"queued")} · ${Number(x.atlas_done??x.step??0).toLocaleString()} / ${Number(x.atlas_total??x.total_steps??0).toLocaleString()}</small></button>`).join(""):'<div class="session empty"><span>CONTINUITY PREVIEW</span><small>Its assets remain preserved in Gallery</small></div>';
  document.querySelectorAll("[data-job]").forEach(b=>b.onclick=()=>{const job=allJobs.find(x=>x.id===b.dataset.job);if(!job)return;state.pinnedJob=job.id;state.activeJob=job.id;state.shownJob=null;sessionStorage.setItem("motionAtlasJob",job.id);acceptAssetJob(job.id);clearPrefilled();state.frames=[];const strip=$("filmstrip");if(strip)strip.querySelectorAll("img").forEach(x=>x.remove());state.hydratedJobs.delete(job.id);hydrateJobAssets(job.id);showJobProgress(job)});
}
async function refreshJobs(){try{const r=await fetch("/api/jobs"),j=await r.json();renderJobFeed(j)}catch{}}
function connectStreams(){const jobs=new EventSource("/api/jobs/events");jobs.addEventListener("jobs",e=>{let data=null;try{data=JSON.parse(e.data)}catch{}if(!data)return;try{const shown=state.pinnedJob||state.shownJob;if(shown)acceptAssetJob(shown);else (data.jobs||[]).filter(x=>String(x.status).toLowerCase()==="running").slice(0,1).forEach(x=>acceptAssetJob(x.id))}catch{}try{const hasRunning=(data.jobs||[]).some(j=>j.status==="running"||j.status==="queued");if(hasRunning&&!state.slideshowTimer){state.slideshowTimer=setInterval(playFrame,340)}else if(!hasRunning&&state.slideshowTimer){clearInterval(state.slideshowTimer);state.slideshowTimer=null}}catch{}try{renderJobFeed(data)}catch{}});jobs.onerror=()=>{$("workerState").textContent="RECONNECTING"};const gpu=new EventSource("/api/telemetry/events");gpu.addEventListener("gpu",e=>{try{renderGPU(JSON.parse(e.data).gpu)}catch{}});gpu.onerror=()=>{};const processes=new EventSource("/api/telemetry/processes/events");processes.addEventListener("process",e=>{try{renderGPUProcess(JSON.parse(e.data))}catch{}});const assets=new EventSource("/api/assets/events");assets.addEventListener("asset",e=>{try{const event=JSON.parse(e.data);if(state.acceptedAssetJobs.has(event.job_id))ingestAssetEvent(event);else{const pending=state.pendingAssets.get(event.job_id)||[];pending.push(event);state.pendingAssets.set(event.job_id,pending)}}catch{}});assets.onerror=()=>{const el=$("stageMessage");if(el&&el.querySelector("span"))el.querySelector("span").textContent="Piper reconnecting to the asset socket."};const model=new EventSource("/api/model/events");model.addEventListener("model",e=>{try{renderModel(JSON.parse(e.data))}catch{}})}
async function modelAction(path){try{const r=await fetch(path,{method:"POST"}),j=await r.json();if(!r.ok||!j.ok)throw Error(j.error||"Model action failed");$("modelMessage").textContent=String(j.status||"Working").toUpperCase();$("modelProgressBar").classList.add("busy")}catch(e){toast(e.message)}}

// stage.js
function loadThumbnail(img,url,seed){document.querySelectorAll("#filmstrip img").forEach(x=>x.classList.remove("selected"));img.classList.add("selected");$("assetSummary").textContent="Loading selected frame…";const shown=$("assetFrameA");shown.classList.remove("active");shown.onload=()=>{shown.classList.add("active");$("assetSummary").textContent=seed?`Seed ${seed} selected · ready to start`:"Selected frame loaded"};shown.onerror=()=>{$("assetSummary").textContent="Frame failed to load"};shown.src=url;$("assetFrameB").classList.remove("active");if(seed){$("seed").value=seed;state.selectedFlavor={seed:String(seed),url};updateJobReady()}}
function cycleFrame(delta){const frames=[...document.querySelectorAll("#filmstrip img")];if(!frames.length)return;let i=frames.findIndex(x=>x.classList.contains("selected"));i=(i<0?(delta>0?-1:0):i)+delta;i=(i+frames.length)%frames.length;frames[i].click();frames[i].scrollIntoView({behavior:"smooth",inline:"center",block:"nearest"})}
function sweepFilmstrip(){const strip=$("filmstrip"),frames=[...strip.querySelectorAll("img")];if(state.filmstripSweeping||frames.length<64)return;state.filmstripSweeping=true;frames.slice(0,32).forEach((img,i)=>setTimeout(()=>{img.classList.add("departing");setTimeout(()=>img.remove(),180)},i*24));setTimeout(()=>{state.filmstripSweeping=false},32*24+220)}
function ingestFrame(d){const url=d.url||d.output_url||d.image_url;if(url&&!state.frames.includes(url)){state.frames.push(url);if(d.live){state.lastFrameAt=Date.now();queueFrames([url])}checkpoint("assets");$("assetSummary").textContent=`${state.frames.length.toLocaleString()} assets received from Piper`;const next=state.assetSide==="A"?$("assetFrameA"):$("assetFrameB"),prior=state.assetSide==="A"?$("assetFrameB"):$("assetFrameA");if(!d.live){next.onload=()=>{prior.classList.remove("active");next.classList.add("active")};next.onerror=()=>{$("assetSummary").textContent="Incoming frame failed to load"};next.src=url;state.assetSide=state.assetSide==="A"?"B":"A";$("assetStage").style.display="block";$("sphere").style.display="none";$("stageMessage").style.display="none";}const img=document.createElement("img");img.onload=()=>img.classList.remove("loading");img.onerror=()=>img.classList.add("failed");img.classList.add("loading");img.src=url;img.title=d.seed?`Load frame and select seed ${d.seed}`:"Load this frame";img.onclick=()=>loadThumbnail(img,url,d.seed);$("filmstrip").querySelector(".filmEmpty")?.remove();$("filmstrip").appendChild(img);$("filmstrip").scrollLeft=999999;sweepFilmstrip()}
  if(d.updateProgress){const done=Number(d.completed||state.frames.length),total=Number(d.total||0),pct=total?Math.min(100,done/total*100):0;$("progressBar").style.width=pct+"%";$("progressText").textContent=total?`${done.toLocaleString()} / ${total.toLocaleString()}`:"— / —";if(state.started&&done&&total){const fps=done/((Date.now()-state.started)/1000);$("rate").textContent=fps.toFixed(2)+" fps";$("eta").textContent=Math.max(0,(total-done)/fps/60).toFixed(1)+" min"}}$("currentCell").textContent=d.index==null?"—":String(d.index).padStart(5,"0")}
// The worker delivers a whole batch at once (batch_size frames in one burst),
// then renders silently for roughly batch_size/rate seconds. Showing the burst
// immediately would flash 64 frames and then freeze, so arriving frames are
// queued and released at the pace they were actually rendered. The interval is
// derived from the live queue depth, so if the queue falls behind it drains
// faster and catches up by the time the next batch lands.
function queueFrames(urls){
  if(!urls||!urls.length)return;
  // A gap since the last arrival means a new batch just landed; restart the
  // pacing window from now.
  if(Date.now()-(state.lastQueueAt||0)>2000)state.batchAt=Date.now();
  state.lastQueueAt=Date.now();
  state.playQueue=(state.playQueue||[]).concat(urls);
  if(!state.playPump)pumpFrame();
}
function paceMs(){
  const q=(state.playQueue||[]).length;
  if(!q)return 900;
  const rate=(state.progress&&state.progress.rate>0)?state.progress.rate:(state.lastRate||1);
  const batch=Math.max(1,Number(state.batchSize||64));
  // How long the worker takes to produce one batch, and how much of that
  // window is left. Dividing the remaining window by the remaining queue keeps
  // the interval stable instead of decelerating as the queue drains.
  const periodMs=(batch/Math.max(rate,0.05))*1000;
  const elapsed=Date.now()-(state.batchAt||Date.now());
  const remaining=periodMs-elapsed;
  // Behind schedule, or more queued than one batch: drain at render pace or faster.
  if(remaining<=0||q>batch)return Math.max(80,Math.min(600,periodMs/Math.max(q,1)));
  return Math.max(80,Math.min(2000,remaining/q));
}
function pumpFrame(){
  const q=state.playQueue||[];
  if(!q.length){state.playPump=null;return}
  showStageFrame(q.shift());
  state.playPump=setTimeout(pumpFrame,paceMs());
}
function showStageFrame(url){
  if(!url)return;
  const next=state.playSide==="B"?$("assetFrameB"):$("assetFrameA");
  const prior=state.playSide==="B"?$("assetFrameA"):$("assetFrameB");
  if(!next)return;
  next.src=url;
  next.classList.add("active");
  if(prior)prior.classList.remove("active");
  state.playSide=state.playSide==="B"?"A":"B";
  state.shownFrame=url;
  $("assetStage").style.display="block";
  $("sphere").style.display="none";
  $("stageMessage").style.display="none";
}
function playFrame(){
  // Idle filler: only used when the queue is empty and nothing is arriving,
  // so the stage never sits frozen on a single frame.
  if((state.playQueue||[]).length)return;
  if(state.frames.length<2)return;
  if(Date.now()-(state.lastFrameAt||0)<2500)return;
  const window=Math.min(state.frames.length,24);
  const base=state.frames.length-window;
  state.playIndex=base+(((state.playIndex??base)-base+1)%window);
  showStageFrame(state.frames[state.playIndex]);
}
function humanDuration(sec){
  if(!Number.isFinite(sec)||sec<=0)return "—";
  if(sec<90)return Math.ceil(sec)+"s";
  if(sec<5400)return (sec/60).toFixed(sec<600?1:0)+"m";
  return (sec/3600).toFixed(1)+"h";
}
function renderEtaBar(){
  const p=state.progress;
  if(!p||!(p.total>0)||!(p.rate>0)){
    $("etaBar").style.width="0%";
    $("etaClock").textContent="—";
    return;
  }
  const projected=Math.min(p.total,p.done+p.rate*((Date.now()-p.at)/1000));
  const elapsed=projected/p.rate;
  const totalEst=p.total/p.rate;
  $("etaBar").style.width=Math.min(100,elapsed/totalEst*100).toFixed(2)+"%";
  $("etaClock").textContent=`${humanDuration(elapsed)} / ${humanDuration(totalEst)}`;
}
function renderFuel(jobs){
  const st=x=>String(x.status).toLowerCase();
  const runningJobs=jobs.filter(x=>st(x)==="running");
  const queuedJobs=jobs.filter(x=>st(x)==="queued");
  const failedJobs=jobs.filter(x=>["error","cancelled"].includes(st(x)));
  const pending=runningJobs.concat(queuedJobs);
  let remaining=0;
  for(const job of pending){
    const preview=String(job.kind||"")==="seed_preview";
    const done=Number(preview?job.images_done:(job.atlas_done??job.step??0))||0;
    const total=Number(preview?job.images_total:(job.atlas_total??job.total_steps??0))||0;
    if(total>done)remaining+=total-done;
  }
  const rate=state.progress&&state.progress.rate>0?state.progress.rate:(state.lastRate||0);
  if(state.progress&&state.progress.rate>0)state.lastRate=state.progress.rate;
  const seconds=rate>0?remaining/rate:0;
  const tank=$("fuelTank");
  const pct=FUEL_CAPACITY_SEC>0?Math.min(100,seconds/FUEL_CAPACITY_SEC*100):0;
  $("fuelBar").style.width=pct.toFixed(2)+"%";
  $("fuelLabel").textContent=remaining>0?(rate>0?`${humanDuration(seconds)} of work`:`${remaining.toLocaleString()} cells`):"IDLE";
  $("statRunning").textContent=runningJobs.length;
  $("statPending").textContent=queuedJobs.length;
  $("statFailed").textContent=failedJobs.length;
  $("statFailed").parentElement.classList.toggle("warn",failedJobs.length>0);
  // Nothing running is only worth flagging when work is queued behind it; an
  // empty queue is idle, not a fault.
  const stalled=runningJobs.length===0&&queuedJobs.length>0;
  $("statRunning").parentElement.classList.toggle("warn",stalled);
  const clear=$("clearErrors");
  if(clear)clear.disabled=failedJobs.length===0||!!state.clearing;
  state.failedJobIds=failedJobs.map(x=>x.id);
  $("fuelDetail").textContent=remaining>0
    ?`${remaining.toLocaleString()} cells remaining${rate>0?` · ${rate.toFixed(2)} fps`:" · rate unknown"}`
    :state.activeJob?"Queue complete":(state.lastRange?"Idle · Continue extends the atlas":"Idle — ready to launch");
  tank.classList.toggle("idle",remaining===0);
  tank.classList.toggle("stalled",stalled);
  tank.classList.toggle("low",remaining>0&&rate>0&&seconds<FUEL_LOW_SEC);
  updateContinue();
}
function tickProgress(){
  const p=state.progress;
  if(!p||!(p.total>0))return;
  const projected=p.rate>0?Math.min(p.total,p.done+p.rate*((Date.now()-p.at)/1000)):p.done;
  $("progressBar").style.width=(projected/p.total*100).toFixed(2)+"%";
  $("progressText").textContent=`${Math.floor(projected).toLocaleString()} / ${p.total.toLocaleString()}`;
  if(p.rate>0){
    const remaining=(p.total-projected)/p.rate;
    if(remaining>0)$("eta").textContent=remaining<90?Math.ceil(remaining)+" sec":(remaining/60).toFixed(1)+" min";
  }
  renderEtaBar();

// fuel.js
  $("fuelLabel").textContent=remaining>0?(rate>0?`${humanDuration(seconds)} of work`:`${remaining.toLocaleString()} cells`):"IDLE";
  $("statRunning").textContent=runningJobs.length;
  $("statPending").textContent=queuedJobs.length;
  $("statFailed").textContent=failedJobs.length;
  $("statFailed").parentElement.classList.toggle("warn",failedJobs.length>0);
  // Nothing running is only worth flagging when work is queued behind it; an
  // empty queue is idle, not a fault.
  const stalled=runningJobs.length===0&&queuedJobs.length>0;
  $("statRunning").parentElement.classList.toggle("warn",stalled);
  const clear=$("clearErrors");
  if(clear)clear.disabled=failedJobs.length===0||!!state.clearing;
  state.failedJobIds=failedJobs.map(x=>x.id);
  $("fuelDetail").textContent=remaining>0
    ?`${remaining.toLocaleString()} cells remaining${rate>0?` · ${rate.toFixed(2)} fps`:" · rate unknown"}`
    :state.activeJob?"Queue complete":(state.lastRange?"Idle · Continue extends the atlas":"Idle — ready to launch");
  tank.classList.toggle("idle",remaining===0);
  tank.classList.toggle("stalled",stalled);
  tank.classList.toggle("low",remaining>0&&rate>0&&seconds<FUEL_LOW_SEC);
  updateContinue();
}
function tickProgress(){
  const p=state.progress;
  if(!p||!(p.total>0))return;
  const projected=p.rate>0?Math.min(p.total,p.done+p.rate*((Date.now()-p.at)/1000)):p.done;
  $("progressBar").style.width=(projected/p.total*100).toFixed(2)+"%";
  $("progressText").textContent=`${Math.floor(projected).toLocaleString()} / ${p.total.toLocaleString()}`;
  if(p.rate>0){
    const remaining=(p.total-projected)/p.rate;
    if(remaining>0)$("eta").textContent=remaining<90?Math.ceil(remaining)+" sec":(remaining/60).toFixed(1)+" min";
  }
  renderEtaBar();
}
async function prefillRecent(){

// prefill.js
  if(state.frames.length)return;
  try{
    const r=await fetch("/api/recent-images?limit=48"),j=await r.json();
    const images=(j.images||[]).filter(x=>x.path).reverse();
    if(!images.length)return;
    if(state.frames.length)return;
    state.prefilled=true;
    $("stageMessage").querySelector("strong").textContent="PREVIOUS RENDERS";
    $("stageMessage").querySelector("span").textContent="Showing recent work while the live stream connects.";
    for(const image of images)ingestFrame({url:image.path,index:null,seed:"",total:0,updateProgress:false});
  }catch{}
}
function clearPrefilled(){
  if(!state.prefilled)return;
  state.prefilled=false;
  state.frames=[];
  const strip=$("filmstrip");
  if(strip)strip.querySelectorAll("img").forEach(x=>x.remove());

// presets.js
}
document.querySelectorAll("[data-run]").forEach(b=>b.onclick=()=>{document.querySelectorAll("[data-run]").forEach(x=>x.classList.remove("active"));b.classList.add("active");state.runType=b.dataset.run});
document.querySelectorAll("[data-study]").forEach(b=>b.onclick=()=>{document.querySelectorAll("[data-study]").forEach(x=>x.classList.remove("active"));b.classList.add("active");state.studyType=b.dataset.study;updateJobReady()});
const presets={continuity:{mode:"elliptic",seedLock:.58,shellScale:1.02,shellCoupling:.7,cacheThreshold:.22,rates:[.18,.06,-.04,.04,-.02,.01]},cinema:{mode:"elliptic",seedLock:.3,shellScale:1.12,shellCoupling:.92,cacheThreshold:.3,rates:[.32,.11,-.09,.08,-.06,.04]},parallax:{mode:"omega",seedLock:.2,shellScale:1.34,shellCoupling:1.45,cacheThreshold:.24,rates:[.5,.28,-.16,.22,-.12,.09]},dream:{mode:"omega",seedLock:.12,shellScale:1.55,shellCoupling:.55,cacheThreshold:.34,rates:[.72,-.38,.44,.31,-.27,.19]},sway:{mode:"sway",seedLock:.42,shellScale:1.08,shellCoupling:1.1,cacheThreshold:.27,rates:[.24,.09,-.05,.07,-.03,.02]}};
const promptTreatments={continuity:"Unbroken identity-first tracking: preserve exact anatomy, silhouette, material, lighting logic, and spatial orientation from frame to frame; measured physical motion with no morphing, substitution, costume drift, or discontinuous camera jumps.",cinema:"A composed cinematic orbit with deliberate lens language, graceful elliptical camera movement, controlled depth separation, motivated light, restrained spectacle, and stable subject identity throughout the sequence.",parallax:"Pronounced deep parallax: foreground, subject, architecture, and horizon travel at clearly separated rates; wide spatial legibility, strong lateral displacement, coherent perspective, and no flattened backdrop.",dream:"A lucid dream sphere with expressive four-dimensional drift, luminous atmospheric transitions, poetic environmental transformation, and a single unmistakably consistent subject acting as the visual anchor.",sway:"A reversible return-sway movement: travel outward, decelerate, reverse gracefully, and return toward the visual home state with matched composition, identity, rhythm, and spatial continuity."};
document.querySelectorAll("[data-preset]").forEach(b=>b.onclick=()=>{const key=b.dataset.preset,p=presets[key],prompt=$("prompt"),base=prompt.value.split("\\n\\nMotion treatment:")[0].trim();prompt.value=`${base}\\n\\nMotion treatment: ${promptTreatments[key]}`;$("mode").value=p.mode;$("seedLock").value=p.seedLock;$("latentDistance").value=p.shellScale;$("shellCoupling").value=p.shellCoupling;$("cacheThreshold").value=p.cacheThreshold;["dimXY","dimXZ","dimXW","dimYZ","dimYW","dimZW"].forEach((id,i)=>$(id).value=p.rates[i]);document.querySelectorAll(".parameter input,.dimAxis input,#latentDistance").forEach(x=>x.dispatchEvent(new Event("input")));document.querySelectorAll("[data-preset]").forEach(x=>x.classList.remove("selected"));b.classList.add("selected");updateJobReady();toast(`${b.querySelector("strong").textContent} prompt generated`)});
function surpriseOrbit(){const keys=Object.keys(presets),key=keys[Math.floor(Math.random()*keys.length)],p=presets[key];$("mode").value=p.mode;$("seedLock").value=Math.max(0,Math.min(.95,p.seedLock+(Math.random()-.5)*.12)).toFixed(2);$("latentDistance").value=Math.max(.01,p.shellScale+(Math.random()-.5)*.18).toFixed(2);$("shellCoupling").value=(p.shellCoupling+(Math.random()-.5)*.3).toFixed(2);$("cacheThreshold").value=p.cacheThreshold;$("seed").value=String(Math.floor(Math.random()*890000000)+10000000);["dimXY","dimXZ","dimXW","dimYZ","dimYW","dimZW"].forEach((id,i)=>$(id).value=(p.rates[i]*(.78+Math.random()*.44)).toFixed(2));document.querySelectorAll(".parameter input,.dimAxis input,#latentDistance").forEach(x=>x.dispatchEvent(new Event("input")));toast(`${key.toUpperCase()} orbit composed · ready to start`);updateJobReady()}
document.querySelectorAll(".groupTitle").forEach(b=>b.onclick=()=>{b.parentElement.classList.toggle("open");b.querySelector("i").textContent=b.parentElement.classList.contains("open")?"−":"+"});
const geometryCopy={elliptic:["ELLIPTIC · CONTROLLED ORBIT","A smooth closed arc around the home identity. Best for stable cinematic motion with gradual change."],omega:["OMEGA / SO(4) · COMPLEX ORBIT","Couples all six rotation planes in four-dimensional latent space. Produces richer viewpoint and form evolution with less predictable motion."],sway:["SWAY · OUT AND RETURN","Moves away from the home state and reverses along a related route. Best for reversible motion and visual return."],oscillatory:["OSCILLATORY · RHYTHMIC PATH","Uses repeating multi-axis waves. Best for cyclical, pulsing, or mechanically rhythmic motion."]},traversalCopy={row_serpentine:"Row serpentine walks each row forward, then reverses the next row to avoid a hard return jump.",column_serpentine:"Column serpentine walks down one column and up the next, favoring vertical neighborhood continuity.",raster:"Raster always moves in one direction and jumps back at each boundary; useful for diagnostics, less smooth for motion."};function updateGeometryHelp(){const g=geometryCopy[$("mode").value];$("geometryName").textContent=g[0];$("geometryHelp").textContent=g[1];$("traversalHelp").textContent=traversalCopy[$("order").value]}$("mode").addEventListener("change",updateGeometryHelp);$("order").addEventListener("change",updateGeometryHelp);
document.querySelectorAll(".parameter input,.dimAxis input,#latentDistance").forEach(input=>input.oninput=()=>{const out=document.querySelector(`output[data-for="${input.id}"]`);if(out)out.value=Number(input.value).toFixed(2);const shell=Math.max(.82,Math.min(1.14,.88+numeric("latentDistance")*.1)),spin=["dimXY","dimXZ","dimXW","dimYZ","dimYW","dimZW"].reduce((n,id)=>n+numeric(id),0)*5;$("sphere").style.setProperty("--shell",shell);$("sphere").style.setProperty("--spin",spin+"deg");$("sphere").style.filter=`drop-shadow(0 0 ${18+numeric("seedLock")*42}px rgba(85,231,238,.24))`});
document.querySelectorAll("[data-view]").forEach(b=>b.onclick=()=>{document.querySelectorAll("[data-view]").forEach(x=>x.classList.remove("active"));b.classList.add("active");const frame=b.dataset.view==="frame"&&state.frames.length;$("assetStage").style.display=frame?"block":"none";$("sphere").style.display=frame?"none":"block"});
$("focusStage").onclick=()=>{document.querySelector(".workspace").classList.toggle("stageFocused");$("focusStage").textContent=document.querySelector(".workspace").classList.contains("stageFocused")?"↙":"↗";setTimeout(drawMap,350)};
$("indexStart").oninput=updateRange;$("cells").oninput=()=>{$("cellsNumber").value=$("cells").value;updateRange()};$("cellsNumber").oninput=()=>{$("cells").value=Math.max(1,Math.min(65536,numeric("cellsNumber")||1));updateRange()};$("planButton").onclick=renderSeedBatch;$("continueButton").onclick=continueAtlas;$("launchButton").onclick=()=>{if(!state.model.loaded){state.model.pendingPreview=true;$("stageMessage").innerHTML="<strong>LOADING FLUX WORKER</strong><span>The visible prompt will begin as one coherent 32-image GPU batch.</span>";return modelAction(state.model.downloaded?"/api/model/load":"/api/model/download")}submit(false)};$("launchFromPlan").onclick=()=>{$("manifestDialog").close();submit(false)};$("refreshJobs").onclick=refreshJobs;
document.querySelectorAll(".dialogClose,.dialogCloseButton").forEach(b=>b.onclick=()=>$("manifestDialog").close());
$("helpButton").onclick=()=>toast("Plan a coherent latent path, then launch it into the resident FLUX worker.");
$("clearErrors").onclick=async()=>{
  const count=(state.failedJobIds||[]).length;
  if(!count||state.clearing)return;
  state.clearing=true;$("clearErrors").disabled=true;
  try{
    const r=await fetch("/api/jobs/prune",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({keep:0,statuses:["error","cancelled"]})});
    const j=await r.json();
    if(!r.ok||!j.ok)throw Error(j.error||"Could not clear failed jobs");
    toast(`Cleared ${(j.removed||[]).length} failed job${(j.removed||[]).length===1?"":"s"}`);
    refreshJobs();

// init.js
  }catch(e){toast(e.message)}
  finally{state.clearing=false}
};
$("downloadModel").onclick=()=>modelAction("/api/model/download");$("loadModel").onclick=()=>modelAction("/api/model/load");
$("atlasForm").addEventListener("input",()=>{sessionStorage.setItem("motionAtlasTitle",$("id").value);sessionStorage.setItem("motionAtlasPrompt",$("prompt").value);sessionStorage.setItem("motionAtlasSeed",$("seed").value);updateJobReady()});document.addEventListener("keydown",e=>{if(["INPUT","TEXTAREA","SELECT"].includes(document.activeElement?.tagName))return;if(e.key==="ArrowLeft"){e.preventDefault();cycleFrame(-1)}if(e.key==="ArrowRight"){e.preventDefault();cycleFrame(1)}});seedPage();drawMap();updateRange();updateGeometryHelp();updateJobReady();refreshJobs();connectStreams();prefillRecent();setInterval(tickProgress,200);window.addEventListener("resize",drawMap);

// Bootstrap: immediately fetch model + nexus + worker state on page load
(async function(){
  try{
    const r=await fetch("/api/health"),j=await r.json();
    if(j.ok){
      const downloaded=!!j.loaded||j.worker_running;
      renderModel({downloaded,loaded:!!j.loaded,device:j.device||"",downloading:false,message:j.loaded?"Model resident on GPU.":""});
      $("workerState").textContent=j.worker_running?"WORKER LIVE":"SUITE LIVE";
      document.querySelectorAll('.stateRow[data-step="worker"]').forEach(el=>{el.classList.remove("active");el.classList.toggle("done",j.worker_running)});
      document.querySelectorAll('.stateRow[data-step="model"]').forEach(el=>{el.classList.remove("active");el.classList.toggle("done",!!j.loaded)});
      document.querySelectorAll('.stateRow[data-step="assets"]').forEach(el=>{el.classList.remove("active");el.classList.toggle("done",j.worker_running)});
    }
  }catch(e){console.warn("health bootstrap",e)}
  try{
    const r=await fetch("/api/nexus/health"),j=await r.json();
    document.querySelectorAll('.stateRow[data-step="nexus"]').forEach(el=>{
      el.classList.toggle("done",!!j.nexus_up);
      el.classList.toggle("active",!j.nexus_up);
      const sm=el.querySelector("small");if(sm)sm.textContent=j.nexus_up?"Connected":"Offline (optional)";
    });
  }catch(e){
    document.querySelectorAll('.stateRow[data-step="nexus"]').forEach(el=>{el.classList.add("active");const sm=el.querySelector("small");if(sm)sm.textContent="Skipped"});
  }
  // Update system label
  const rows=document.querySelectorAll(".stateRow");
  const done=[...rows].filter(el=>el.classList.contains("done")).length;
  const lbl=$("systemLabel");
  if(lbl){lbl.textContent=done>=rows.length-1?"READY":done>0?"PARTIAL":"OFFLINE";lbl.style.color=done>=rows.length-1?"#34d399":done>0?"#fbbf24":"#ff5f6d"}
})();

