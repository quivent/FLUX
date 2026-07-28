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
