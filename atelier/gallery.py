#!/home/dev/venv/bin/python
"""Live feed of the collection: generations newest-first, cards as they land.

Each run of collection.py writes a generation under ~/runs/gen-NNNN and every
card gets a URL unique to that generation -- the fix for a page that looked
frozen while the files underneath were changing.

    /                    the feed (paginated, newest first, first page inlined)
    /api/feed?page=      JSON page of generations
    /api/cards           flat newest-first card list with absolute image URLs,
                         for a remote critic polling from afar
    /api/verdict         POST a score back onto a card
    /events              SSE; pushes the newest page as cards are published
    /thumb/<gen>/<key>   small WEBP for the grid (cached on disk)
    /img/<gen>/<key>     the full-size card PNG
    /gen/<gen>.zip       one generation as a download
"""
import asyncio
import io
import json
import pathlib
import time
import urllib.request
import zipfile

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    Response,
    StreamingResponse,
)
from PIL import Image
from pydantic import BaseModel, Field

RUNS = pathlib.Path("/home/dev/runs")
STUDIO = pathlib.Path("/home/dev/studio")
FLUXD = "http://127.0.0.1:8080"
THUMBS = pathlib.Path("/home/dev/thumbs")
PER_PAGE = 2          # generations per page
THUMB_W = 360         # grid images; the PNGs are ~1 MB each and far too heavy
COLS = 4              # one row == one batch

app = FastAPI(title="koyomi-stream")

IMMUTABLE = {"Cache-Control": "public, max-age=31536000, immutable"}


def generations():
    """Every generation on disk, newest first, each with its manifest + verdicts."""
    out = []
    for d in sorted(RUNS.glob("gen-*"), key=lambda p: p.name, reverse=True):
        f = d / "run.json"
        if not f.is_file():
            continue
        try:
            m = json.loads(f.read_text())
        except json.JSONDecodeError:
            continue  # mid-write; it will be there on the next poll
        m["id"] = d.name
        verdicts = {}
        vf = d / "verdicts.json"
        if vf.is_file():
            try:
                verdicts = json.loads(vf.read_text())
            except json.JSONDecodeError:
                verdicts = {}
        for c in m.get("cards", []):
            c["verdict"] = verdicts.get(c["key"])
        out.append(m)
    return out


def fingerprint():
    """Cheap state token: changes when any manifest or verdict file is written."""
    fp = []
    for d in sorted(RUNS.glob("gen-*")):
        for name in ("run.json", "verdicts.json"):
            f = d / name
            if f.is_file():
                fp.append((d.name, name, f.stat().st_mtime))
    return fp


@app.get("/api/feed")
def feed(page: int = Query(0, ge=0)):
    gens = generations()
    start = page * PER_PAGE
    return JSONResponse(
        {
            "page": page,
            "pages": max(1, (len(gens) + PER_PAGE - 1) // PER_PAGE),
            "total": len(gens),
            "per_page": PER_PAGE,
            "generations": gens[start : start + PER_PAGE],
        }
    )


@app.get("/api/cards")
def cards(request: Request, limit: int = Query(40, ge=1, le=500)):
    """Flat, newest-first card list with absolute URLs.

    This is the surface a critic running elsewhere polls: it needs to fetch the
    pixels over the public endpoint, so relative paths are no use to it.
    """
    base = str(request.base_url).rstrip("/")
    out = []
    for g in generations():
        for c in g.get("cards", []):
            out.append(
                {
                    "generation": g["id"],
                    "key": c["key"],
                    "product": c["product"],
                    "character": c["character"],
                    "quote": c["quote"],
                    "seed": c.get("seed"),
                    "width": g.get("width"),
                    "height": g.get("height"),
                    "steps": g.get("steps"),
                    "label": g.get("label", ""),
                    "image": f"{base}/img/{g['id']}/{c['key']}",
                    "thumb": f"{base}/thumb/{g['id']}/{c['key']}",
                    "verdict": c.get("verdict"),
                }
            )
            if len(out) >= limit:
                return JSONResponse({"cards": out})
    return JSONResponse({"cards": out})


class Verdict(BaseModel):
    generation: str
    key: str
    score: float | None = Field(default=None, ge=0, le=10)
    verdict: str | None = None  # keep | reroll | edit
    note: str | None = None
    critic: str = "unknown"


@app.post("/api/verdict")
def put_verdict(v: Verdict):
    """Record a critic's judgement on one card. Idempotent per (generation, key)."""
    d = (RUNS / v.generation).resolve()
    if RUNS.resolve() not in d.parents or not d.is_dir():
        raise HTTPException(404, "no such generation")
    vf = d / "verdicts.json"
    data = {}
    if vf.is_file():
        try:
            data = json.loads(vf.read_text())
        except json.JSONDecodeError:
            data = {}
    data[v.key] = {
        "score": v.score,
        "verdict": v.verdict,
        "note": v.note,
        "critic": v.critic,
    }
    vf.write_text(json.dumps(data, indent=2))
    return JSONResponse({"ok": True, "generation": v.generation, "key": v.key})


@app.get("/events")
async def events():
    """SSE: emit the newest page whenever a card or verdict is written."""

    async def stream():
        last = None
        while True:
            now = fingerprint()
            if now != last:
                last = now
                gens = generations()
                payload = {
                    "page": 0,
                    "pages": max(1, (len(gens) + PER_PAGE - 1) // PER_PAGE),
                    "total": len(gens),
                    "per_page": PER_PAGE,
                    "generations": gens[:PER_PAGE],
                }
                yield f"data: {json.dumps(payload)}\n\n"
            else:
                yield ": keepalive\n\n"
            await asyncio.sleep(1.0)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _card_path(gen, key):
    path = (RUNS / gen / "cards" / f"{key}.png").resolve()
    if RUNS.resolve() not in path.parents or not path.is_file():
        raise HTTPException(404, "no such card")
    return path


@app.get("/thumb/{gen}/{key}")
def thumb(gen: str, key: str):
    """Downscaled WEBP for the grid, generated once and cached.

    The grid was pulling eight ~1 MB PNGs per generation; at THUMB_W these are
    roughly 25x smaller, which is the whole difference in how fast the feed paints.
    """
    src = _card_path(gen, key)
    dst = THUMBS / gen / f"{key}.webp"
    if not dst.is_file() or dst.stat().st_mtime < src.stat().st_mtime:
        dst.parent.mkdir(parents=True, exist_ok=True)
        im = Image.open(src).convert("RGB")
        im.thumbnail((THUMB_W, THUMB_W * 4), Image.LANCZOS)
        im.save(dst, "WEBP", quality=82, method=4)
    return FileResponse(dst, media_type="image/webp", headers=IMMUTABLE)


@app.get("/img/{gen}/{key}")
def img(gen: str, key: str):
    return FileResponse(_card_path(gen, key), media_type="image/png", headers=IMMUTABLE)


@app.get("/gen/{gen}.zip")
def gen_zip(gen: str):
    d = (RUNS / gen).resolve()
    if RUNS.resolve() not in d.parents or not d.is_dir():
        raise HTTPException(404, "no such generation")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted((d / "cards").glob("*.png")):
            z.write(p, f"{gen}/{p.name}")
        for name in ("run.json", "verdicts.json"):
            if (d / name).is_file():
                z.write(d / name, f"{gen}/{name}")
    return Response(
        buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{gen}.zip"'},
    )


# ---------------------------------------------------------------- studio


def _studio_runs():
    out = []
    for d in sorted(STUDIO.glob("*"), reverse=True):
        f = d / "meta.json"
        if f.is_file():
            try:
                out.append(json.loads(f.read_text()))
            except json.JSONDecodeError:
                pass
    return out


@app.get("/api/studio")
def studio_list():
    return JSONResponse({"runs": _studio_runs()[:60]})


@app.post("/api/studio/edit")
async def studio_edit(
    file: UploadFile | None = File(None),
    instruction: str = Form(...),
    steps: int = Form(28),
    guidance: float = Form(2.5),
    seed: int | None = Form(None),
    variants: int = Form(1),
    source_id: str | None = Form(None),
    source_which: str = Form("out"),
):
    """Drop an image + an instruction; Kontext returns the edit.

    The upload is written to disk first because fluxd takes a path, not bytes --
    it runs in a different process with its own view of the filesystem.
    """
    variants = max(1, min(8, variants))
    rid = f"{int(time.time() * 1000)}"
    d = STUDIO / rid
    d.mkdir(parents=True, exist_ok=True)
    src = d / "in.png"
    lineage = None

    if source_id:
        # Chain: an earlier run's output becomes this run's input.
        prev = (STUDIO / source_id / f"{source_which}.png").resolve()
        if STUDIO.resolve() not in prev.parents or not prev.is_file():
            raise HTTPException(404, "no such source image")
        src.write_bytes(prev.read_bytes())
        lineage = f"{source_id}:{source_which}"
    else:
        if file is None:
            raise HTTPException(400, "need an upload or a source_id")
        data = await file.read()
        if not data:
            raise HTTPException(400, "empty upload")
        if len(data) > 32 * 1024 * 1024:
            raise HTTPException(413, "image too large (32 MB cap)")
        try:
            Image.open(io.BytesIO(data)).convert("RGB").save(src)
        except Exception:
            raise HTTPException(400, "not a readable image")

    def one(v_seed, idx):
        body = json.dumps(
            {
                "image": str(src),
                "instruction": instruction,
                "steps": steps,
                "guidance": guidance,
                "seed": v_seed,
                "stem": f"studio-{rid}-{idx}",
            }
        ).encode()
        req = urllib.request.Request(
            f"{FLUXD}/edit", data=body, headers={"Content-Type": "application/json"}
        )
        return json.load(urllib.request.urlopen(req, timeout=1800))

    outs = []
    try:
        for i in range(variants):
            # Variants differ ONLY by seed, so they are alternatives of the same
            # instruction rather than four different asks.
            v_seed = None if seed is None else seed + i
            r = await asyncio.to_thread(one, v_seed, i)
            name = "out.png" if i == 0 else f"out-{i}.png"
            (d / name).write_bytes(pathlib.Path(r["path"]).read_bytes())
            outs.append({"which": name[:-4], "seed": r.get("seed"),
                         "seconds": r.get("seconds")})
    except Exception as e:
        if not outs:
            raise HTTPException(502, f"kontext failed: {e}")

    result = {}
    meta = {
        "id": rid,
        "instruction": instruction,
        "steps": steps,
        "guidance": guidance,
        "seed": outs[0]["seed"] if outs else seed,
        "seconds": round(sum(o["seconds"] or 0 for o in outs), 1),
        "width": None,
        "height": None,
        "variants": outs,
        "lineage": lineage,
        "filename": (file.filename if file is not None else f"from {lineage}"),
        "created": time.time(),
    }
    (d / "meta.json").write_text(json.dumps(meta, indent=2))
    return JSONResponse(meta)


@app.get("/studio/img/{rid}/{which}")
def studio_img(rid: str, which: str):
    if which != "in" and not which.startswith("out"):
        raise HTTPException(404, "no such image")
    path = (STUDIO / rid / f"{which}.png").resolve()
    if STUDIO.resolve() not in path.parents or not path.is_file():
        raise HTTPException(404, "no such image")
    return FileResponse(path, media_type="image/png", headers=IMMUTABLE)


@app.get("/studio", response_class=HTMLResponse)
def studio():
    return STUDIO_PAGE.replace("__RUNS__", json.dumps(_studio_runs()[:60]))


STUDIO_PAGE = r"""<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>KOYOMI — Kontext studio</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin:0; padding:36px 26px 90px; background:#17150f; color:#efe6d6;
         font:16px/1.6 "Iowan Old Style", Palatino, Georgia, serif; }
  header { max-width:1100px; margin:0 auto 26px; text-align:center; }
  h1 { font-size:15px; letter-spacing:.42em; font-weight:500; margin:0 0 8px;
       text-indent:.42em; color:#d8c9a8; }
  header a { font-size:12px; color:#8f8570; letter-spacing:.14em; }
  .panel { max-width:1100px; margin:0 auto 34px; }
  #drop { border:1px dashed #4a4231; background:#1d1a12; padding:44px 20px; text-align:center;
          color:#8f8570; cursor:pointer; transition:border-color .18s, background .18s; }
  #drop.over { border-color:#b99b5e; background:#231f15; color:#d8c9a8; }
  #drop img { max-height:230px; margin-top:14px; border:1px solid #302b1e; }
  .row { display:flex; gap:12px; margin-top:16px; flex-wrap:wrap; }
  textarea { flex:1 1 420px; min-height:74px; background:#1d1a12; color:#efe6d6;
             border:1px solid #3a3324; padding:11px 13px; font:inherit; font-size:15px; resize:vertical; }
  .opts { display:flex; gap:10px; align-items:center; flex-wrap:wrap; }
  label { font-size:12px; color:#6b634f; letter-spacing:.12em; text-transform:uppercase; }
  input[type=number] { width:86px; background:#1d1a12; color:#efe6d6; border:1px solid #3a3324;
                       padding:7px 9px; font:inherit; font-size:14px; }
  button { font:inherit; font-size:14px; background:#b99b5e; color:#17150f; border:0;
           padding:11px 26px; cursor:pointer; letter-spacing:.1em; }
  button:disabled { opacity:.45; cursor:default; }
  .status { font-size:13px; color:#8f8570; font-style:italic; margin-top:12px; min-height:22px; }
  .pair { display:grid; grid-template-columns:1fr 1fr; gap:18px; margin-top:16px; }
  .pair figure { margin:0; }
  .pair img { width:100%; border:1px solid #302b1e; display:block; }
  .pair figcaption { font-size:11px; letter-spacing:.18em; color:#6b634f; text-transform:uppercase;
                     margin-bottom:7px; }
  .outs { display:grid; grid-template-columns:repeat(auto-fit,minmax(120px,1fr)); gap:8px; }
  .outs figure { margin:0; cursor:pointer; position:relative; }
  .outs img { border:1px solid #302b1e; transition:border-color .15s, transform .15s; }
  .outs figure:hover img { border-color:#b99b5e; transform:translateY(-2px); }
  .outs .tag { position:absolute; left:6px; top:6px; font-size:10px; letter-spacing:.12em;
               background:rgba(20,18,14,.85); color:#b99b5e; padding:2px 6px; }
  .chain { font-size:12px; color:#8f8570; font-style:italic; margin-top:8px; }
  .hist { max-width:1100px; margin:0 auto; }
  .hist h2 { font-size:12px; letter-spacing:.24em; color:#8f8570; font-weight:500;
             text-transform:uppercase; border-bottom:1px solid #302b1e; padding-bottom:8px; }
  .item { display:grid; grid-template-columns:110px 110px 1fr; gap:14px; align-items:start;
          padding:14px 0; border-bottom:1px solid #241f16; }
  .item img { width:100%; border:1px solid #302b1e; display:block; }
  .item .txt { font-size:14px; color:#d8c9a8; }
  .item .sub { font-size:12px; color:#6b634f; font-style:italic; }
</style>
<header>
  <h1>KONTEXT STUDIO</h1>
  <a href="/">← back to the feed</a>
</header>

<div class="panel">
  <div id="drop">
    <b>Drop an image here</b><br><span>or click to choose — any reference, sketch, or card</span>
    <div id="preview"></div>
  </div>
  <input type="file" id="file" accept="image/*" hidden>
  <div class="row">
    <textarea id="instr" placeholder="What should change? e.g. &quot;Reframe to a waist-up portrait, keep the costume and background&quot;"></textarea>
  </div>
  <div class="row opts">
    <label>steps</label><input type="number" id="steps" value="28" min="4" max="60">
    <label>guidance</label><input type="number" id="guid" value="2.5" step="0.1" min="1" max="8">
    <label>seed</label><input type="number" id="seed" placeholder="random">
    <label>variants</label><input type="number" id="vars" value="1" min="1" max="8">
    <button id="go" disabled>Run Kontext</button>
  </div>
  <div class="status" id="status"></div>
  <div class="pair" id="pair" style="display:none">
    <figure><figcaption>before</figcaption><img id="before"></figure>
    <figure><figcaption>after — click any variant to run it back through</figcaption>
      <div id="outs" class="outs"></div></figure>
  </div>
</div>

<div class="hist">
  <h2>History</h2>
  <div id="hist"></div>
</div>

<script>
  const RUNS = __RUNS__;
  let chosen = null, chainFrom = null;
  const $ = id => document.getElementById(id);
  const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

  function pick(f) {
    if (!f || !f.type.startsWith('image/')) return;
    chosen = f; chainFrom = null;
    $('preview').innerHTML = `<img src="${URL.createObjectURL(f)}">`;
    $('go').disabled = false;
  }

  $('drop').onclick = () => $('file').click();
  $('file').onchange = e => pick(e.target.files[0]);
  ['dragenter','dragover'].forEach(ev => $('drop').addEventListener(ev, e => {
    e.preventDefault(); $('drop').classList.add('over'); }));
  ['dragleave','drop'].forEach(ev => $('drop').addEventListener(ev, e => {
    e.preventDefault(); $('drop').classList.remove('over'); }));
  $('drop').addEventListener('drop', e => pick(e.dataTransfer.files[0]));
  // Paste straight from the clipboard, which is how a screenshot usually arrives.
  window.addEventListener('paste', e => {
    for (const it of e.clipboardData.files) { pick(it); break; }
  });

  $('go').onclick = async () => {
    if (!chosen && !chainFrom) return;
    const instr = $('instr').value.trim();
    if (!instr) { $('status').textContent = 'give it an instruction first'; return; }
    $('go').disabled = true;
    const t0 = performance.now();
    $('status').textContent = 'running…';
    const fd = new FormData();
    fd.append('file', chosen);
    fd.append('instruction', instr);
    fd.append('steps', $('steps').value);
    fd.append('guidance', $('guid').value);
    fd.append('variants', $('vars').value);
    if ($('seed').value) fd.append('seed', $('seed').value);
    if (chainFrom) { fd.append('source_id', chainFrom.id); fd.append('source_which', chainFrom.which); }
    try {
      const r = await fetch('/api/studio/edit', { method: 'POST', body: fd });
      if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
      const m = await r.json();
      $('before').src = `/studio/img/${m.id}/in`;
      $('outs').innerHTML = (m.variants || [{which:'out', seed:m.seed}]).map(v =>
        `<figure data-id="${esc(m.id)}" data-which="${esc(v.which)}">
           <img src="/studio/img/${esc(m.id)}/${esc(v.which)}">
           <span class="tag">${esc(v.seed)}</span>
         </figure>`).join('');
      $('pair').style.display = 'grid';
      wireOuts();
      $('status').textContent = `${(m.variants||[]).length} variant(s) · ${m.seconds}s GPU · ${((performance.now()-t0)/1000).toFixed(1)}s round trip`;
      RUNS.unshift(m); paintHist();
    } catch (err) {
      $('status').textContent = 'failed: ' + err.message;
    }
    $('go').disabled = false;
  };

  // Click a variant to make it the next input: this is the whole point of an
  // iterative editor, and it beats downloading and re-uploading.
  function wireOuts() {
    document.querySelectorAll('.outs figure').forEach(f => {
      f.onclick = () => {
        chainFrom = { id: f.dataset.id, which: f.dataset.which };
        chosen = null;
        $('preview').innerHTML = `<img src="/studio/img/${f.dataset.id}/${f.dataset.which}">`;
        $('drop').insertAdjacentHTML('beforeend',
          `<div class="chain">chaining from ${f.dataset.id}:${f.dataset.which} — type the next instruction</div>`);
        $('go').disabled = false;
        $('instr').focus();
        window.scrollTo({ top: 0, behavior: 'smooth' });
      };
    });
  }

  function paintHist() {
    $('hist').innerHTML = RUNS.map(m => `
      <div class="item">
        <img src="/studio/img/${esc(m.id)}/in" loading="lazy">
        <img src="/studio/img/${esc(m.id)}/out" loading="lazy">
        <div><div class="txt">${esc(m.instruction)}</div>
             <div class="sub">${esc((m.variants||[]).length || 1)} variant(s) · ${esc(m.seconds)}s · seed ${esc(m.seed)}${m.lineage ? ' · chained from ' + esc(m.lineage) : ''}</div></div>
      </div>`).join('') || '<div class="sub" style="padding:16px 0">nothing yet</div>';
  }
  paintHist();
</script>
"""


@app.get("/", response_class=HTMLResponse)
def index():
    """The first page is inlined into the HTML.

    Fetching it client-side meant every load flashed "waiting for the first
    generation" before the data arrived, which reads as broken when there is in
    fact plenty on disk.
    """
    gens = generations()
    initial = {
        "page": 0,
        "pages": max(1, (len(gens) + PER_PAGE - 1) // PER_PAGE),
        "total": len(gens),
        "per_page": PER_PAGE,
        "generations": gens[:PER_PAGE],
    }
    return PAGE.replace("__INITIAL__", json.dumps(initial)).replace("__COLS__", str(COLS))


PAGE = r"""<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>KOYOMI — live render feed</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 36px 26px 90px;
    background: #17150f; color: #efe6d6;
    font: 16px/1.6 "Iowan Old Style", Palatino, Georgia, serif;
  }
  header { max-width: 1440px; margin: 0 auto 22px; text-align: center; }
  h1 { font-size: 15px; letter-spacing: .42em; font-weight: 500; margin: 0 0 10px;
       text-indent: .42em; color: #d8c9a8; }
  .live { display: inline-flex; align-items: center; gap: 8px; font-size: 12px;
          letter-spacing: .18em; color: #8f8570; text-transform: uppercase; }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: #4b7f4b;
         box-shadow: 0 0 0 0 rgba(75,127,75,.7); animation: pulse 2s infinite; }
  .dot.off { background: #6b4a3a; animation: none; }
  @keyframes pulse { 70% { box-shadow: 0 0 0 9px rgba(75,127,75,0); }
                     100% { box-shadow: 0 0 0 0 rgba(75,127,75,0); } }
  .gen { max-width: 1440px; margin: 0 auto 44px; }
  .genhead { display: flex; align-items: baseline; gap: 14px; flex-wrap: wrap;
             border-bottom: 1px solid #302b1e; padding-bottom: 9px; margin-bottom: 20px; }
  .genhead b { font-size: 15px; letter-spacing: .22em; color: #d8c9a8; font-weight: 500; }
  .genhead .meta { font-size: 13px; color: #6b634f; font-style: italic; }
  .genhead .newest { font-size: 10px; letter-spacing: .18em; color: #17150f;
                     background: #b99b5e; padding: 3px 8px; border-radius: 2px; }
  .genhead a { margin-left: auto; font-size: 12px; color: #8f8570; letter-spacing: .1em; }
  /* One row is one batch: exactly __COLS__ across, collapsing on narrow screens. */
  .grid { display: grid; gap: 22px; grid-template-columns: repeat(__COLS__, minmax(0, 1fr)); }
  @media (max-width: 900px) { .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
  @media (max-width: 480px) { .grid { grid-template-columns: 1fr; } }
  figure { margin: 0; animation: rise .45s ease both; }
  @keyframes rise { from { opacity: 0; transform: translateY(12px); } }
  figure a { display: block; border: 1px solid #302b1e; background: #0e0d09;
             transition: border-color .18s, transform .18s; }
  figure a:hover { border-color: #6d6244; transform: translateY(-3px); }
  /* Reserve the card's aspect box so the grid doesn't reflow as images arrive. */
  img { display: block; width: 100%; height: auto; aspect-ratio: 2 / 3; object-fit: cover;
        background: #0e0d09; }
  figcaption { margin-top: 8px; font-size: 12px; color: #8f8570;
               display: flex; justify-content: space-between; gap: 10px; align-items: baseline; }
  figcaption b { color: #d8c9a8; font-weight: 500; letter-spacing: .09em; }
  .score { font-size: 11px; letter-spacing: .1em; padding: 2px 7px; border-radius: 2px;
           background: #241f16; border: 1px solid #3a3324; color: #b99b5e; }
  .score.keep { color: #8fbf7f; border-color: #3c5236; }
  .score.reroll { color: #d08a72; border-color: #5b3a2e; }
  .pager { max-width: 1440px; margin: 0 auto; display: flex; justify-content: center;
           gap: 10px; padding-top: 18px; border-top: 1px solid #302b1e; }
  .pager button { font: inherit; font-size: 13px; background: #201d14; color: #d8c9a8;
                  border: 1px solid #3a3324; padding: 7px 16px; cursor: pointer; }
  .pager button:disabled { opacity: .35; cursor: default; }
  .pager span { font-size: 13px; color: #6b634f; align-self: center; }
  .empty { text-align: center; color: #6b634f; font-style: italic; padding: 60px 0; }
</style>
<header>
  <h1>KOYOMI</h1>
  <div class="live"><span class="dot" id="dot"></span><span id="status">live</span></div>
  <div style="margin-top:10px"><a href="/studio" style="font-size:12px;color:#8f8570;letter-spacing:.14em">KONTEXT STUDIO →</a></div>
</header>
<main id="feed"></main>
<div class="pager">
  <button id="prev">← newer</button>
  <span id="pageinfo"></span>
  <button id="next">older →</button>
</div>
<script>
  // Inlined by the server so the first paint needs no round trip.
  const INITIAL = __INITIAL__;
  let page = 0, pages = 1;
  const feed = document.getElementById('feed');

  const esc = s => String(s ?? '').replace(/[&<>"]/g, c =>
    ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

  function scoreChip(v) {
    if (!v) return '<span></span>';
    const cls = v.verdict === 'keep' ? ' keep' : v.verdict === 'reroll' ? ' reroll' : '';
    const bits = [v.score != null ? Number(v.score).toFixed(1) : null, v.verdict]
      .filter(Boolean).join(' · ');
    return bits ? `<span class="score${cls}" title="${esc(v.note || '')}">${esc(bits)}</span>` : '<span></span>';
  }

  function renderGen(g, isNewest) {
    const cards = (g.cards || []).map(c => `
      <figure>
        <a href="/img/${esc(g.id)}/${esc(c.key)}" target="_blank">
          <img src="/thumb/${esc(g.id)}/${esc(c.key)}" alt="${esc(c.product)}" loading="lazy" decoding="async">
        </a>
        <figcaption><b>${esc(c.product)}</b>${scoreChip(c.verdict)}</figcaption>
      </figure>`).join('');
    const dims = g.width ? `${g.width}×${g.height}` : '';
    const bits = [dims, g.steps ? g.steps + ' steps' : '', g.batch_size ? 'batch ' + g.batch_size : '',
                  (g.cards || []).length + ' cards', esc(g.label || '')].filter(Boolean).join(' · ');
    return `<section class="gen">
      <div class="genhead">
        <b>${esc(g.id).toUpperCase()}</b>
        ${isNewest && page === 0 ? '<span class="newest">NEWEST</span>' : ''}
        <span class="meta">${bits}</span>
        <a href="/gen/${esc(g.id)}.zip">download ↓</a>
      </div>
      <div class="grid">${cards}</div>
    </section>`;
  }

  function paint(d) {
    page = d.page; pages = d.pages;
    feed.innerHTML = (d.generations || []).length
      ? d.generations.map((g, i) => renderGen(g, i === 0)).join('')
      : '<div class="empty">no generations yet — run collection.py</div>';
    document.getElementById('pageinfo').textContent = `page ${page + 1} of ${pages} · ${d.total} generations`;
    document.getElementById('prev').disabled = page === 0;
    document.getElementById('next').disabled = page >= pages - 1;
  }

  async function load(p) {
    const r = await fetch(`/api/feed?page=${p}`);
    paint(await r.json());
  }

  document.getElementById('prev').onclick = () => load(Math.max(0, page - 1));
  document.getElementById('next').onclick = () => load(page + 1);

  paint(INITIAL);

  // Live: repaint only while the viewer is on page 0, so paging back through
  // history isn't yanked out from under them.
  const es = new EventSource('/events');
  es.onopen = () => { document.getElementById('status').textContent = 'live';
                      document.getElementById('dot').classList.remove('off'); };
  es.onerror = () => { document.getElementById('status').textContent = 'reconnecting';
                       document.getElementById('dot').classList.add('off'); };
  es.onmessage = e => {
    const d = JSON.parse(e.data);
    if (page === 0) paint(d);
    else document.getElementById('status').textContent = 'live · new work on page 1';
  };
</script>
"""

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8090, log_level="warning")
