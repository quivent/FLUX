const { chromium } = require('playwright');

const BASE = process.env.BASE_URL || 'https://flux.influx.vision';
const PAGES = [
  ['atlas', '/motion-atlas/'],
  ['optics', '/motion-atlas/optics.html'],
  ['queue', '/motion-atlas/queue.html'],
  ['gallery', '/motion-atlas/registry.html'],
  ['governor', '/motion-atlas/governor.html'],
  ['visionary', '/motion-atlas/visionary.html'],
];

(async () => {
  const browser = await chromium.launch();
  let anyFail = false;

  for (const [name, path] of PAGES) {
    const page = await browser.newPage();
    const consoleErrors = [];
    const pageErrors = [];
    const badResponses = [];

    page.on('console', m => { if (m.type() === 'error') consoleErrors.push(m.text()); });
    page.on('pageerror', e => pageErrors.push(e.message));
    page.on('requestfailed', r => {
      const u = r.url();
      // Google Fonts is unreachable here and is not part of what we ship.
      if (!/fonts\.(googleapis|gstatic)\.com/.test(u)) badResponses.push(`${u} :: ${r.failure()?.errorText}`);
    });
    page.on('response', r => {
      if (r.status() >= 400 && !/fonts\.(googleapis|gstatic)\.com/.test(r.url())) {
        badResponses.push(`${r.url()} :: HTTP ${r.status()}`);
      }
    });

    let navError = null;
    try {
      await page.goto(BASE + path, { waitUntil: 'domcontentloaded', timeout: 20000 });
    } catch (e) {
      navError = e.message.split('\n')[0];
    }
    await page.waitForTimeout(4000);

    const dom = await page.evaluate(() => ({
      title: document.title,
      headers: document.querySelectorAll('header.atlasTopbar').length,
      activeTabs: Array.from(document.querySelectorAll('.atlasTabs a.active')).map(a => a.textContent.trim()),
      tabCount: document.querySelectorAll('.atlasTabs a').length,
      sigils: document.querySelectorAll('.atlasSigil').length,
      bodyClass: document.body.className,
      appChildren: (document.getElementById('app') || {}).childElementCount ?? null,
      buildStamp: (document.documentElement.innerHTML.match(/build:(\d+)/) || [])[1] || null,
      visibleText: (document.body.innerText || '').trim().length,
    }));

    const fail = navError || pageErrors.length || badResponses.length || dom.headers !== 1 || dom.visibleText < 50;
    if (fail) anyFail = true;

    console.log(`\n=== ${name.toUpperCase()} ${path} ===`);
    console.log(`  status       : ${fail ? 'FAIL' : 'PASS'}`);
    if (navError) console.log(`  navError     : ${navError}`);
    console.log(`  title        : ${dom.title}`);
    console.log(`  headers      : ${dom.headers} (expect 1)`);
    console.log(`  tabs         : ${dom.tabCount} (expect 6), active=${JSON.stringify(dom.activeTabs)}`);
    console.log(`  sigils       : ${dom.sigils} (expect 1)`);
    console.log(`  build stamp  : ${dom.buildStamp}`);
    console.log(`  body class   : ${dom.bodyClass || '(none)'}`);
    console.log(`  #app children: ${dom.appChildren}`);
    console.log(`  visible chars: ${dom.visibleText}`);
    if (pageErrors.length) console.log(`  pageErrors   : ${pageErrors.slice(0, 5).join(' | ')}`);
    if (consoleErrors.length) console.log(`  consoleErrors: ${consoleErrors.slice(0, 5).join(' | ')}`);
    if (badResponses.length) console.log(`  badResponses : ${badResponses.slice(0, 5).join(' | ')}`);

    await page.close();
  }

  console.log(`\n==================== SUITE: ${anyFail ? 'FAIL' : 'PASS'} ====================\n`);
  await browser.close();
  process.exit(anyFail ? 1 : 0);
})();
