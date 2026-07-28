const { chromium } = require('playwright');
const URL = process.env.ATLAS_URL || 'https://flux.influx.vision/motion-atlas/';
(async () => {
  const b = await chromium.launch();
  const p = await b.newPage();
  p.on('console', m => console.log('[page]', m.text()));
  await p.addInitScript(() => {
    // Trap writes to state.activeJob and report who did it.
    window.__trap = () => {
      if (typeof state === 'undefined' || state.__trapped) return false;
      let v = state.activeJob;
      Object.defineProperty(state, 'activeJob', {
        get() { return v; },
        set(next) {
          if (next !== v) {
            const stack = (new Error().stack || '').split('\n').slice(2, 5).join(' <- ');
            console.log(`ACTIVEJOB ${v} -> ${next} :: ${stack}`);
          }
          v = next;
        },
        configurable: true,
      });
      state.__trapped = true;
      return true;
    };
  });
  await p.goto(URL, { waitUntil: 'domcontentloaded', timeout: 20000 });
  // Install the trap as soon as state exists.
  for (let i = 0; i < 40; i++) {
    const ok = await p.evaluate(() => window.__trap());
    if (ok) break;
    await p.waitForTimeout(25);
  }
  await p.waitForTimeout(12000);
  await b.close();
})();
