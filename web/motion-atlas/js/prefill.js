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
