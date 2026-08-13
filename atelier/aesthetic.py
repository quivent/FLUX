#!/home/dev/venv/bin/python
"""A quality signal for a rendered card that is about the PICTURE, not about
resembling a previous picture.

WHY NOT taste.py
taste.py fits a landscape from anchors and repulsors the operator has voted on.
Right now that landscape is two machine-seeded anchors drawn from a single
concept ("sencha") and zero repulsors, so its "fitness" is, arithmetically,
cosine similarity to a sencha card. Optimising it would converge the whole
collection onto one concept and call that improvement. A selection loop needs a
signal that cannot be won by copying an incumbent, so nothing in this module
looks at any other render.

THE THREE COMPONENTS
Each names a different failure, and each is measured against something outside
the population:

  adherence   cos(image, text(the prompt that made it)). Did FLUX render what
              was asked, or drift into a generic pretty frame? The reference is
              the prompt. CLIP's text tower is 77 tokens, so this reads the
              front of the prompt -- medium, movement, composition, subject --
              which is exactly the part a treatment sets; the LOWER boilerplate
              is identical on every card and sits at the tail, so losing it to
              truncation costs no discrimination.

  quality     zero-shot contrast between craft probes: six positive ("a
              beautiful professional illustration, well composed, clean
              confident drawing" and kin) against six negative ("a malformed
              amateur drawing, muddy, broken anatomy, garbled"). Several probes
              a side and averaged, because any single probe is a wording
              lottery.

  blank_lower the format constraint, measured in pixels instead of asked of a
              model. compose() stamps the product name, subtitle and quote over
              the bottom of the card, so art with detail down there is type on
              top of a drawing. Measured on this node's own output it is the
              LEAST satisfied of the three -- roughly 40% of sampled cards carry
              more edge energy in the type band than above it -- which makes it
              the most valuable thing to select on, not the least.

ART, NOT CARD
Everything is measured on the ART image (raw FLUX output), never the composed
card: compose() paints an opaque cream plate over the lower 40%, which would
turn blank_lower into a measurement of PIL and drag adherence toward "a picture
with a cream band". score() accepts either path and resolves .../cards/x.png to
.../art/x.png (or /home/dev/art/x.png) when that sibling exists.

    from aesthetic import score
    rows = score([p1, p2, ...], [prompt1, prompt2, ...])
    # -> [{path, adherence, quality, blank_lower, score, parts}, ...]

See CALIBRATION at the bottom for the measured distribution the constants come
from, and the argument for the weights.
"""
import math
import pathlib
import threading

import numpy as np
import torch
from PIL import Image
from transformers import CLIPModel, CLIPProcessor

MODEL_ID = "openai/clip-vit-base-patch32"
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

POS_PROBES = [
    "a beautiful professional illustration, well composed, clean confident drawing",
    "a striking print by a master illustrator, elegant composition, deliberate shapes",
    "a polished finished artwork with a clear focal point and confident linework",
    "a well drawn character illustration with correct anatomy and clean edges",
    "a rich detailed art print, harmonious colour, careful craftsmanship",
    "a beautiful poster design, balanced layout, crisp graphic shapes",
]
NEG_PROBES = [
    "a malformed amateur drawing, muddy, broken anatomy, garbled",
    "a sloppy unfinished sketch with mistakes and smeared shapes",
    "a cluttered incoherent picture with a confusing muddled composition",
    "a distorted figure with warped hands and a deformed face",
    "a dull washed out image with flat lifeless colour and no focal point",
    "a low quality generated image with artifacts and mangled detail",
]
PROBES = POS_PROBES + NEG_PROBES

# ---------------------------------------------------------------- calibration
# Constants below are the p5/p95 of 30 real cards sampled evenly across
# runs/gen-0001..gen-0223. Squashing to 0..1 before weighting is not cosmetic:
# raw cosine lives in 0.21-0.37 while the probe margin lives in 0.003-0.072, so
# an unnormalised sum would be cosine with a rounding error attached.

ADH_LO, ADH_HI = 0.24, 0.36      # cos(image, prompt), p5 / p95

# p_good = sigmoid(QUAL_TEMP * (margin - QUAL_MID)) where margin is
# mean(cos to positive probes) - mean(cos to negative probes). The offset is the
# whole point: CLIP's positive probes beat its negative probes on essentially
# every real render (margin > 0 for 30/30 of the sample), so the sign carries no
# information and CLIP's own logit_scale of 100 pins 75% of cards above p_good
# 0.96. Recentred on the corpus median it separates.
QUAL_MID, QUAL_TEMP = 0.040, 45.0

# blank_lower blends an absolute and a relative reading of the bottom third.
# Absolute alone would punish every densely-textured treatment (riso halftone,
# stained glass) for its medium; relative alone would punish sumi-e, whose
# enormous empty top makes any bottom look busy by comparison. Together they
# agree on the thing that actually matters -- a face or a border sitting where
# the type goes.
EDGE_LO, EDGE_HI = 5.0, 22.0     # absolute bottom-third edge energy, p5 / p95
RATIO_LO, RATIO_HI = 0.35, 1.25  # bottom-third edge / top-two-thirds edge

W_ADHERENCE = 0.25
W_QUALITY = 0.40
W_BLANK = 0.35

_lock = threading.Lock()
_model = None
_proc = None
_pos_emb = None
_neg_emb = None


def _feat(out):
    """The projected embedding, whichever shape the installed transformers gives.

    transformers 5 changed get_text_features / get_image_features to return a
    BaseModelOutputWithPooling whose pooler_output carries the projection;
    transformers 4 returned the tensor. Both are in the wild on this node.
    """
    if isinstance(out, torch.Tensor):
        return out
    po = getattr(out, "pooler_output", None)
    return po if po is not None else out[0]


def _unit(x):
    return x / x.norm(dim=-1, keepdim=True)


def load():
    """CLIP once, fp16, ~0.29 GiB -- small enough to sit beside a resident FLUX."""
    global _model, _proc, _pos_emb, _neg_emb
    if _model is None:
        _proc = CLIPProcessor.from_pretrained(MODEL_ID)
        _model = CLIPModel.from_pretrained(MODEL_ID).to(DEVICE, dtype=torch.float16).eval()
        with torch.no_grad():
            t = _proc(text=PROBES, return_tensors="pt", padding=True,
                      truncation=True).to(DEVICE)
            e = _unit(_feat(_model.get_text_features(**t)))
            _pos_emb, _neg_emb = e[:len(POS_PROBES)], e[len(POS_PROBES):]
    return _model, _proc


def art_path(path):
    """.../cards/x.png -> the raw render, when it is on disk.

    The composed card has an opaque plate over the bottom; scoring it would
    measure the compositor instead of the renderer.
    """
    p = pathlib.Path(path)
    if p.parent.name == "cards":
        for alt in (p.parent.parent / "art" / p.name,
                    pathlib.Path("/home/dev/art") / p.name):
            if alt.is_file():
                return alt
    return p


def bands(im):
    """(edge_top, edge_bottom, band_std) on a 256px-wide luma downscale.

    The split is at 2/3 of the height, which is where compose()'s opaque plate
    begins on a 448x672 card.
    """
    w = 256
    h = max(1, round(w * im.height / im.width))
    g = np.asarray(im.convert("RGB").resize((w, h)), dtype=np.float32).mean(-1)
    cut = int(h * 2 / 3)

    def energy(b):
        if b.size == 0:
            return 0.0
        dx = np.abs(np.diff(b, axis=1)).mean() if b.shape[1] > 1 else 0.0
        dy = np.abs(np.diff(b, axis=0)).mean() if b.shape[0] > 1 else 0.0
        return float(dx + dy)

    top, bot = g[:cut, :], g[cut:, :]
    return energy(top), energy(bot), float(bot.std() if bot.size else 0.0)


def _clamp01(x):
    return 0.0 if x < 0 else (1.0 if x > 1 else float(x))


def components(paths, prompts, chunk=16):
    """Raw, unweighted measurements. One CLIP image pass per chunk.

    Text embeddings are computed for the unique prompts only: a wave renders the
    same treatment against several concepts and the same concept under several
    treatments, so the prompt set is much smaller than the image set.
    """
    load()
    srcs = [art_path(p) for p in paths]
    rows = [{"path": str(p), "ok": False, "adherence_raw": 0.0, "margin": 0.0,
             "p_good": 0.0, "edge_top": 0.0, "edge_bot": 0.0, "ratio": 0.0,
             "band_std": 0.0} for p in srcs]

    imgs, idx = [], []
    for i, p in enumerate(srcs):
        try:
            im = Image.open(p)
            im.load()
            imgs.append(im.convert("RGB"))
            idx.append(i)
        except Exception as e:
            rows[i]["error"] = repr(e)
    if not imgs:
        return rows

    for i, im in zip(idx, imgs):
        et, eb, bs = bands(im)
        rows[i].update(edge_top=et, edge_bot=eb, band_std=bs,
                       ratio=eb / max(1e-6, et))

    keys = [(prompts[i] if i < len(prompts) else "") or "" for i in idx]
    keys = [k[:400] for k in keys]
    uniq = sorted(set(keys))
    tpos = {t: k for k, t in enumerate(uniq)}

    with _lock, torch.no_grad():
        ti = _proc(text=uniq, return_tensors="pt", padding=True,
                   truncation=True, max_length=77).to(DEVICE)
        temb = _unit(_feat(_model.get_text_features(**ti)))

        for s in range(0, len(imgs), chunk):
            part = imgs[s:s + chunk]
            part_idx, part_keys = idx[s:s + chunk], keys[s:s + chunk]
            pv = _proc(images=part, return_tensors="pt")["pixel_values"]
            iemb = _unit(_feat(_model.get_image_features(
                pixel_values=pv.to(DEVICE, dtype=torch.float16))))
            adh = (iemb @ temb.T).float().cpu().numpy()
            mp = (iemb @ _pos_emb.T).float().mean(-1).cpu().numpy()
            mn = (iemb @ _neg_emb.T).float().mean(-1).cpu().numpy()
            for r, (i, k) in enumerate(zip(part_idx, part_keys)):
                margin = float(mp[r] - mn[r])
                rows[i]["adherence_raw"] = float(adh[r, tpos[k]])
                rows[i]["margin"] = margin
                rows[i]["p_good"] = 1.0 / (1.0 + math.exp(-QUAL_TEMP * (margin - QUAL_MID)))
                rows[i]["ok"] = True
    return rows


def score(paths, prompts, weights=None):
    """[{path, adherence, quality, blank_lower, score, parts}, ...]

    Named fields are the normalised 0..1 components; `parts` carries the raw
    measurements so a caller can re-weight or debug without a second GPU pass.
    An unreadable image scores 0.0 rather than raising -- a scorer must never be
    the reason a wave dies.
    """
    wa, wq, wb = weights or (W_ADHERENCE, W_QUALITY, W_BLANK)
    out = []
    for r in components(paths, prompts):
        adh = _clamp01((r["adherence_raw"] - ADH_LO) / (ADH_HI - ADH_LO))
        qual = _clamp01(r["p_good"])
        b_abs = _clamp01((EDGE_HI - r["edge_bot"]) / (EDGE_HI - EDGE_LO))
        b_rel = _clamp01((RATIO_HI - r["ratio"]) / (RATIO_HI - RATIO_LO))
        blank = 0.5 * b_abs + 0.5 * b_rel
        total = 0.0 if not r["ok"] else (wa * adh + wq * qual + wb * blank)
        out.append({
            "path": r["path"],
            "adherence": round(adh, 4),
            "quality": round(qual, 4),
            "blank_lower": round(blank, 4),
            "score": round(total, 4),
            "parts": {"adherence_raw": round(r["adherence_raw"], 4),
                      "margin": round(r["margin"], 4),
                      "p_good": round(r["p_good"], 4),
                      "edge_top": round(r["edge_top"], 3),
                      "edge_bot": round(r["edge_bot"], 3),
                      "ratio": round(r["ratio"], 3),
                      "band_std": round(r["band_std"], 2),
                      "blank_abs": round(b_abs, 4),
                      "blank_rel": round(b_rel, 4),
                      "ok": r["ok"],
                      "weights": [wa, wq, wb]},
        })
    return out


CALIBRATION = """
30 cards sampled evenly across runs/gen-0001..gen-0223 (216 parseable cards;
every one resolved to its raw art). Raw per-component distribution:

                  min      p5      p25     med     p75     p95     max
  cos(img,prompt) 0.2107  0.2398  0.2775  0.3143  0.3301  0.3600  0.3679
  probe margin    0.0031  0.0106  0.0339  0.0480  0.0584  0.0671  0.0719
  edge_bottom     5.23    7.53    13.52   16.90   19.92   22.36   26.92
  edge_bot/top    0.345   0.489   0.704   0.813   1.087   1.821   3.424

Two things in that table set the design:

* CLIP's own softmax is useless here. With logit_scale 100 the positive side
  takes p_good 0.96+ on three quarters of the sample and 0.99+ on half; the
  ordering survives but the magnitude does not, so a weighted sum would be
  dominated by whichever component still had range. Recentring on the corpus
  median (QUAL_MID 0.040) and softening the temperature (45) restores it.
* the blank lower third is the format's most-violated rule, not its safest.
  Median ratio 0.81 and a quarter of the sample above 1.0 means FLUX routinely
  puts a face, a border or a botanical straight into the type band. That earns
  it a heavy weight (0.35) -- it is the component with the most headroom, and
  the one the operator would notice first.

Weights 0.25 adherence / 0.40 quality / 0.35 blank_lower:
* quality leads because it is the only term that reads craft directly, but it
  does not take a majority -- it is also the term most easily satisfied by a
  bland, safe picture.
* blank_lower is close behind for the reason above.
* adherence is the anti-collapse term. Without it a treatment that quietly
  ignores its own medium (a "sumi-e" that renders as generic anime) still wins
  on p_good, and a bandit will happily drive all 14 treatments into one look.
"""

if __name__ == "__main__":
    import json
    import sys

    ps = sys.argv[1:]
    print(json.dumps(score(ps, [""] * len(ps)), indent=2))
