const { chromium } = require('playwright');
const B=process.env.BASE_URL||'https://flux.influx.vision';
(async()=>{
  const b=await chromium.launch();const p=await b.newPage({viewport:{width:1500,height:1000}});
  const errs=[];p.on('pageerror',e=>errs.push(e.message));
  p.on('console',m=>{if(m.type()==='error')errs.push(m.text())});
  await p.goto(B+'/motion-atlas/registry.html',{waitUntil:'domcontentloaded',timeout:20000});
  await p.waitForTimeout(4500);
  const before=await p.evaluate(()=>({cards:document.querySelectorAll('.setCard').length,url:location.search}));
  // click the second collection (an atlas one)
  await p.evaluate(()=>{const b=document.querySelectorAll('[data-open]')[1];b&&b.click()});
  await p.waitForTimeout(3000);
  const opened=await p.evaluate(()=>({
    url:location.search,
    barName:(document.querySelector('.openBar strong')||{}).textContent,
    barCount:(document.querySelector('.openBar span')||{}).textContent,
    images:document.querySelectorAll('[data-asset]').length,
    empty:!!document.querySelector('.detailEmpty'),
    hasBack:!!document.getElementById('backToSets'),
  }));
  await p.goBack();
  await p.waitForTimeout(2500);
  const back=await p.evaluate(()=>({url:location.search,cards:document.querySelectorAll('.setCard').length}));
  console.log('initial      :',JSON.stringify(before));
  console.log('opened       :',JSON.stringify(opened));
  console.log('after goBack :',JSON.stringify(back));
  console.log('OPEN SHOWS ASSETS :',opened.images>0&&!opened.empty?`PASS (${opened.images} shown, ${opened.barCount})`:'FAIL');
  console.log('URL SLUG          :',opened.url.includes('set=')?`PASS (${opened.url})`:'FAIL');
  console.log('BACK BUTTON       :',opened.hasBack?'PASS':'FAIL');
  console.log('BROWSER BACK      :',back.cards>0&&!back.url.includes('set=')?`PASS (${back.cards} cards)`:'FAIL');
  console.log('errors            :',errs.length?errs.slice(0,3):'none');
  await b.close();
})();
