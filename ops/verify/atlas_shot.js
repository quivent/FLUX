const { chromium } = require('playwright');
(async()=>{
  const b=await chromium.launch();
  const p=await b.newPage({viewport:{width:1600,height:1000}});
  const errs=[];p.on('pageerror',e=>errs.push(e.message));
  await p.goto('https://flux.influx.vision/motion-atlas/',{waitUntil:'domcontentloaded',timeout:20000});
  await p.waitForTimeout(12000);
  await p.screenshot({path:'/tmp/atlas.png'});
  const r=await p.evaluate(()=>{
    const stage=document.getElementById('assetStage');
    const a=document.getElementById('assetFrameA'), bb=document.getElementById('assetFrameB');
    const vis=e=>e?getComputedStyle(e).opacity:null;
    return {
      activeTab:(document.querySelector('.atlasTabs a.active')||{}).textContent,
      stageDisplay:stage?getComputedStyle(stage).display:null,
      aOpacity:vis(a), bOpacity:vis(bb),
      aSrc:(a&&a.currentSrc||'').split('/').slice(-2).join('/'),
      stageMsgVisible:(document.getElementById('stageMessage')||{}).style?.display,
      filmstrip:document.querySelectorAll('#filmstrip img').length,
      frames:typeof state!=='undefined'?state.frames.length:null,
      shown:typeof state!=='undefined'?state.shownJob:null,
      title:(document.getElementById('stageTitle')||{}).textContent,
      progress:(document.getElementById('progressText')||{}).textContent,
      bar:(document.getElementById('progressBar')||{}).style?.width,
      fuel:(document.getElementById('fuelLabel')||{}).textContent,
      running:(document.getElementById('statRunning')||{}).textContent,
      pending:(document.getElementById('statPending')||{}).textContent,
      etaClock:(document.getElementById('etaClock')||{}).textContent,
    };
  });
  for(const [k,v] of Object.entries(r)) console.log(String(k).padEnd(16),':',v);
  console.log('errors'.padEnd(16),':',errs.length?errs.slice(0,3):'none');
  await b.close();
})();
