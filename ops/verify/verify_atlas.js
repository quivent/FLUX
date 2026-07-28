const { chromium } = require('playwright');

const URL = process.env.ATLAS_URL || 'http://127.0.0.1:7861/motion-atlas/';
const WATCH_MS = Number(process.env.WATCH_MS || 15000);

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();

  const consoleErrors = [];
  const pageErrors = [];
  const failedRequests = [];

  page.on('console', m => {
    if (m.type() === 'error') consoleErrors.push(m.text());
  });
  page.on('pageerror', e => pageErrors.push(e.message));
  page.on('requestfailed', r => failedRequests.push(`${r.url()} :: ${r.failure()?.errorText}`));
  page.on('response', r => {
    if (r.status() >= 400) failedRequests.push(`${r.url()} :: HTTP ${r.status()}`);
  });

  await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 20000 });

  // Sample state repeatedly so we can see whether things actually move.
  const samples = [];
  const started = Date.now();
  let firstRealProgressMs = null;
  let firstFrameMs = null;
  while (Date.now() - started < WATCH_MS) {
    const snap = await page.evaluate(() => {
      const st = (typeof state !== 'undefined') ? state : null;
      const stage = document.getElementById('assetStage');
      const a = document.getElementById('assetFrameA');
      const b = document.getElementById('assetFrameB');
      const bar = document.getElementById('progressBar');
      const sigil = document.querySelector('.atlasSigil');
      return {
        t: Date.now(),
        frames: st ? st.frames.length : null,
        prefilled: st ? !!st.prefilled : null,
        activeJob: st ? st.activeJob : null,
        accepted: st ? Array.from(st.acceptedAssetJobs || []).length : null,
        pending: st ? (st.pendingAssets ? st.pendingAssets.size : null) : null,
        slideshow: st ? !!st.slideshowTimer : null,
        playIndex: st ? (st.playIndex ?? null) : null,
        progressObj: st && st.progress ? { done: Math.round(st.progress.done), total: st.progress.total, rate: +st.progress.rate.toFixed(2) } : null,
        stageDisplay: stage ? getComputedStyle(stage).display : null,
        aSrc: a ? (a.currentSrc || a.src || '').split('/').pop() : null,
        aVisible: a ? getComputedStyle(a).opacity : null,
        bSrc: b ? (b.currentSrc || b.src || '').split('/').pop() : null,
        bVisible: b ? getComputedStyle(b).opacity : null,
        filmstripImgs: document.querySelectorAll('#filmstrip img').length,
        progressWidth: bar ? bar.style.width : null,
        etaWidth: (document.getElementById('etaBar') || {}).style?.width ?? null,
        etaClock: (document.getElementById('etaClock') || {}).textContent,
        fuelWidth: (document.getElementById('fuelBar') || {}).style?.width ?? null,
        fuelLabel: (document.getElementById('fuelLabel') || {}).textContent,
        fuelDetail: (document.getElementById('fuelDetail') || {}).textContent,
        fuelClass: (document.getElementById('fuelTank') || {}).className,
        statRunning: (document.getElementById('statRunning') || {}).textContent,
        statPending: (document.getElementById('statPending') || {}).textContent,
        statFailed: (document.getElementById('statFailed') || {}).textContent,
        progressText: (document.getElementById('progressText') || {}).textContent,
        sigilActive: sigil ? sigil.classList.contains('active') : null,
        sessions: document.querySelectorAll('#sessions [data-job]').length,
      };
    });
    samples.push(snap);
    if (firstRealProgressMs === null && snap.progressObj) firstRealProgressMs = Date.now() - started;
    if (firstFrameMs === null && snap.frames > 0) firstFrameMs = Date.now() - started;
    await page.waitForTimeout(samples.length < 12 ? 250 : 1500);
  }

  const first = samples[0], last = samples[samples.length - 1];

  const distinctStageSrc = new Set(samples.map(s => s.aSrc).concat(samples.map(s => s.bSrc)).filter(Boolean));
  const distinctWidths = new Set(samples.map(s => s.progressWidth).filter(Boolean));

  console.log('\n================ ATLAS VERIFICATION ================');
  console.log('URL:', URL);
  console.log('\n--- ERRORS ---');
  console.log('pageErrors:', pageErrors.length ? pageErrors : 'none');
  console.log('consoleErrors:', consoleErrors.length ? consoleErrors.slice(0, 10) : 'none');
  console.log('failedRequests:', failedRequests.length ? failedRequests.slice(0, 10) : 'none');

  console.log('\n--- FIRST SAMPLE ---');
  console.log(JSON.stringify(first, null, 2));
  console.log('\n--- LAST SAMPLE ---');
  console.log(JSON.stringify(last, null, 2));

  console.log('\n--- VERDICTS ---');
  console.log('IMAGES DISPLAYED   :', (last.stageDisplay === 'block' && (parseFloat(last.aVisible) > 0.05 || parseFloat(last.bVisible) > 0.05) && distinctStageSrc.size > 0) ? 'PASS' : 'FAIL');
  console.log('SLIDESHOW CYCLING  :', distinctStageSrc.size > 1 ? `PASS (${distinctStageSrc.size} distinct frames shown)` : `FAIL (${distinctStageSrc.size} distinct)`);
  console.log('PROGRESS ADVANCING :', distinctWidths.size > 1 ? `PASS (${distinctWidths.size} distinct widths)` : `FAIL (${distinctWidths.size} distinct: ${[...distinctWidths]})`);
  console.log('SIGIL FLICKER ON   :', last.sigilActive ? 'PASS' : 'FAIL');
  const barPct = parseFloat(last.progressWidth) || 0;
  const m = /^([\d,]+)\s*\/\s*([\d,]+)/.exec(last.progressText || '');
  const textPct = m ? (Number(m[1].replace(/,/g, '')) / Number(m[2].replace(/,/g, '')) * 100) : NaN;
  console.log('BAR/TEXT AGREE     :', Number.isFinite(textPct) && Math.abs(barPct - textPct) < 1.5
    ? `PASS (bar ${barPct.toFixed(2)}% vs text ${textPct.toFixed(2)}%)`
    : `FAIL (bar ${barPct.toFixed(2)}% vs text ${Number.isFinite(textPct) ? textPct.toFixed(2) + '%' : last.progressText})`);
  console.log('FRAMES INGESTED    :', last.frames > 0 ? `PASS (${last.frames})` : 'FAIL (0)');
  console.log('PREFILL CLEARED    :', last.prefilled === false ? 'PASS' : `note: prefilled=${last.prefilled}`);
  console.log('SESSIONS LISTED    :', last.sessions);
  const fakeZero = samples.filter(s => !s.progressObj && /^0\s*\//.test(s.progressText || '')).length;
  console.log('TIME TO PROGRESS   :', firstRealProgressMs === null ? 'FAIL (never)' : `${firstRealProgressMs}ms`);
  console.log('TIME TO FIRST FRAME:', firstFrameMs === null ? 'FAIL (never)' : `${firstFrameMs}ms`);
  console.log('NO FAKE ZERO       :', fakeZero === 0 ? 'PASS' : `FAIL (${fakeZero} samples showed a 0/N label with no real job data)`);
  const jobIds = new Set(samples.map(s => s.activeJob).filter(Boolean));
  console.log('JOB STABLE         :', jobIds.size <= 1 ? `PASS (${[...jobIds]})` : `FAIL (switched between ${[...jobIds].join(', ')})`);
  const etaWidths = new Set(samples.map(s => s.etaWidth).filter(w => w && w !== '0%'));
  console.log('ETA BAR ADVANCING  :', etaWidths.size > 1 ? `PASS (${etaWidths.size} distinct, now ${last.etaWidth}, ${last.etaClock})` : `FAIL (${etaWidths.size} distinct: ${[...etaWidths]})`);
  const fuelOk = last.fuelWidth && last.fuelWidth !== '0%' && last.fuelLabel && last.fuelLabel !== '—';
  console.log('QUEUE FUEL GAUGE   :', fuelOk ? `PASS (${last.fuelLabel} | ${last.fuelDetail})` : `FAIL (width=${last.fuelWidth} label=${last.fuelLabel})`);
  console.log('FUEL STATE         :', last.fuelClass || '(none)');
  const statsOk = last.statRunning !== '—' && last.statPending !== '—' && last.statFailed !== '—';
  console.log('QUEUE STATS        :', statsOk ? `PASS (running ${last.statRunning}, pending ${last.statPending}, failed ${last.statFailed})` : `FAIL (running=${last.statRunning} pending=${last.statPending} failed=${last.statFailed})`);
  console.log('====================================================\n');

  await browser.close();
})();
