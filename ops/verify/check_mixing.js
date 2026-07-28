const { chromium } = require('playwright');
(async()=>{
  const b=await chromium.launch();const p=await b.newPage();
  await p.goto((process.env.BASE_URL||'https://flux.influx.vision')+'/motion-atlas/',{waitUntil:'domcontentloaded',timeout:20000});
  await p.waitForTimeout(14000);
  const r=await p.evaluate(()=>{
    const dirOf=u=>{try{return new URL(u,location.href).pathname.split('/').slice(0,-1).join('/')}catch{return u}};
    const dirs=[...new Set(state.frames.map(dirOf))];
    return {shown:state.shownJob,pinned:state.pinnedJob,frames:state.frames.length,dirs:dirs,
      title:(document.getElementById('stageTitle')||{}).textContent,
      meta:(document.getElementById('stageJobMeta')||{}).textContent,
      prompt:((document.getElementById('stagePrompt')||{}).textContent||'').slice(0,70)};
  });
  console.log('shown job    :',r.shown);
  console.log('frames       :',r.frames);
  console.log('source dirs  :',JSON.stringify(r.dirs));
  console.log('stage title  :',r.title);
  console.log('stage meta   :',r.meta);
  console.log('stage prompt :',r.prompt);
  const single=r.dirs.length<=1;
  console.log('NO MIXING    :',single?'PASS (all frames from one collection)':`FAIL (${r.dirs.length} different sources)`);
  console.log('TITLE SHOWN  :',r.title&&r.title!=='Motion continuity, mapped.'?'PASS':'FAIL');
  await b.close();
})();
