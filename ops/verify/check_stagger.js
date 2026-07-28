const { chromium } = require('playwright');
(async()=>{
  const b=await chromium.launch();const p=await b.newPage();
  await p.goto('https://flux.influx.vision/motion-atlas/',{waitUntil:'domcontentloaded',timeout:20000});
  await p.waitForTimeout(8000);
  const seen=[];const t0=Date.now();
  let last=null;
  while(Date.now()-t0<40000){
    const r=await p.evaluate(()=>{
      const a=document.getElementById('assetFrameA'),bb=document.getElementById('assetFrameB');
      const op=e=>parseFloat(getComputedStyle(e).opacity);
      const el=op(a)>=op(bb)?a:bb;
      const m=/cell_(\d+)/.exec(el.currentSrc||el.src||'');
      return {cell:m?Number(m[1]):null,queue:(state.playQueue||[]).length,pace:typeof paceMs==='function'?Math.round(paceMs()):null};
    });
    if(r.cell!==last){seen.push({t:((Date.now()-t0)/1000).toFixed(1),...r});last=r.cell}
    await p.waitForTimeout(150);
  }
  console.log('t(s)   cell    queue  paceMs');
  seen.slice(0,26).forEach(s=>console.log(String(s.t).padEnd(7),String(s.cell).padEnd(8),String(s.queue).padEnd(6),s.pace));
  const gaps=[];for(let i=1;i<seen.length;i++)gaps.push(+(seen[i].t-seen[i-1].t).toFixed(1));
  console.log('\ndistinct frames shown in 40s :',seen.length);
  console.log('inter-frame gaps (s)         :',JSON.stringify(gaps.slice(0,20)));
  const maxGap=Math.max(...gaps,0);
  console.log('largest freeze (s)           :',maxGap);
  console.log('CONTINUOUS PLAYBACK          :',seen.length>=20&&maxGap<4?'PASS':`FAIL (frames=${seen.length}, maxGap=${maxGap}s)`);
  await b.close();
})();
