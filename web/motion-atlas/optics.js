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
const sphere={job:null,points:[],yaw:.35,pitch:-.12,zoom:1,drag:null};
const clamp=(v,a,b)=>Math.max(a,Math.min(b,v));
const angular=(a,b)=>Math.acos(clamp(a.x*b.x+a.y*b.y+a.z*b.z,-1,1));
function sphereOrder(job){
  const rows=Number(job.n_rows)||1024,cols=Number(job.n_cols)||64,count=Number(job.total_steps||job.atlas_total)||2048,coupling=Number(job.shell_coupling)||1,base=[];
  for(let row=0;row<rows;row++){const reverse=row%2===1;for(let c=0;c<cols;c++)base.push(row*cols+(reverse?cols-1-c:c))}
  const picked=[];
  if(job.sample_mode==="smooth_even"){const bands=Math.max(1,Math.ceil(count/cols));for(let band=0;band<bands&&picked.length<count;band++){const mu=Math.acos(1-2*(band+.5)/bands),row=Math.min(rows-1,Math.max(0,Math.round(mu*rows/Math.PI-.5)));for(let c=0;c<cols&&picked.length<count;c++)picked.push(row*cols+(band%2?cols-1-c:c))}}
  else for(let i=0;i<count;i++)picked.push(base[Math.min(base.length-1,Math.round(i*(base.length-1)/Math.max(1,count-1)))]);
  const loop=job.study_type==="loop",scale=Number(job.shell_scale)||1,lock=Number(job.seed_lock)||0;
  return picked.map(index=>{const row=Math.floor(index/cols),col=index%cols,theta=2*Math.PI*(row+coupling*col/cols+.5)/rows,azimuth=2*Math.PI*(col+coupling*row/rows+.5)/cols,mu=theta/2,mode=String(job.mode||"elliptic").toLowerCase();let x,y,z,w=0;
    if(loop){if(mode==="screw"){x=Math.sin((Number(job.spin)||2)*theta)/Math.SQRT2;y=Math.cos((Number(job.spin)||2)*theta)/Math.SQRT2;z=Math.cos(theta)/Math.SQRT2;w=Math.sin(theta)/Math.SQRT2}else{const amp=Number(job.amp)||1,base=Number(job.base)||0,arc=Number(job.arc)||1.5708,a=mode==="sway"?scale*amp*(1-Math.cos(theta))/2:mode==="oscillatory"?scale*amp*Math.sin(theta):scale*(base||arc),phi=(Number(job.orbit)||1)*theta+azimuth;x=Math.sin(a)*Math.cos(phi);y=Math.cos(a);z=Math.sin(a)*Math.sin(phi)}if(lock){y=(1-lock)*y+lock;x*=(1-lock);z*=(1-lock);w*=(1-lock);const n=Math.hypot(x,y,z,w)||1;x/=n;y/=n;z/=n;w/=n}}
    else{x=Math.sin(mu)*Math.cos(azimuth);y=Math.cos(mu);z=Math.sin(mu)*Math.sin(azimuth);w=Math.sin(theta*scale)}
    return{x,y,z,w,index,loop}});
}
async function refreshSphere(){
  try{const r=await fetch("/api/jobs"),j=await r.json(),jobs=Array.isArray(j)?j:(j.jobs||[]);sphere.job=jobs.find(x=>String(x.status).toLowerCase()==="running"&&Number(x.n_rows)>0)||jobs.find(x=>Number(x.n_rows)>0)||null;if(!sphere.job)return;
    sphere.points=sphereOrder(sphere.job);const done=Number(sphere.job.step||sphere.job.atlas_done||0),rows=Number(sphere.job.n_rows)||1024,cols=Number(sphere.job.n_cols)||64,current=sphere.points[Math.max(0,Math.min(done-1,sphere.points.length-1))],index=current?.index??0,row=Math.floor(index/cols),col=index%cols,coupling=Number(sphere.job.shell_coupling)||1,theta=2*Math.PI*(row+coupling*col/cols+.5)/rows,phi=2*Math.PI*(col+coupling*row/rows+.5)/cols,type=String(sphere.job.study_type||"unclassified"),loop=type==="loop",atlas=type==="atlas",closure=sphere.points.length>1?angular(sphere.points[0],sphere.points.at(-1)):0;
    $("sphereSeed").textContent=String(sphere.job.seed||"—");$("sphereRendered").textContent=done.toLocaleString();$("sphereFrontier").textContent=index.toLocaleString();$("sphereTheta").textContent=`${(theta*180/Math.PI).toFixed(2)}°`;$("spherePhi").textContent=`${(phi*180/Math.PI).toFixed(2)}°`;$("sphereRow").textContent=row;$("sphereCol").textContent=col;$("sphereStudyKind").textContent=loop?"loop probes":atlas?"filled probes":"unclassified study";$("sphereMeasureLabel").textContent=loop?"loop closure":atlas?"radial coverage":"classification";$("sphereRho").textContent=loop?`${closure.toFixed(3)} rad`:atlas?`${(done/(rows*cols)*100).toFixed(1)}%`:"required";$("sphereRows").textContent=`${(done/Math.max(1,sphere.points.length)*100).toFixed(1)}%`;
  }catch{}
}
function drawSphere(){
  const canvas=$("opticSphere"),box=canvas.getBoundingClientRect(),dpr=Math.min(devicePixelRatio||1,2),w=Math.max(1,box.width),h=Math.max(1,box.height);
  if(canvas.width!==Math.round(w*dpr)||canvas.height!==Math.round(h*dpr)){canvas.width=Math.round(w*dpr);canvas.height=Math.round(h*dpr)}
  const c=canvas.getContext("2d");c.setTransform(dpr,0,0,dpr,0,0);c.clearRect(0,0,w,h);
  if(!sphere.drag)sphere.yaw+=.0003;
  const cx=w/2,cy=h/2,r=Math.min(w,h)*.425*sphere.zoom,project=p=>{const x1=p.x*Math.cos(sphere.yaw)-p.z*Math.sin(sphere.yaw),z1=p.x*Math.sin(sphere.yaw)+p.z*Math.cos(sphere.yaw),y=p.y*Math.cos(sphere.pitch)-z1*Math.sin(sphere.pitch),z=p.y*Math.sin(sphere.pitch)+z1*Math.cos(sphere.pitch);return{x:cx+x1*r,y:cy-y*r,z,w:p.w}};
  const shell=c.createRadialGradient(cx-r*.28,cy-r*.3,r*.08,cx,cy,r);shell.addColorStop(0,"rgba(44,61,98,.62)");shell.addColorStop(.72,"rgba(18,32,60,.46)");shell.addColorStop(1,"rgba(6,7,11,.28)");c.beginPath();c.arc(cx,cy,r,0,Math.PI*2);c.fillStyle=shell;c.fill();
  const done=Number(sphere.job?.step||sphere.job?.atlas_done||0),sites=(Number(sphere.job?.n_rows)||1024)*(Number(sphere.job?.n_cols)||64),type=String(sphere.job?.study_type||"unclassified"),loop=type==="loop",atlas=type==="atlas",rho=clamp(done/sites,0,1),fillR=r*rho;if(atlas&&fillR>0){c.beginPath();c.arc(cx,cy,fillR,0,Math.PI*2);c.fillStyle="rgba(109,241,255,.42)";c.fill();if(fillR>r*.02){c.beginPath();c.arc(cx,cy,fillR,0,Math.PI*2);c.strokeStyle="rgba(159,247,255,.55)";c.lineWidth=.8;c.stroke()}}
  c.strokeStyle="rgba(93,114,166,.25)";c.lineWidth=.7;
  for(let lat=-3;lat<=3;lat++){c.beginPath();for(let i=0;i<=96;i++){const phi=i/96*Math.PI*2,mu=Math.PI*(lat+4)/8,p=project({x:Math.sin(mu)*Math.cos(phi),y:Math.cos(mu),z:Math.sin(mu)*Math.sin(phi)});i?c.lineTo(p.x,p.y):c.moveTo(p.x,p.y)}c.stroke()}
  for(let lon=0;lon<12;lon++){c.beginPath();for(let i=0;i<=64;i++){const mu=i/64*Math.PI,phi=lon/12*Math.PI*2,p=project({x:Math.sin(mu)*Math.cos(phi),y:Math.cos(mu),z:Math.sin(mu)*Math.sin(phi)});i?c.lineTo(p.x,p.y):c.moveTo(p.x,p.y)}c.stroke()}
  const projected=sphere.points.map((p,i)=>({...project(p),i}));if(loop&&projected.length>1){for(let pass=0;pass<2;pass++){c.beginPath();for(let i=0;i<projected.length;i++){const p=projected[i];i?c.lineTo(p.x,p.y):c.moveTo(p.x,p.y)}c.closePath();c.strokeStyle=pass?"rgba(109,241,255,.18)":"rgba(167,139,250,.12)";c.lineWidth=pass?1:3;c.stroke()}if(done>1){c.beginPath();for(let i=0;i<Math.min(done,projected.length);i++){const p=projected[i];i?c.lineTo(p.x,p.y):c.moveTo(p.x,p.y)}c.strokeStyle="#6df1ff";c.lineWidth=1.35;c.stroke()}}
  const draw=projected.slice(0,done).sort((a,b)=>a.z-b.z);for(const p of draw){const front=p.i===Math.max(0,done-1);c.beginPath();c.arc(p.x,p.y,front?4:1.8,0,Math.PI*2);c.fillStyle=front?"#fff":"#6df1ff";c.fill();if(front){c.shadowBlur=14;c.shadowColor="#fff";c.fill();c.shadowBlur=0}}
  c.beginPath();c.arc(cx,cy,3.5,0,Math.PI*2);c.fillStyle="#f5bf4f";c.shadowBlur=12;c.shadowColor="#f5bf4f";c.fill();c.shadowBlur=0;requestAnimationFrame(drawSphere);
}
const sphereCanvas=$("opticSphere");
sphereCanvas.addEventListener("pointerdown",e=>{sphere.drag={x:e.clientX,y:e.clientY};sphereCanvas.setPointerCapture(e.pointerId)});
sphereCanvas.addEventListener("pointermove",e=>{if(!sphere.drag)return;sphere.yaw+=(e.clientX-sphere.drag.x)*.008;sphere.pitch=clamp(sphere.pitch+(e.clientY-sphere.drag.y)*.008,-1.35,1.35);sphere.drag={x:e.clientX,y:e.clientY}});
sphereCanvas.addEventListener("pointerup",()=>sphere.drag=null);sphereCanvas.addEventListener("pointercancel",()=>sphere.drag=null);
sphereCanvas.addEventListener("wheel",e=>{e.preventDefault();sphere.zoom=clamp(sphere.zoom*Math.exp(-e.deltaY*.001),.65,1.8)},{passive:false});
refreshSphere();setInterval(refreshSphere,5000);requestAnimationFrame(drawSphere);
compile();
