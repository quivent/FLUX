const { chromium } = require('playwright');
const URL = process.env.ATLAS_URL || 'https://flux.influx.vision/motion-atlas/';
(async () => {
  const b = await chromium.launch();
  const p = await b.newPage();
  await p.goto(URL, { waitUntil: 'domcontentloaded', timeout: 20000 });
  const seq = [];
  for (let i = 0; i < 40; i++) {
    const s = await p.evaluate(() => ({
      t: Date.now(),
      activeJob: typeof state !== 'undefined' ? state.activeJob : null,
      shownJob: typeof state !== 'undefined' ? state.shownJob : null,
      pinned: typeof state !== 'undefined' ? state.pinnedJob : null,
      previewSize: typeof state !== 'undefined' ? state.preview.size : null,
      discovery: typeof state !== 'undefined' ? { started: state.discovery.started, jobs: state.discovery.jobs.size } : null,
    }));
    seq.push(s);
    await p.waitForTimeout(400);
  }
  const t0 = seq[0].t;
  let prev = null;
  for (const s of seq) {
    const key = `${s.activeJob}|${s.shownJob}|${s.pinned}|${s.previewSize}|${JSON.stringify(s.discovery)}`;
    if (key !== prev) {
      console.log(`+${String(s.t - t0).padStart(6)}ms active=${s.activeJob} shown=${s.shownJob} pinned=${s.pinned} previewSize=${s.previewSize} discovery=${JSON.stringify(s.discovery)}`);
      prev = key;
    }
  }
  await b.close();
})();
