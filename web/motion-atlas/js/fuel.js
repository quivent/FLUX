// fuel.js
  $("fuelLabel").textContent=remaining>0?(rate>0?`${humanDuration(seconds)} of work`:`${remaining.toLocaleString()} cells`):"EMPTY";
  $("statRunning").textContent=runningJobs.length;
  $("statPending").textContent=queuedJobs.length;
  $("statFailed").textContent=failedJobs.length;
  $("statFailed").parentElement.classList.toggle("warn",failedJobs.length>0);
  $("statRunning").parentElement.classList.toggle("warn",runningJobs.length===0);
  const clear=$("clearErrors");
  if(clear)clear.disabled=failedJobs.length===0||!!state.clearing;
  state.failedJobIds=failedJobs.map(x=>x.id);
  $("fuelDetail").textContent=remaining>0
    ?`${remaining.toLocaleString()} cells remaining${rate>0?` · ${rate.toFixed(2)} fps`:" · rate unknown"}`
    :state.activeJob?"Queue complete":"Idle — ready to launch";
  tank.classList.toggle("empty",false);
  tank.classList.toggle("low",remaining>0&&rate>0&&seconds<FUEL_LOW_SEC);
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
