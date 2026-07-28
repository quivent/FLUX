# Atlas UI Verification

Headless browser check for the Motion Atlas suite. Loads the page in real
Chromium, samples live DOM and `state` every 1.5s, and reports pass/fail
against observable behaviour rather than source reading.

## Setup

```zsh
cd ops/verify
npm install
npx playwright install chromium
```

## Run

```zsh
cd ops/verify
node verify_atlas.js
```

Options via environment:

- `ATLAS_URL` — target page. Defaults to `http://127.0.0.1:7861/motion-atlas/`.
- `WATCH_MS` — sampling window in milliseconds. Defaults to `15000`.

```zsh
ATLAS_URL=https://flux.influx.vision/motion-atlas/ WATCH_MS=20000 node verify_atlas.js
```

## What it checks

- `IMAGES DISPLAYED` — asset stage is visible and a frame has non-zero opacity.
- `SLIDESHOW CYCLING` — more than one distinct frame reached the stage.
- `PROGRESS ADVANCING` — the progress bar width changed across samples.
- `SIGIL FLICKER ON` — the `F` sigil carries `.active` while work is in flight.
- `BAR/TEXT AGREE` — bar percentage matches the `done / total` label.
- `FRAMES INGESTED` — `state.frames` is populated.
- `PREFILL CLEARED` — previous-render prefill was replaced by live assets.

It also reports page errors, console errors, and failed requests. These are
silent in the app because the SSE handlers swallow exceptions, so this is the
only place they surface.

## Note on page load

The page never fires the `load` event because `app.css` `@import`s Google
Fonts, which is unreachable in this environment. The harness waits for
`domcontentloaded` instead. If fonts need to work offline, vendor them
locally rather than importing.
