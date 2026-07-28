const { chromium } = require('playwright');
(async()=>{
  const b=await chromium.launch();const p=await b.newPage({viewport:{width:1500,height:1000}});
  await p.goto((process.env.BASE_URL||'https://flux.influx.vision')+'/motion-atlas/registry.html',{waitUntil:'domcontentloaded',timeout:20000});
  await p.waitForTimeout(4500);
  console.log(JSON.stringify(await p.evaluate(()=>{
    const card=document.querySelector('.setCard');
    const strip=card&&card.querySelector('.setStrip');
    const img=strip&&strip.querySelector('img');
    const cs=e=>e?getComputedStyle(e):null;
    const pick=(e,ks)=>{const s=cs(e);return s?Object.fromEntries(ks.map(k=>[k,s[k]])):null};
    return {
      cardHTML:card?card.outerHTML.slice(0,260):null,
      stripTag:strip?strip.tagName:null,
      stripStyle:pick(strip,['display','gridTemplateColumns','gap','width','position']),
      imgStyle:pick(img,['display','position','width','height','aspectRatio','objectFit','gridColumn','gridArea']),
      imgParentIsStrip:img?img.parentElement.className:null,
      stripChildCount:strip?strip.childElementCount:null,
    };
  }),null,2));
  await b.close();
})();
