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
// connectSocket replaces the old per-feed EventSource with a real WebSocket:
// EventSource auto-reconnects on its own, a raw WebSocket doesn't, so that
// reconnect-with-backoff is reimplemented here explicitly, once, shared by
// every feed (jobs/telemetry/processes/assets/model).
function connectSocket(path,onData,onReconnecting){
  const url=(location.protocol==="https:"?"wss://":"ws://")+location.host+path;
  let ws,closedByUs=false,retries=0;
  const connect=()=>{
    ws=new WebSocket(url);
    ws.onopen=()=>{retries=0};
    ws.onmessage=e=>{let data=null;try{data=JSON.parse(e.data)}catch{}if(data)onData(data)};
    ws.onerror=()=>{ws.close()};
    // Exponential backoff with jitter, capped at 10s: without this, every
    // open tab reconnects in lockstep on a fixed timer after any blip,
    // hammering the server the instant it's back.
    ws.onclose=()=>{if(closedByUs)return;onReconnecting&&onReconnecting();const delay=Math.min(1000*2**retries,10000)+Math.random()*300;retries++;setTimeout(connect,delay)};
  };
  connect();
  return{close:()=>{closedByUs=true;ws&&ws.close()}};
}
function connectStreams(){connectSocket("/api/jobs/ws",data=>{try{const shown=state.pinnedJob||state.shownJob;if(shown)acceptAssetJob(shown);else (data.jobs||[]).filter(x=>String(x.status).toLowerCase()==="running").slice(0,1).forEach(x=>acceptAssetJob(x.id))}catch{}try{const hasRunning=(data.jobs||[]).some(j=>j.status==="running"||j.status==="queued");if(hasRunning&&!state.slideshowTimer){state.slideshowTimer=setInterval(playFrame,340)}else if(!hasRunning&&state.slideshowTimer){clearInterval(state.slideshowTimer);state.slideshowTimer=null}}catch{}try{renderJobFeed(data)}catch{}},()=>{$("workerState").textContent="RECONNECTING"});connectSocket("/api/telemetry/ws",data=>{try{renderGPU(data.gpu)}catch{}});connectSocket("/api/telemetry/processes/ws",data=>{try{renderGPUProcess(data)}catch{}});connectSocket("/api/assets/ws",event=>{try{if(state.acceptedAssetJobs.has(event.job_id))ingestAssetEvent(event);else{const pending=state.pendingAssets.get(event.job_id)||[];pending.push(event);state.pendingAssets.set(event.job_id,pending)}}catch{}},()=>{const el=$("stageMessage");if(el&&el.querySelector("span"))el.querySelector("span").textContent="Piper reconnecting to the asset socket."});connectSocket("/api/model/ws",data=>{try{renderModel(data)}catch{}})}
async function modelAction(path){try{const r=await fetch(path,{method:"POST"}),j=await r.json();if(!r.ok||!j.ok)throw Error(j.error||"Model action failed");$("modelMessage").textContent=String(j.status||"Working").toUpperCase();$("modelProgressBar").classList.add("busy")}catch(e){toast(e.message)}}
