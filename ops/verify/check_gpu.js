const { chromium } = require('playwright');
(async()=>{
  const b=await chromium.launch();const p=await b.newPage();
  const errs=[];p.on('pageerror',e=>errs.push(e.message));
  await p.goto((process.env.BASE_URL||'https://flux.influx.vision')+'/motion-atlas/',{waitUntil:'domcontentloaded',timeout:20000});
  await p.waitForTimeout(9000);
  const t=id=>`(document.getElementById('${id}')||{}).textContent`;
  const r=await p.evaluate(()=>({
    gpuName:(document.getElementById('gpuName')||{}).textContent,
    vram:(document.getElementById('vramValue')||{}).textContent,
    vramBar:(document.getElementById('vramBar')||{}).style?.width,
    compute:(document.getElementById('computeValue')||{}).textContent,
    computeBar:(document.getElementById('computeBar')||{}).style?.width,
    thermal:(document.getElementById('thermalValue')||{}).textContent,
    power:(document.getElementById('powerValue')||{}).textContent,
    procs:(document.getElementById('gpuProcesses')||{}).textContent,
    workerState:(document.getElementById('workerState')||{}).textContent,
    device:(document.getElementById('device')||{}).textContent,
    phase:(document.getElementById('experimentPhase')||{}).textContent,
    rate:(document.getElementById('rate')||{}).textContent,
  }));
  for(const [k,v] of Object.entries(r)) console.log(String(k).padEnd(12),':',v);
  console.log('errors      :',errs.length?errs.slice(0,3):'none');
  await b.close();
})();
