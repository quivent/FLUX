const $=id=>document.getElementById(id);
const esc=s=>{const d=document.createElement("div");d.textContent=s??"";return d.innerHTML};
let assets=[],jobs=[],mode="sets",setFilter="",viewed=[],viewIndex=0,motionTimer=null,swipeX=null,swipeCarry=0,displayLimit=180;
const SETS_PER_PAGE=25;
const SET_TILES=5;
const SET_CYCLE_MS=120;
let stripTimer=null,stripVisible=new Set(),stripObserver=null;
let collections=[],setPage=0;
function collectionJobId(c){return String(c.raw_name||c.name||"").replace(/\.sphere$/,"")}
function renderSetPager(pages){const host=$("setPager");if(!host)return;if(pages<=1){host.innerHTML="";host.hidden=true;return}host.hidden=false;const btn=(p,label,dis)=>`<button type="button" data-set-page="${p}" ${dis?"disabled":""}>${label}</button>`;let out=btn(setPage-1,"\u2190 PREV",setPage===0);out+=`<span>${setPage+1} / ${pages} \u00b7 ${collections.length} collections</span>`;out+=btn(setPage+1,"NEXT \u2192",setPage>=pages-1);host.innerHTML=out;host.querySelectorAll("[data-set-page]").forEach(b=>b.onclick=()=>{setPage=Number(b.dataset.setPage);render();window.scrollTo({top:0,behavior:"smooth"})})}
let openPath="",openImages=[],openName="";
function slugOf(p){return String(p||"").replace(/^atlas\//,"").replace(/^batches\//,"").replace(/\.sphere$/,"")}
async function openCollection(path,push=true){
  if(!path)return;
  openPath=path;openName=slugOf(path);
  try{
    const r=await fetch("/api/collection?path="+encodeURIComponent(path));
    const j=await r.json();
    if(!r.ok||!j.ok)throw Error(j.error||"Could not open collection");
    openImages=(j.images||[]).filter(x=>x.url);
    openName=(j.collection&&j.collection.name)||openName;
  }catch(e){openImages=[];toast&&toast(e.message)}
  if(push)history.pushState({set:path},"","?set="+encodeURIComponent(path));
  mode="images";setFilter="";syncModes();render();
  window.scrollTo({top:0,behavior:"smooth"});
}
function closeCollection(push=true){
  openPath="";openImages=[];openName="";
  if(push)history.pushState({},"",location.pathname);
  mode="sets";syncModes();render();
}
async function deleteCollection(path,name){
  if(!path)return;
  if(!confirm(`Move "${name||slugOf(path)}" to trash?\n\nFiles are moved to outputs/.trash and can be restored from disk.`))return;
  try{
    const r=await fetch("/api/collection/delete?path="+encodeURIComponent(path),{method:"POST"});
    const j=await r.json();
    if(!r.ok||!j.ok)throw Error(j.error||"Delete failed");
    collections=collections.filter(c=>c.path!==path);
    render();
    loadCollections();
  }catch(e){alert(e.message)}
}
window.addEventListener("popstate",ev=>{
  const p=(ev.state&&ev.state.set)||new URLSearchParams(location.search).get("set")||"";
  if(p)openCollection(p,false);else closeCollection(false);
});
function startStripCycle(slice){
  if(stripTimer){clearInterval(stripTimer);stripTimer=null}
  stripVisible.clear();
  if(stripObserver){stripObserver.disconnect();stripObserver=null}
  const cards=[...document.querySelectorAll("[data-strip]")];
  if(!cards.length)return;
  const frames=cards.map((card,idx)=>{
    const c=slice[idx]||{};
    const fr=(c.samples&&c.samples.length?c.samples:[c.thumbnail]).filter(Boolean);
    return {imgs:[...card.querySelectorAll(".setStrip img")],fr:fr,off:0};
  });
  stripObserver=new IntersectionObserver(es=>{
    for(const e of es){
      const i=Number(e.target.dataset.strip);
      if(e.isIntersecting)stripVisible.add(i);else stripVisible.delete(i);
    }
  },{rootMargin:"150px"});
  cards.forEach(c=>stripObserver.observe(c));
  stripTimer=setInterval(()=>{
    for(const i of stripVisible){
      const f=frames[i];
      if(!f||f.fr.length<2)continue;
      f.off=(f.off+1)%f.fr.length;
      f.imgs.forEach((img,n)=>{
        const pick=f.fr[(f.off+Math.floor(n*f.fr.length/SET_TILES))%f.fr.length];
        if(pick&&img.getAttribute("src")!==pick)img.setAttribute("src",pick);
      });
    }
  },SET_CYCLE_MS);
}
async function loadCollections(){try{const r=await fetch("/api/collections");const j=await r.json();collections=(j.collections||[]).slice().sort((a,b)=>Number(b.updated||0)-Number(a.updated||0));render()}catch{}}
const diagnostic=v=>/(\bproof\b|motion-probe)/i.test(String(v||""));
const deletionCandidate=(id,items)=>{const job=jobs.find(x=>x.id===id)||{},expected=Number(job.atlas_total??job.cells??job.total_steps??0),active=["queued","running","cancelling"].includes(String(job.status||"").toLowerCase());return items.length===1&&expected>1&&!active};
const matching=()=>{const q=$("registrySearch").value.trim().toLowerCase();return assets.filter(a=>(!setFilter||a.job_id===setFilter)&&(!q||String(a.path||a.access_url).toLowerCase().includes(q)))};
const thumb=a=>`/api/asset/thumbnail?w=384&src=${encodeURIComponent(a.access_url)}`;
function imageCard(a,i){const eager=i<12;return `<button class="assetCard" style="--reveal:${Math.min(i,18)*34}ms" data-asset="${i}" aria-label="Open image"><span class="assetGlow"></span><img src="${esc(thumb(a))}" loading="${eager?"eager":"lazy"}" decoding="async" ${i<6?'fetchpriority="high"':""} alt=""></button>`}
function revealImages(){document.querySelectorAll("#assetWall img").forEach(img=>{const ready=()=>img.closest("button")?.classList.add("imageReady");if(img.complete&&img.naturalWidth)ready();else img.addEventListener("load",ready,{once:true})})}
function render(){
 const visible=matching();
 if(mode==="sets"){const per=SETS_PER_PAGE;const pages=Math.max(1,Math.ceil(collections.length/per));if(setPage>=pages)setPage=pages-1;if(setPage<0)setPage=0;const slice=collections.slice(setPage*per,setPage*per+per);$("assetWall").classList.add("setWall");$("assetWall").innerHTML=collections.length?slice.map((c,i)=>{const done=Number(c.count||0),total=Number(c.total||0);const pct=total>0?Math.min(100,done/total*100):0;const partial=total>0&&done<total;const fr=(c.samples&&c.samples.length?c.samples:[c.thumbnail]).filter(Boolean);const tiles=Array.from({length:SET_TILES},(_,n)=>`<img src="${esc(fr[Math.floor(n*fr.length/SET_TILES)]||fr[0]||"")}" loading="${i<6?"eager":"lazy"}" decoding="async" alt="">`).join("");return `<div class="setRow"><button class="setCard assetCard" style="--reveal:${Math.min(i,12)*45}ms" data-open="${esc(c.path||"")}" data-strip="${i}" aria-label="Open collection ${esc(c.name||"")}"><span class="setStrip">${tiles}</span><strong>${esc(c.name||collectionJobId(c))}</strong><small>${done.toLocaleString()}${total>0?` / ${total.toLocaleString()}`:""} frames${c.updated_text?` \u00b7 ${esc(c.updated_text)}`:""}</small>${partial?`<span class="setProgress"><i style="width:${pct.toFixed(1)}%"></i></span>`:""}</button><button class="setDelete" type="button" data-del="${esc(c.path||"")}" data-delname="${esc(c.name||"")}" title="Move this collection to trash">DELETE</button></div>`}).join(""):'<div class="detailEmpty">No generated sets yet.</div>';renderSetPager(pages);const _h=$("openBarHost");if(_h)_h.innerHTML="";startStripCycle(slice);document.querySelectorAll("[data-open]").forEach(b=>b.onclick=()=>openCollection(b.dataset.open));document.querySelectorAll("[data-del]").forEach(b=>b.onclick=async ev=>{ev.stopPropagation();await deleteCollection(b.dataset.del,b.dataset.delname)})}else{const list=openPath?openImages.map((im,n)=>({id:"open-"+n,job_id:openName,path:im.name,access_url:im.url,url:im.url,media_type:"image/png",cell_index:n})):visible;const shown=list.slice(0,displayLimit);$("assetWall").classList.remove("setWall");const bar=openPath?`<div class="openBar"><button type="button" id="backToSets">\u2190 ALL COLLECTIONS</button><strong>${esc(openName)}</strong><span>${list.length.toLocaleString()} frames</span><button type="button" class="setDelete" data-del="${esc(openPath)}" data-delname="${esc(openName)}">DELETE</button></div>`:"";$("assetWall").insertAdjacentHTML;$("assetWall").innerHTML=list.length?shown.map(imageCard).join("")+(list.length>shown.length?`<button class="galleryMore" id="galleryMore" aria-label="Reveal more images">+</button>`:""):'<div class="detailEmpty">No generated assets yet.</div>';const host=$("openBarHost");if(host)host.innerHTML=bar;document.querySelectorAll("[data-asset]").forEach(b=>b.onclick=()=>showAsset(Number(b.dataset.asset),list));if($("backToSets"))$("backToSets").onclick=()=>closeCollection();document.querySelectorAll(".openBar [data-del]").forEach(b=>b.onclick=async()=>{await deleteCollection(b.dataset.del,b.dataset.delname);closeCollection()});if($("galleryMore"))$("galleryMore").onclick=()=>{displayLimit+=180;render()}}
 revealImages();
 $("assetCount").textContent=assets.length.toLocaleString();$("ledgerStamp").textContent=setFilter?`SET · ${visible.length} IMAGES`:`LIVE · ${new Date().toLocaleTimeString()}`
}
function syncModes(){document.querySelectorAll("[data-gallery-mode]").forEach(b=>b.classList.toggle("active",b.dataset.galleryMode===mode))}
function preloadAround(){for(let d=-10;d<=10;d++){const a=viewed[(viewIndex+d+viewed.length)%viewed.length];if(a){const img=new Image();img.src=a.access_url}}}
function showAsset(i,list=matching()){viewed=list;viewIndex=(i+viewed.length)%viewed.length;const a=viewed[viewIndex];if(!a)return;$("assetViewerImage").classList.add("loading");$("assetViewerImage").onload=()=>$("assetViewerImage").classList.remove("loading");$("assetViewerImage").src=a.access_url;$("assetViewerLabel").textContent=`${viewIndex+1} / ${viewed.length} · ${(a.path||a.access_url).split("/").pop()}`;preloadAround();if(!$("assetViewer").open)$("assetViewer").showModal()}
function cycle(d){if(viewed.length)showAsset((viewIndex+d+viewed.length)%viewed.length,viewed)}
function toggleMotion(){if(motionTimer){clearInterval(motionTimer);motionTimer=null;$("motionPlay").textContent="▶ PLAY MOTION";return}$("motionPlay").textContent="Ⅱ PAUSE";motionTimer=setInterval(()=>cycle(1),85)}
function showCollection(id){const job=jobs.find(x=>x.id===id)||{},items=assets.filter(x=>x.job_id===id);$("collectionTitle").textContent=job.prompt||"Generated collection";$("collectionSettings").innerHTML=[["Resolution",job.width&&job.height?`${job.width} × ${job.height}`:"—"],["Steps",job.steps||job.total_steps||"—"],["Guidance",job.guidance||"—"],["Latent distance",job.latent_distance||"—"],["Seed",job.seed||"—"],["Images",items.length]].map(([k,v])=>`<div><span>${k}</span><strong>${esc(v)}</strong></div>`).join("");$("openCollection").onclick=()=>{$("collectionViewer").close();setFilter=id;mode="images";syncModes();render()};$("collectionViewer").showModal()}
async function load(){requestAnimationFrame(()=>document.body.classList.add("galleryReady"));try{const r=await fetch("/api/atlas/catalog"),j=await r.json();if(!r.ok||!j.ok)throw Error();assets=(j.assets||[]).filter(a=>!diagnostic(a.job_id)&&!diagnostic(a.path));jobs=(j.jobs||[]).filter(x=>!diagnostic(x.id)&&!diagnostic(x.prompt));$("jobCount").textContent=jobs.length.toLocaleString();$("seedCount").textContent=(j.seeds||[]).filter(x=>!diagnostic(x.source_job_id)).length.toLocaleString();$("registryState").textContent="LIVE ASSETS";render()}catch{$("registryState").textContent="ASSET FEED ATTENTION"}}
$("registrySearch").oninput=render;$("assetVault").onclick=()=>{setFilter="";mode="sets";syncModes();render()};document.querySelectorAll("[data-gallery-mode]").forEach(b=>b.onclick=()=>{mode=b.dataset.galleryMode;setFilter="";syncModes();render()});$("closeAssetViewer").onclick=()=>{if(motionTimer)toggleMotion();$("assetViewer").close()};$("assetPrev").onclick=()=>cycle(-1);$("assetNext").onclick=()=>cycle(1);$("motionPlay").onclick=toggleMotion;document.addEventListener("keydown",e=>{if(!$("assetViewer").open)return;if(e.key==="ArrowLeft"){e.preventDefault();cycle(-1)}if(e.key==="ArrowRight"){e.preventDefault();cycle(1)}if(e.key===" "){e.preventDefault();toggleMotion()}});
$("assetViewerImage").addEventListener("pointerdown",e=>{swipeX=e.clientX;swipeCarry=0;e.currentTarget.setPointerCapture(e.pointerId)});
$("assetViewerImage").addEventListener("pointermove",e=>{if(swipeX==null)return;swipeCarry+=e.clientX-swipeX;swipeX=e.clientX;while(Math.abs(swipeCarry)>=22){cycle(swipeCarry<0?1:-1);swipeCarry+=swipeCarry<0?22:-22}});
$("assetViewerImage").addEventListener("pointerup",()=>{swipeX=null;swipeCarry=0});$("assetViewerImage").addEventListener("pointercancel",()=>{swipeX=null;swipeCarry=0});
$("closeCollectionViewer").onclick=()=>$("collectionViewer").close();
const stream=new EventSource("/api/assets/events");stream.addEventListener("asset",e=>{const event=JSON.parse(e.data),a=event.asset||{};if(diagnostic(event.job_id)||diagnostic(a.path))return;if(!assets.some(x=>x.id===a.id)){assets.unshift({...a,job_id:event.job_id});render()}});
const model=new EventSource("/api/model/events");model.addEventListener("model",e=>{const m=JSON.parse(e.data),top=$("modelTopState");top.className="modelTopState "+(m.loaded?"loaded":m.downloaded?"downloaded":"");top.lastChild.textContent=m.loaded?" MODEL LOADED":m.downloaded?" MODEL READY":" MODEL MISSING"});
load();loadCollections();setInterval(loadCollections,20000);const _initSet=new URLSearchParams(location.search).get("set");if(_initSet)openCollection(_initSet,false);
