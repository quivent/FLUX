const { chromium } = require('playwright');
(async()=>{
  const b=await chromium.launch();const p=await b.newPage({viewport:{width:1600,height:900}});
  await p.goto('https://flux.influx.vision/motion-atlas/registry.html',{waitUntil:'domcontentloaded',timeout:20000});
  await p.waitForTimeout(5000);
  const files=n=>p.evaluate(i=>[...document.querySelectorAll('.setCard')[i].querySelectorAll('.setStrip img')].map(x=>(x.getAttribute('src')||'').split('/').pop()),n);
  const idx=8;
  const before=await files(idx);
  await p.evaluate(i=>document.querySelectorAll('.setCard')[i].scrollIntoView({block:'center'}),idx);
  await p.waitForTimeout(2500);
  const after=await files(idx);
  console.log('card',idx,'before scroll:',JSON.stringify(before.slice(0,2)));
  console.log('card',idx,'after  scroll:',JSON.stringify(after.slice(0,2)));
  console.log('CYCLES WHEN VISIBLE :',JSON.stringify(before)!==JSON.stringify(after)?'PASS':'FAIL');
  await b.close();
})();
