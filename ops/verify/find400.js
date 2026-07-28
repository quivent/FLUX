const { chromium } = require('playwright');
(async()=>{
  const b=await chromium.launch();const p=await b.newPage();
  const bad=[];
  p.on('response',r=>{if(r.status()>=400)bad.push(`${r.status()} ${r.url()}`)});
  await p.goto((process.env.BASE_URL||'https://flux.influx.vision')+'/motion-atlas/registry.html',{waitUntil:'domcontentloaded',timeout:20000});
  await p.waitForTimeout(4000);
  await p.evaluate(()=>{const b=document.querySelectorAll('[data-open]')[1];b&&b.click()});
  await p.waitForTimeout(3000);
  console.log([...new Set(bad)].slice(0,8).join('\n')||'none');
  await b.close();
})();
