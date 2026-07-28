const $=id=>document.getElementById(id);
const values=()=>({focal:Number($("focal").value),aperture:Number($("aperture").value)/10,focus:Number($("focus").value)/10,squeeze:Number($("squeeze").value)/100,distortion:Number($("distortion").value),halation:Number($("halation").value)});
function toast(s){$("toast").textContent=s;$("toast").classList.add("show");setTimeout(()=>$("toast").classList.remove("show"),2200)}
function compile(){
  const v=values(),field=v.focal<28?"ULTRA WIDE":v.focal<45?"WIDE":v.focal<70?"NORMAL":v.focal<120?"COMPRESSED":"TELEPHOTO";
  $("focalOut").value=`${v.focal} mm`;$("apertureOut").value=`T${v.aperture.toFixed(1)}`;$("focusOut").value=`${v.focus.toFixed(1)} m`;$("squeezeOut").value=`${v.squeeze.toFixed(2)}×`;$("distortionOut").value=`${v.distortion}%`;$("halationOut").value=`${v.halation}%`;
  $("fieldReadout").textContent=field;$("planeReadout").textContent=`${v.focus.toFixed(1)} M`;$("gateReadout").textContent=`${v.squeeze.toFixed(2)}×`;
  $("lensAssembly").style.setProperty("--focal",(v.focal-14)/186);$("lensAssembly").style.setProperty("--aperture",(v.aperture-1.2)/14.8);
  $("compiledOptics").textContent=`${$("opticPrompt").value.trim()}. Photographed through a ${v.focal}mm ${$("character").value} cinema lens at T${v.aperture.toFixed(1)}, focus plane at ${v.focus.toFixed(1)} meters, ${$("focusBehavior").value}, ${v.squeeze.toFixed(2)}x anamorphic squeeze, ${v.distortion}% signed optical distortion, ${v.halation}% controlled halation, physically coherent depth of field, consistent lens geometry and optical behavior across the sequence.`;
}
const presets={anamorphic:[50,20,55,200,"vintage low-contrast",8,35],portrait:[85,18,30,100,"Cooke warmth",0,18],documentary:[35,40,70,100,"modern neutral",-3,6],macro:[100,28,5,100,"Zeiss micro-contrast",2,10]};
document.querySelectorAll("[data-optic]").forEach(b=>b.onclick=()=>{const p=presets[b.dataset.optic];["focal","aperture","focus","squeeze"].forEach((id,i)=>$(id).value=p[i]);$("character").value=p[4];$("distortion").value=p[5];$("halation").value=p[6];document.querySelectorAll("[data-optic]").forEach(x=>x.classList.remove("selected"));b.classList.add("selected");compile()});
document.querySelectorAll("input,textarea,select").forEach(x=>x.addEventListener("input",compile));
$("copyOptics").onclick=async()=>{await navigator.clipboard.writeText($("compiledOptics").textContent);toast("Optical instruction copied")};
$("generateOptics").onclick=async()=>{const button=$("generateOptics");button.disabled=true;$("opticState").textContent="DISPATCHING OPTICAL PROOFS";try{const base=String(Date.now()%800000000+10000000),r=await fetch("/api/render",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({prompt:$("compiledOptics").textContent,model:"dev",backend:"cuda",width:512,height:512,steps:16,guidance:4.2,seed:base,filename:`optical-proof-${Date.now()}.png`,iterations:8})}),j=await r.json();if(!r.ok||!j.ok)throw Error(j.error||"Dispatch failed");$("opticState").textContent="PIPER · PROOFS IN FLIGHT";toast("Eight optical proofs dispatched")}catch(e){$("opticState").textContent="OPTICAL BENCH ATTENTION";toast(e.message)}finally{button.disabled=false}};
const assets=new EventSource("/api/assets/events");assets.addEventListener("asset",e=>{const a=JSON.parse(e.data).asset||{};if(!String(a.name||"").startsWith("optical-proof-"))return;const img=document.createElement("img");img.src=a.access_url;img.title=`Seed ${a.seed||"—"}`;$("opticAssets").prepend(img);$("opticState").textContent="PIPER · OPTICAL PROOF RECEIVED"});
const model=new EventSource("/api/model/events");model.addEventListener("model",e=>{const m=JSON.parse(e.data),top=$("modelTopState");top.className="modelTopState "+(m.loaded?"loaded":m.downloaded?"downloaded":"");top.lastChild.textContent=m.loaded?" MODEL LOADED":m.downloaded?" MODEL READY":" MODEL MISSING"});
const sphere={job:null,points:[],angle:0};
function sphereOrder(job){
  const rows=Number(job.n_rows)||1024,cols=Number(job.n_cols)||64,count=Number(job.total_steps||job.atlas_total)||2048,coupling=Number(job.shell_coupling)||1,base=[];
  for(let row=0;row<rows;row++){const reverse=row%2===1;for(let c=0;c<cols;c++)base.push(row*cols+(reverse?cols-1-c:c))}
  const picked=[];
  if(job.sample_mode==="smooth_even"){const bands=Math.max(1,Math.ceil(count/cols));for(let band=0;band<bands&&picked.length<count;band++){const mu=Math.acos(1-2*(band+.5)/bands),row=Math.min(rows-1,Math.max(0,Math.round(mu*rows/Math.PI-.5)));for(let c=0;c<cols&&picked.length<count;c++)picked.push(row*cols+(band%2?cols-1-c:c))}}
  else for(let i=0;i<count;i++)picked.push(base[Math.min(base.length-1,Math.round(i*(base.length-1)/Math.max(1,count-1)))]);
  return picked.map(index=>{const row=Math.floor(index/cols),col=index%cols,mu=Math.PI*(row+coupling*col/cols+.5)/rows,phi=2*Math.PI*(col+coupling*row/rows+.5)/cols;return{x:Math.sin(mu)*Math.cos(phi),y:Math.cos(mu),z:Math.sin(mu)*Math.sin(phi),index}});
}
async function refreshSphere(){
  try{const r=await fetch("/api/jobs"),j=await r.json(),jobs=Array.isArray(j)?j:(j.jobs||[]);sphere.job=jobs.find(x=>String(x.status).toLowerCase()==="running"&&Number(x.n_rows)>0)||jobs.find(x=>Number(x.n_rows)>0)||null;if(!sphere.job)return;
    sphere.points=sphereOrder(sphere.job);const done=Number(sphere.job.step||sphere.job.atlas_done||0),total=Number(sphere.job.total_steps||sphere.job.atlas_total||sphere.points.length),slots=(Number(sphere.job.n_rows)||1024)*(Number(sphere.job.n_cols)||64);
    $("sphereJob").textContent=sphere.job.id||"ATLAS";$("sphereCoverage").textContent=`${(total/slots*100).toFixed(3)}% · ${done}/${total}`;$("spherePath").textContent=String(sphere.job.sample_mode||"—").toUpperCase();$("sphereSeed").textContent=String(sphere.job.seed||"—");
  }catch{}
}
function drawSphere(){
  const canvas=$("opticSphere"),box=canvas.getBoundingClientRect(),dpr=Math.min(devicePixelRatio||1,2),w=Math.max(1,box.width),h=Math.max(1,box.height);
  if(canvas.width!==Math.round(w*dpr)||canvas.height!==Math.round(h*dpr)){canvas.width=Math.round(w*dpr);canvas.height=Math.round(h*dpr)}
  const c=canvas.getContext("2d");c.setTransform(dpr,0,0,dpr,0,0);c.clearRect(0,0,w,h);
  const cx=w/2,cy=h/2+3,r=Math.min(w,h)*.37,angle=sphere.angle+=.0018,project=p=>{const x=p.x*Math.cos(angle)-p.z*Math.sin(angle),z=p.x*Math.sin(angle)+p.z*Math.cos(angle);return{x:cx+x*r,y:cy-p.y*r,z}};
  c.strokeStyle="#6df1ff22";c.lineWidth=.7;
  for(let lat=-3;lat<=3;lat++){c.beginPath();for(let i=0;i<=96;i++){const phi=i/96*Math.PI*2,mu=Math.PI*(lat+4)/8,p=project({x:Math.sin(mu)*Math.cos(phi),y:Math.cos(mu),z:Math.sin(mu)*Math.sin(phi)});i?c.lineTo(p.x,p.y):c.moveTo(p.x,p.y)}c.stroke()}
  for(let lon=0;lon<12;lon++){c.beginPath();for(let i=0;i<=64;i++){const mu=i/64*Math.PI,phi=lon/12*Math.PI*2,p=project({x:Math.sin(mu)*Math.cos(phi),y:Math.cos(mu),z:Math.sin(mu)*Math.sin(phi)});i?c.lineTo(p.x,p.y):c.moveTo(p.x,p.y)}c.stroke()}
  const done=Number(sphere.job?.step||sphere.job?.atlas_done||0),draw=sphere.points.map((p,i)=>({...project(p),i})).sort((a,b)=>a.z-b.z);
  for(const p of draw){const complete=p.i<done,front=p.i===Math.max(0,done-1);c.beginPath();c.arc(p.x,p.y,front?3:complete?1.4:.65,0,Math.PI*2);c.fillStyle=front?"#fff":complete?`rgba(109,241,255,${p.z>0?.9:.42})`:"#6df1ff35";c.fill();if(front){c.shadowBlur=12;c.shadowColor="#fff";c.fill();c.shadowBlur=0}}
  c.beginPath();c.arc(cx,cy,3.5,0,Math.PI*2);c.fillStyle="#f5bf4f";c.shadowBlur=12;c.shadowColor="#f5bf4f";c.fill();c.shadowBlur=0;requestAnimationFrame(drawSphere);
}
refreshSphere();setInterval(refreshSphere,5000);requestAnimationFrame(drawSphere);
compile();
