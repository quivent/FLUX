const { chromium } = require('playwright');
(async()=>{
  const b=await chromium.launch();const p=await b.newPage();
  await p.goto('https://flux.influx.vision/motion-atlas/',{waitUntil:'domcontentloaded',timeout:20000});
  await p.waitForTimeout(10000);
  // Sample what the stage shows vs what the newest ingested frame is.
  const rows=[];
  for(let i=0;i<14;i++){
    rows.push(await p.evaluate(()=>{
      const a=document.getElementById('assetFrameA'),bb=document.getElementById('assetFrameB');
      const op=e=>parseFloat(getComputedStyle(e).opacity);
      const shownEl=op(a)>=op(bb)?a:bb;
      const cell=s=>{const m=/cell_(\d+)/.exec(s||'');return m?Number(m[1]):null};
      const dir=s=>{try{return new URL(s,location.href).pathname.split('/').slice(-2)[0]}catch{return null}};
      return {
        stage:cell(shownEl.currentSrc||shownEl.src),
        stageDir:dir(shownEl.currentSrc||shownEl.src),
        newest:cell(state.frames[state.frames.length-1]),
        newestDir:dir(state.frames[state.frames.length-1]),
        total:state.frames.length,
        playIndex:state.playIndex,
        job:state.shownJob,
      };
    }));
    await p.waitForTimeout(700);
  }
  console.log('stage_cell  newest_cell  playIdx  frames  job');
  for(const r of rows) console.log(String(r.stage).padEnd(11),String(r.newest).padEnd(12),String(r.playIndex).padEnd(8),String(r.total).padEnd(7),r.job);
  const dirs=new Set(rows.map(r=>r.stageDir));
  const lag=rows.map(r=>(r.newest!=null&&r.stage!=null)?r.newest-r.stage:null).filter(x=>x!=null);
  console.log('\nstage source dirs :',[...dirs]);
  console.log('newest-vs-stage gap (cells):',JSON.stringify(lag));
  console.log('IS STAGE LIVE     :',lag.every(x=>Math.abs(x)<=2)?'yes, tracking newest':'NO — cycling older buffered frames');
  await b.close();
})();
