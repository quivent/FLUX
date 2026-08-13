#!/home/dev/venv/bin/python
"""Triage daemon: stage 1 of the Perpetual Sieve, run on the node it serves.

  POST /triage  {"images": [{"key", "url"}, ...], "keep": N, "batch": "gen-XXXX"}
                -> {"blocked": [key, ...], "reasons": {key: why}, ...}
  GET  /health  -> {"ready": true, "model": ..., "vram_gib": ...}

WHY THIS EXISTS AS A LOCAL DAEMON
Triage used to be a remote "officer" behind an expose_port URL. Those URLs are
bound to the container that minted them: the node stopped, the endpoint died,
and givemeanode never resurrects it. perpetual.py kept the dead URL as a
constant, so every cycle of this session logged

    [perpetual] officer unreachable (<HTTPError 404: 'Not Found'>)

and passed every render through unfiltered. A gate that fails open forever is
not a gate. The fix is not a fresher URL -- the next stop kills that one too --
it is to put triage on the same node as the loop, on loopback, where it lives
and dies with the work it filters. estate.py already names this file
(MODELS["triage"] = "triaged.py"); this is that daemon.

WHAT IT CAN AND CANNOT SEE
The original design named Qwen2.5-VL. It is not in /home/dev/hf-cache and
HF_HUB_OFFLINE=1 means it cannot be fetched, so it is not an option here. The
one vision model actually cached on this node is openai/clip-vit-base-patch32.
CLIP is not a VLM: it cannot look at a render and tell you the hands are wrong.
What it can do is score an image against a fixed set of text probes, which is
enough to separate "a picture of something" from "a blank, blown-out, or
noise-filled frame". So triage runs two gates, in order:

  1. STRUCTURAL (numpy, no model)  Certain render failures -- a frame with no
     information in it at all. Near-zero variance, crushed black, blown white,
     or a mosaic of flat tiles. These are arithmetic, not opinion.
  2. SEMANTIC (CLIP zero-shot)     Frames that are structurally busy but are
     still garbage -- static, glitch fields, washed smears. Measured on this
     node's own output the margin is wide: real cards score p_defect 0.03-0.10,
     synthetic blanks/noise 0.80-0.9997, so DEFECT_P sits at 0.55 with room on
     both sides.

Gate 1 catches what gate 2 is unreliable about (a perfectly flat fill is a
plausible "solid flat block of one color" to CLIP but also to a legitimate
minimalist render); gate 2 catches what gate 1 is blind to (uniform noise has
healthy variance). Together they cover gross failure.

THIS IS DEFECT DETECTION, NOT QUALITY RATING. It never asks whether a render is
good, only whether it is broken. Ranking against the human's taste is taste.py's
job downstream, and triage must not put a thumb on that scale -- a merely ugly
render has to reach the scorer so a Retire verdict can teach the anchor set
something. Blocking on taste here would quietly launder a machine's preference
into the human's.

One GPU shared with a resident FLUX, so the CLIP pass is serialized behind a
lock and the model is fp16 (~0.3 GiB) -- small enough to sit beside FLUX without
evicting it.

    ./triaged.py
"""
import asyncio
import io
import pathlib
import urllib.request

import numpy as np
import torch
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from PIL import Image
from pydantic import BaseModel, Field
from transformers import CLIPModel, CLIPProcessor

MODEL_ID = "openai/clip-vit-base-patch32"
RUNS = pathlib.Path("/home/dev/runs")

# Port map on this node: 8080 fluxd, 8090 gallery, 8091 control panel, 8092 here.
PORT = 8092
FETCH_TIMEOUT = 30

# Gate 1 thresholds, in 8-bit luma on a 256px-wide downscale. Calibrated against
# this node's own cards, which run mean ~195-205, std ~61-73, flat_tiles 0.00-0.03.
BLANK_STD = 6.0        # whole-frame std below this is no image at all
BLACK_MEAN = 8.0       # crushed to nothing
BLOWN_MEAN = 247.0     # blown to nothing
FLAT_TILE_STD = 2.0    # a tile with less spread than this carries no detail
FLAT_TILE_FRAC = 0.90  # ...and a frame that is mostly such tiles is a dead render
TILES = 8              # 8x8 grid

# Gate 2. Probability mass on the defect side of the probe set above which the
# render is called broken. Real cards measured 0.03-0.10, degenerate ones 0.80+.
DEFECT_P = 0.55
# ...and above this the call is not a judgement any more. A block at or over
# DEFECT_HARD is never waived by the anti-wipe rail below, so a lone garbage
# frame still gets blocked when it is the only candidate in its batch.
DEFECT_HARD = 0.90

DEFECT_PROBES = [
    "a blank empty image with nothing in it",
    "a solid flat block of one color",
    "an image of random noise and static",
    "a corrupted glitched image with digital artifacts",
    "a blurry gray smear with no recognizable subject",
]
GOOD_PROBES = [
    "an illustration of a character",
    "a detailed painting of a scene",
    "a decorative art print with a border",
    "a photograph of an object",
]
PROBES = DEFECT_PROBES + GOOD_PROBES

app = FastAPI(title="triaged")
_lock = asyncio.Lock()
_model = None
_proc = None


class ImageRef(BaseModel):
    key: str
    url: str


class TriageReq(BaseModel):
    images: list[ImageRef] = Field(default_factory=list)
    keep: int = 4
    batch: str = ""


def load():
    """Load CLIP once and keep it warm, exactly like fluxd holds the pipeline."""
    global _model, _proc
    if _model is None:
        _proc = CLIPProcessor.from_pretrained(MODEL_ID)
        _model = CLIPModel.from_pretrained(MODEL_ID).to("cuda", dtype=torch.float16).eval()
    return _model, _proc


def fetch(ref, batch):
    """Bytes for one image ref, gallery first, disk as the backstop.

    The caller hands us {SELF_FEED}/img/<gen>/<key>, which the gallery serves. But
    the gallery is a separate process and triage should not start blocking renders
    because a sibling daemon died, so when the fetch fails we resolve the same card
    off the disk the gallery would have read it from.
    """
    try:
        with urllib.request.urlopen(ref.url, timeout=FETCH_TIMEOUT) as r:
            return r.read()
    except Exception:
        card = RUNS / batch / "cards" / f"{ref.key}.png"
        if card.is_file():
            return card.read_bytes()
        raise


def structural(im):
    """Gate 1: is there any information in this frame? Returns a reason or None."""
    w = 256
    small = im.convert("RGB").resize((w, max(1, round(w * im.height / im.width))))
    g = np.asarray(small, dtype=np.float32).mean(-1)

    if g.std() < BLANK_STD:
        return f"near-uniform frame (std {g.std():.2f} < {BLANK_STD})"
    if g.mean() < BLACK_MEAN:
        return f"crushed black (mean {g.mean():.1f} < {BLACK_MEAN})"
    if g.mean() > BLOWN_MEAN:
        return f"blown white (mean {g.mean():.1f} > {BLOWN_MEAN})"

    # A frame can hold spread globally (a dark half and a light half) while every
    # local patch is dead flat. That is a failed render too, so look per tile.
    h, wd = g.shape
    th, tw = max(1, h // TILES), max(1, wd // TILES)
    flat = sum(
        1
        for i in range(TILES)
        for j in range(TILES)
        if g[i * th:(i + 1) * th, j * tw:(j + 1) * tw].std() < FLAT_TILE_STD
    )
    frac = flat / float(TILES * TILES)
    if frac >= FLAT_TILE_FRAC:
        return f"flat tiles ({frac:.0%} of the frame carries no detail)"
    return None


def semantic(images):
    """Gate 2: p(defect) per image from one batched CLIP pass."""
    model, proc = load()
    with torch.no_grad():
        inp = proc(text=PROBES, images=images, return_tensors="pt", padding=True).to("cuda")
        inp["pixel_values"] = inp["pixel_values"].half()
        probs = model(**inp).logits_per_image.float().softmax(-1)
    return probs[:, : len(DEFECT_PROBES)].sum(-1).tolist()


def triage_batch(req):
    """The whole verdict for one request. Runs on a worker thread, holds the GPU."""
    blocked, reasons, notes = [], {}, {}
    loaded, structural_kills = [], set()

    for ref in req.images:
        try:
            im = Image.open(io.BytesIO(fetch(ref, req.batch)))
            im.load()
        except Exception as e:
            # Our failure, not the render's. Fail open: an unreadable fetch must
            # never cost a render its place, or triage becomes the defect.
            notes[ref.key] = f"unfetchable, passed ({e!r})"
            continue
        why = structural(im)
        if why:
            blocked.append(ref.key)
            reasons[ref.key] = why
            structural_kills.add(ref.key)
        else:
            loaded.append((ref.key, im.convert("RGB")))

    scores = semantic([im for _, im in loaded]) if loaded else []
    semantic_kills = [(k, p) for (k, _), p in zip(loaded, scores) if p >= DEFECT_P]

    # The rail: gate 2 is a judgement call, so it must not be the reason a whole
    # batch of otherwise-sound frames disappears. If the probe wants every
    # structurally-clean render gone, spare the least-defective one and say so.
    #
    # Two things switch it off.
    #
    # It stands down when even the least-defective candidate sits at or above
    # DEFECT_HARD. That is not a soft signal, and the loop emits batches as small
    # as one, so an unconditional rail would mean gate 2 could never block
    # anything at all -- the same silent no-op the dead officer URL produced.
    #
    # It also stands down the moment gate 1 has condemned anything. Gate 1 is
    # arithmetic -- a frame it kills really is empty -- so a batch that has
    # already lost renders to it is a genuinely broken batch, and sparing the
    # last frame would just hand the champion selector a noise field. "Every
    # variant blocked" is a real outcome the loop already handles; it should not
    # be papered over.
    survivors = len(loaded) - len(semantic_kills)
    if loaded and survivors < 1 and not structural_kills and req.keep >= 1:
        semantic_kills.sort(key=lambda kp: kp[1])
        if semantic_kills[0][1] < DEFECT_HARD:
            spared, p = semantic_kills.pop(0)
            notes[spared] = (f"probe wanted the whole batch (p_defect {p:.3f}); "
                             f"spared as least-defective")

    for k, p in semantic_kills:
        blocked.append(k)
        reasons[k] = f"probe reads as a defect (p_defect {p:.3f} >= {DEFECT_P})"
    for (k, _), p in zip(loaded, scores):
        if k not in reasons:
            notes.setdefault(k, f"p_defect {p:.3f}")

    return {
        "blocked": blocked,
        "reasons": reasons,
        "notes": notes,
        "checked": len(req.images),
        "structural": len(structural_kills),
        "semantic": len(blocked) - len(structural_kills),
        "model": MODEL_ID,
        "batch": req.batch,
    }


@app.on_event("startup")
def _warm():
    load()


@app.get("/health")
def health():
    return {
        "ready": _model is not None,
        "model": MODEL_ID,
        "port": PORT,
        "defect_p": DEFECT_P,
        "vram_gib": round(torch.cuda.memory_allocated() / 2**30, 3),
        "vram_free_gib": round(torch.cuda.mem_get_info()[0] / 2**30, 2),
    }


@app.post("/triage")
async def triage(req: TriageReq):
    """Defects out, everything else through. Never a quality ranking."""
    if not req.images:
        return JSONResponse({"blocked": [], "reasons": {}, "notes": {}, "checked": 0,
                             "structural": 0, "semantic": 0, "model": MODEL_ID,
                             "batch": req.batch})
    async with _lock:
        result = await asyncio.to_thread(triage_batch, req)
    return JSONResponse(result)


if __name__ == "__main__":
    import uvicorn

    # Loopback only: this is the loop's own organ, not a public surface. Nothing
    # off-node needs it, and binding wider would just re-create the officer.
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="info")
