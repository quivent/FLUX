// fuel.js
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
}
