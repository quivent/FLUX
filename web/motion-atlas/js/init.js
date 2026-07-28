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

