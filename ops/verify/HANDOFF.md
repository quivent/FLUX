# Motion Atlas — Handoff

Read this before touching the atlas UI. Written 2026-07-28.

## Read this first: how to not waste the user's time

There is a verification harness at `ops/verify`. **Use it.** Do not tell the
user something works because you read the code and it looked right. That
happened repeatedly in this session and every single time the user had to come
back and say it was still broken.

```zsh
cd ops/verify && npm install && npx playwright install chromium
node verify_suite.js                                   # all 6 pages, errors, headers
node verify_atlas.js                                   # atlas behaviour, timings
ATLAS_URL=https://flux.influx.vision/motion-atlas/ node verify_atlas.js
```

Test against **`https://flux.influx.vision`**, not localhost. Two real bugs only
appeared behind the proxy (see Thumbnails below).

Take screenshots and actually look at them. `getComputedStyle` lies: it reported
`grid-template-columns: repeat(5, 1fr)` on an element whose `display` had
resolved to `block`, so the value was inert and all five images were stacked at
the same x-position. Only `getBoundingClientRect` revealed it.

When something is set but not taking effect, instrument instead of reasoning.
Trapping writes to `state.activeJob` with `Object.defineProperty` and logging
the stack found the auto-dispatch bug in one shot after a long stretch of
guessing. `ops/verify/trap_job.js` is that trap, keep it.

## Architecture

- Go HTTP server, `internal/server/`, serves static files from `web/motion-atlas/`
  behind an allowlist in `motionAtlas()`. **New files must be added to that map
  or they 404.**
- Pages are Jinja2 templates in `web/motion-atlas/templates/`, rendered to static
  HTML by `render_templates.py`. Build-time only, no runtime Python.
  **Edit the template, never the rendered HTML — it gets overwritten.**
- After any template/CSS/JS edit: `python3 render_templates.py`. Every asset URL
  is stamped with a fresh unix timestamp and a `<!-- build:N -->` comment lands
  in `<head>`, so stale cache is never the explanation. Check the stamp.
- Restarting the Go server does not touch the FLUX worker. The worker holds the
  model in VRAM and talks over `.fluxd/flux.sock`. It survived every restart
  this session.

## Landmines

**Silent catch blocks.** The jobs SSE handler wraps everything in bare
`try{}catch{}`. Two separate bugs hid there: `updatePreview`/`updateDiscovery`
throwing would silently skip `acceptAssetJob` and `showJobProgress`, so images
and progress both died with no console output. They are individually isolated
now. If something in `renderJobFeed` mysteriously does nothing, this is why.

**Do not auto-dispatch renders.** `renderModel` used to call `renderSeedBatch()`
when the model loaded with `pendingPreview` set, and initial state had
`pendingPreview: !resumedJob`. Every fresh load, new tab, or hard refresh
submitted a 32-image GPU job nobody asked for. That produced the whole
`seed-ramp-*` backlog and the errored jobs. Removed. Generating must be a click.

**Job selection.** `renderJobFeed` recomputes the shown job on every SSE push.
Precedence: explicit pin > sticky (currently shown and still running) > newest
running atlas > newest running preview > fallbacks. Earlier it treated queued
and running as equal and took the newest by creation, so the newer queued
backlog always beat the job actually executing — a queued job has no `started`,
so rate was 0, the progress anchor was dropped, and the bar sat at 0%.
Form fields only sync when the job genuinely changes, otherwise it overwrites
the user's typing mid-edit.

**One job's frames only.** `ingestAssetEvent` drops events whose `job_id` is not
the shown job. An earlier fix accepted every running job's assets and the stage
mixed unrelated renders together — lightning playing while birds appeared. The
user was rightly furious. Frames clear on job switch.

## Frame delivery — the important one

The worker delivers in bursts, not a stream. Measured: **64 asset events all
within the same 0.1s, then ~66s of silence.** That is `batch_size: 64`.

So the stage queues arriving frames and releases them at the rate they were
rendered, rather than flashing 64 and freezing:

- `queueFrames()` — appends; a >2s gap since the last arrival marks a new batch
  and resets `state.batchAt`.
- `paceMs()` — divides *remaining* time in the batch period by *remaining* queue
  depth. Dividing by queue depth alone makes the interval decelerate as the
  queue drains, so it never empties and backlog accumulates. That was the first
  attempt and it was wrong.
- `pumpFrame()` — self-rescheduling `setTimeout`, not `setInterval`.
- `playFrame()` — idle filler only, runs when the queue is empty and nothing has
  arrived for 2.5s, cycling the last 24 frames so the stage never freezes.

Verified stable at 1050ms intervals, gaps 1.0–1.1s, matching 0.97 fps.
`ops/verify/check_stagger.js` measures this. If you change batching, re-run it.

## Gallery

Sets read `/api/collections`, which aggregates from disk. Do **not** derive them
from `/api/atlas/catalog` — that caps assets at `LIMIT 10000`, so with 2048-frame
atlases only ~5 of 19 collections survived and the rest silently vanished.

Cards are 5 frames across, `gap: 0`, one collection per row, all five tiles
cycling at 120ms with each tile offset into a different region of the set.
`collectionSummary` returns 40 evenly-spread `samples` per collection to feed it.
Only cards in view animate (IntersectionObserver) so 19x5 tiles don't thrash.

The CSS needs `!important` on `display`, `position`, and the image
opacity/scale/filter. Inherited `.assetCard img` transitions and
`.registryPage .assetCard{display:block!important}` actively fight the strip
layout. Verify with `getBoundingClientRect`, not computed style.

`?set=<path>` is pushed to history; browser back, an explicit back button, and
deep links all work. Verified in `check_nav.js`.

## Thumbnails — proxy-only bug

`assetThumbnail` required `src` to start with `/outputs/`, but the client sends a
fully qualified `https://flux.influx.vision/outputs/...`. Every thumbnail 400'd
behind the proxy and worked fine on localhost. Now parses absolute URLs down to
their path. **This is why you test against the real domain.**

## Open / not done

1. **`One-Off Renders` mixes unrelated prompts** — 1,460 frames of different
   subjects in one folder from `syncOneOffRenders`. Splitting needs a grouping
   rule; filename stem (`seed-batch-observatory-*`, `orchid-continuity-*`) would
   work but is a guess at intent. **Ask the user.**
2. **Click-to-extend** — user wants to click a thumbnail, land on the job, and
   extend it: more/less latent space, change direction, add frames in thousands.
   Two paths: a new linked job seeded from the original (no backend change), or
   true atlas extension appending cells to the existing `.sphere` (needs worker
   support). Never started.
3. **Post-processing for "more life"** — user asked, no direction given yet.
   Options were: viewer-only CSS grade (free, reversible), worker post-pass on
   write (film curve, halation, grain, ~50-100ms/frame), or in-pipeline at the
   VAE stage (highest risk, would disturb atlas continuity). **Ask before
   building.**
4. **Nexus / Piper daemon status is not surfaced.** The user asked for it in
   caps and did not get it. `nexus_accepted` and `piper_asset_ready` exist as
   per-job booleans and drive the execution spine, but there is no live daemon
   health endpoint on this server the way the Governor has one. This likely
   needs a new endpoint. **This was the last thing asked and is unanswered.**
5. **Google Fonts blocks page load.** `app.css` `@import`s it and it is
   unreachable from this host, so `load` never fires and the harness waits on
   `domcontentloaded`. Vendor the fonts locally.
6. **vLLM holds 122.9 GB at SM 0%** while FLUX works at SM 98%. That is a third
   of VRAM parked idle. Worth reclaiming for bigger batches or resolutions.

## Tone

The user is a serious operator running real GPU work and has low tolerance for
being told something is fixed when it is not. Verify first, then report, and
cite the actual output. If you broke something, say so plainly and fix it. Do
not silently change their design — a 4-tile mosaic became 1 tile because the new
API only returned one thumbnail, and not flagging that was a mistake.
