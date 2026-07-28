const { chromium } = require('playwright');
const URL = (process.env.BASE_URL||'https://flux.influx.vision')+'/motion-atlas/registry.html';
(async()=>{
  const b=await chromium.launch();const p=await b.newPage();
  const errs=[];p.on('pageerror',e=>errs.push(e.message));
  p.on('console',m=>{if(m.type()==='error')errs.push(m.text())});
  await p.goto(URL,{waitUntil:'domcontentloaded',timeout:20000});
  await p.waitForTimeout(4000);
  const snap=async()=>p.evaluate(()=>({
    cards:document.querySelectorAll('.setCard').length,
    tilesPerCard:document.querySelector('.setStrip')?document.querySelector('.setStrip').querySelectorAll('img').length:0,
    titles:Array.from(document.querySelectorAll('.setCard strong')).slice(0,3).map(x=>x.textContent),
    metas:Array.from(document.querySelectorAll('.setCard small')).slice(0,2).map(x=>x.textContent),
    pager:(document.getElementById('setPager')||{}).textContent,
    pagerHidden:(document.getElementById('setPager')||{}).hidden,
    srcs:Array.from(document.querySelectorAll('.setStrip img')).slice(0,5).map(i=>i.getAttribute('src')?.split('/').pop()),
    cols:document.querySelector('.setWall')?getComputedStyle(document.querySelector('.setWall')).gridTemplateColumns:null,
    stripCols:document.querySelector('.setStrip')?getComputedStyle(document.querySelector('.setStrip')).gridTemplateColumns:null,
    gap:document.querySelector('.setStrip')?getComputedStyle(document.querySelector('.setStrip')).gap:null,
  }));
  const a=await snap();
  await p.waitForTimeout(2500);
  const c=await snap();
  console.log('cards on page   :',a.cards,'(expect 18, cap 25)');
  console.log('tiles per card  :',a.tilesPerCard,'(expect 5)');
  console.log('strip columns   :',a.stripCols);
  console.log('strip gap       :',a.gap,'(expect 0px)');
  console.log('wall columns    :',a.cols);
  console.log('titles          :',JSON.stringify(a.titles));
  console.log('meta            :',JSON.stringify(a.metas));
  console.log('pager           :',a.pagerHidden?'(hidden, single page)':a.pager);
  const moved=JSON.stringify(a.srcs)!==JSON.stringify(c.srcs);
  console.log('cycling         :',moved?'PASS (frames advanced)':'FAIL (static)');
  console.log('  before        :',JSON.stringify(a.srcs));
  console.log('  after         :',JSON.stringify(c.srcs));
  console.log('errors          :',errs.length?errs.slice(0,3):'none');
  await b.close();
})();
