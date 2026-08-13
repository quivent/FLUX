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
