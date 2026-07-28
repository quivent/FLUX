const { chromium } = require('playwright');
(async()=>{
  const b=await chromium.launch();
  const p=await b.newPage({viewport:{width:1500,height:1000}});
  await p.goto((process.env.BASE_URL||'https://flux.influx.vision')+'/motion-atlas/registry.html',{waitUntil:'domcontentloaded',timeout:20000});
  await p.waitForTimeout(5000);
  await p.screenshot({path:'/tmp/gallery.png'});
  const box=await p.evaluate(()=>{
    const card=document.querySelector('.setCard');
    const imgs=card?[...card.querySelectorAll('.setStrip img')]:[];
    return {
      cardRect:card?card.getBoundingClientRect().toJSON():null,
      imgRects:imgs.map(i=>{const r=i.getBoundingClientRect();return {w:Math.round(r.width),h:Math.round(r.height),x:Math.round(r.x)}}),
      naturalSizes:imgs.map(i=>`${i.naturalWidth}x${i.naturalHeight}`),
    };
  });
  console.log(JSON.stringify(box,null,2));
  await b.close();
})();
