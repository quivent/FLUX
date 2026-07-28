const { chromium } = require('playwright');
(async()=>{
  const b=await chromium.launch();const p=await b.newPage({viewport:{width:1600,height:1200}});
  await p.goto('https://flux.influx.vision/motion-atlas/registry.html',{waitUntil:'domcontentloaded',timeout:20000});
  await p.waitForTimeout(5000);
  const check=async()=>p.evaluate(()=>[...document.querySelectorAll('.setCard')].map(card=>{
    const name=(card.querySelector('strong')||{}).textContent;
    const target=card.dataset.open;
    const dirs=[...card.querySelectorAll('.setStrip img')].map(i=>{
      try{return new URL(i.getAttribute('src'),location.href).pathname.split('/').slice(0,-1).join('/')}catch{return null}
    });
    const files=[...card.querySelectorAll('.setStrip img')].map(i=>(i.getAttribute('src')||'').split('/').pop());
    return {name,target,uniqueDirs:[...new Set(dirs)],files};
  }));
  const a=await check();
  await p.waitForTimeout(2000);
  const c=await check();
  let bad=0, mismatch=0;
  a.forEach((row,i)=>{
    const own='/outputs/'+row.target;
    const clean=row.uniqueDirs.length===1;
    const belongs=row.uniqueDirs[0]===own;
    if(!clean)bad++;
    if(!belongs)mismatch++;
    const moved=JSON.stringify(row.files)!==JSON.stringify(c[i].files);
    console.log(`${clean&&belongs?'OK  ':'BAD '} ${String(row.name).slice(0,28).padEnd(29)} dirs=${row.uniqueDirs.length} cycling=${moved?'yes':'NO'}`);
    if(!clean||!belongs)console.log('      expected',own,'got',row.uniqueDirs);
  });
  console.log(`\ncards=${a.length}  mixed-source cards=${bad}  wrong-collection cards=${mismatch}`);
  console.log('ALL TILES OWN SET :',bad===0&&mismatch===0?'PASS':'FAIL');
  await b.close();
})();
