#!/usr/bin/env python3
"""The Arcane Fortiche aesthetic, made measurable.

WHY THIS EXISTS
docs/ARCANE_LATENT_CARTOGRAPHY_PROTOCOL_SPEC.md section 3 ratifies six aesthetic
invariants that every generated cell is supposed to satisfy -- impasto, planar
silhouette, dual-source chiaroscuro, the two realm palettes, and a hard
anti-plastic-CGI filter. Until this module none of the six were checked by any
code anywhere in the repo. The spec asserted them; nothing enforced them.

WHAT THIS IS, AND WHAT IT DELIBERATELY IS NOT
This is the OBJECTIVE FLOOR under the jury's subjective judgement: pure numpy
and Pillow, no model, no weights, no CUDA. It runs on a laptop with no GPU and
it runs identically every time. The three-shard jury above it
(RedHatAI/pixtral-12b-quantized.w4a16 for palette/medium/lighting,
unsloth/Qwen3.8-27B-NVFP4 for structure, nvidia/Gemma-4-31B-IT-NVFP4 for
synthesis) is where nuance lives; every one of those is 4-bit quantised and
therefore somewhat less precise than full precision. That is an argument for
this floor being rigorous, not for it being clever. When a quantised juror says
9.5 and this module says the frame is airbrushed and spectrally dead, this
module is the one with the receipts.

It is NOT a Fortiche classifier and must not be sold as one. See CALIBRATION.

THE ONE RULE, borrowed from atelier/aesthetic.py
That module refuses to build a quality signal that can be won by copying an
incumbent, because a selection loop optimised against its own population
collapses onto whatever it already made. The same rule binds here: every
measurement below is taken against an EXTERNAL reference -- the spec's stated
invariants, fixed palette anchors, absolute spectral and geometric properties.
Nothing here looks at any other frame in the population. Novelty and mode
collapse belong to sensory_gates.py and uniqueness_tracker.py. This module only
ever asks "does this frame satisfy the specification", never "is this frame
different from the last one".

THE SIX MEASUREMENTS

  impasto      Broadband high-frequency energy, times how directionally
               ORGANISED that energy is. Paint is anisotropic: strokes run.
               Photographic grain and render noise are isotropic, and an
               airbrushed surface has no high band at all. Energy alone would
               reward noise; anisotropy alone would reward a blurred frame
               whose only surviving structure is a few hair strands. The
               product rejects both.

  planarity    Hard tonal breaks per unit area, structure-tensor coherence, and
               concentration of the gradient-orientation histogram into a few
               dominant angles. Facets meet at edges; spheres do not.

  chiaroscuro  Bimodality of the luminance histogram -- an actual valley
               between a shadow population and a lit population, weighted by
               how far apart the two modes sit -- plus dynamic range, plus rim
               evidence: whether the brightest 3% of the frame sits on hard
               gradients (a rim carving an edge) or in flat pools (bloom, sky,
               a blown highlight).

  palette_*    CIELAB distance from the frame's own dominant palette to each
               realm's fixed anchor set, blended with the pixel fraction
               actually carrying the realm's signature hue. Anchors are in
               arcane_prompts.toml with provenance flags.

  anti_cgi     The rejection gate, scored so HIGH means successfully NOT
               plastic. Four tells: large low-variance blocks inside a
               skin-chroma mask; absence of hard tonal breaks ON the skin
               (a painted face facets, a rendered face ramps); large flat
               mid-luminance blocks anywhere; and spectral poverty.

RESOLUTION
Every frame is resampled to a fixed WORK_SIDE before measurement, so a 512px
scout and a 1024px final are read under identical conditions. Every spatial
parameter is a fraction of the frame, not a pixel count. Upsampling a small
render adds no high-frequency content, so a 512px cell will score lower on
impasto than the same content rendered at 1024. That is intended: it genuinely
has less brush detail. Aspect ratio matters for the same reason: a 1600x400
frame is squeezed harder than a square one and its detail concentrates, which
measured +17 points of fortiche_score on one test frame. Compare within one
render size AND one aspect ratio.

GENERATOR PRECISION
Calibrated against BF16 output from black-forest-labs/FLUX.1-dev. impasto and
anti_cgi read exactly the fine-texture band that low-bit generator quantisation
destroys first, so a Q4 generator would depress both and these thresholds would
no longer mean what they say. The pipeline keeps the generator at BF16 for that
reason. If that ever changes, recalibrate before trusting a number here.

USAGE
    from arcane_aesthetic import conformance, arcane_prompt, views
    conformance("cell_0007.png")
    arcane_prompt("italian princess", realm="zaun", view="three-quarter", rose=True)
    views(64)

    python3 arcane_aesthetic.py            # calibration table over apps/**/*.png
    python3 arcane_aesthetic.py a.png b.png

See CALIBRATION at the bottom for the measured distribution these constants
come from, the sample size, and an explicit statement of what is NOT evidenced.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import pathlib
import sys

import numpy as np
from PIL import Image

try:  # stdlib on 3.11+
    import tomllib as _toml
except ImportError:  # pragma: no cover - 3.10 and older
    try:
        import tomli as _toml  # type: ignore
    except ImportError:
        _toml = None  # type: ignore

# Sibling pipeline modules are owned by other agents and may not exist yet.
# This module must import and run standalone on a laptop with no pipeline.
try:  # pragma: no cover - optional
    import pipeline_paths as _pipeline_paths  # type: ignore
except ImportError:  # pragma: no cover
    _pipeline_paths = None

__all__ = [
    "FORTICHE_RUBRIC", "FORTICHE_RUBRIC_STRUCTURAL", "ZAUN", "PILTOVER",
    "REALMS", "conformance", "features", "arcane_prompt", "views",
    "CALIBRATION",
]

HERE = pathlib.Path(__file__).resolve().parent
CORPUS_PATH = HERE / "arcane_prompts.toml"


# ===========================================================================
#  MEASUREMENT GEOMETRY
# ===========================================================================
# Longest side every frame is resampled to before anything is measured. 768 is
# a compromise: high enough that the 0.35-0.75 normalised-frequency band still
# carries real brush detail, low enough that a 64-cell orbit scores in seconds
# on a CPU. Changing it invalidates every constant below.
WORK_SIDE = 768

# Spatial scales, all expressed as a fraction of the frame's geometric mean
# side so they mean the same thing at any input resolution.
_R_FINE = 1.0 / 256.0    # high-pass radius: separates brush detail from form
_R_LOCAL = 1.0 / 64.0    # window for local texture variance
_R_STROKE = 1.0 / 100.0  # window for stroke-direction coherence
_STEP = 1.0 / 400.0      # gradient step, so |grad| is L* per 1/400 frame width
_BLOCK = 1.0 / 96.0      # block size for "large flat region" tests


# ===========================================================================
#  NORMALISATION CONSTANTS
#  Each raw measurement is squashed to 0..1 by (x - LO) / (HI - LO), clamped.
#  LO and HI are documented against the measured corpus in CALIBRATION.
# ===========================================================================

# --- impasto ---------------------------------------------------------------
# BROAD: share of windowed FFT power above normalised radius 0.10. A perfectly
# airbrushed frame measured 0.04; detailed FLUX renders sit 0.20-0.42; the
# synthetic broadband-paint control reached 0.60.
BROAD_LO, BROAD_HI = 0.08, 0.55
# TEX: fraction of pixels whose high-pass residual exceeds 0.75 L* -- how much
# of the canvas is carrying fine incident, rather than just the subject edges.
TEX_LO, TEX_HI = 0.08, 0.85
# ANISO: energy-weighted structure-tensor coherence OF THE HIGH-PASS RESIDUAL.
# Isotropic noise measured 0.10, an airbrushed frame 0.09, real renders
# 0.24-0.60, the faceted paint control 0.54.
ANISO_LO, ANISO_HI = 0.15, 0.60
W_IMP_BROAD, W_IMP_TEX = 0.45, 0.55   # inside the energy term
IMP_ANISO_FLOOR = 0.45                # energy * (FLOOR + (1-FLOOR)*aniso)

# --- planarity -------------------------------------------------------------
# HB: fraction of pixels whose gradient exceeds HARD_BREAK_L* per step -- the
# density of hard tonal breaks, i.e. facet boundaries.
HARD_BREAK_L = 2.0
HB_LO, HB_HI = 0.02, 0.45
# COH: structure-tensor coherence of the raw luminance. A plane boundary is
# locally one orientation; a sphere's terminator is too, so this is the weakest
# of the three and is weighted accordingly.
COH_LO, COH_HI = 0.25, 0.62
# OC: 1 - normalised entropy of the magnitude-weighted 36-bin gradient
# orientation histogram. Facet art concentrates into a few angles.
OC_LO, OC_HI = 0.005, 0.09
W_PLN_HB, W_PLN_COH, W_PLN_OC = 0.40, 0.30, 0.30

# --- chiaroscuro -----------------------------------------------------------
# DIP: 1 - (histogram density in the Otsu valley) / (taller mode's density).
DIP_LO, DIP_HI = 0.10, 0.85
# GAP: L* distance between the two class means either side of the Otsu cut.
GAP_LO, GAP_HI = 22.0, 52.0
# DR: p99 - p1 of L*.
DR_LO, DR_HI = 45.0, 95.0
# RIM: mean gradient magnitude over the brightest 3% of pixels, divided by the
# frame's mean gradient magnitude. Below 1.0 the highlights sit in flat pools
# (bloom, sky, a blown background); above 2 they sit on hard edges (a rim).
RIM_LO, RIM_HI = 0.80, 2.60
W_CHI_BIMOD, W_CHI_DR, W_CHI_RIM = 0.45, 0.25, 0.30

# --- palette ---------------------------------------------------------------
PALETTE_K = 12            # median-cut palette size
SIGMA_CHROMA = 28.0       # dE76 falloff for chromatic anchors
SIGMA_NEUTRAL = 13.0      # tighter: every grey is near a neutral anchor
PAL_L_FLOOR, PAL_L_KNEE = 6.0, 16.0   # crushed black carries no palette info
ACCENT_HUE_DEG = 22.0     # hue half-window for signature-hue pixels
ACCENT_CHROMA_MIN = 14.0  # ignore near-grey pixels when counting the accent
ACCENT_TARGET = 0.030     # 3% of the frame in the signature hue saturates it
W_PAL_ANCHOR, W_PAL_ACCENT = 0.70, 0.30
REALM_MARGIN = 10.0       # points between the realms; below this -> "mixed"
REALM_MIN_FIT = 30.0      # naming a realm at all requires this much fit

# --- anti_cgi (higher = less plastic) --------------------------------------
# SKIN_SMOOTH: fraction of the frame in blocks that are >=80% skin-chroma AND
# whose local high-frequency std is under SKIN_FLAT_HF. This is the spec's
# "smooth skin" clause, measured.
SKIN_FLAT_HF = 0.60
SS_LO, SS_HI = 0.005, 0.18
# SKIN_BREAK: fraction of skin pixels carrying a hard tonal break. A painted
# face facets; a rendered face is tonally continuous however detailed its pores
# are. Only trusted when the skin mask covers at least SKIN_MIN_AREA.
SKIN_MIN_AREA = 0.05
SB_LO, SB_HI = 0.06, 0.55
# FLAT: fraction of the frame in large mid-luminance blocks with no fine
# structure -- the "flat photographic CGI" clause, and airbrush generally.
FLAT_LO, FLAT_HI = 0.01, 0.30
W_CGI_SKIN_SMOOTH, W_CGI_SKIN_BREAK, W_CGI_FLAT, W_CGI_POVERTY = (
    0.34, 0.28, 0.20, 0.18)

# ===========================================================================
#  COMPOSITE WEIGHTS
# ===========================================================================
# Justified in the conformance() docstring. Sum to 1.0.
W_ANTI_CGI = 0.30
W_IMPASTO = 0.25
W_CHIAROSCURO = 0.20
W_PLANARITY = 0.15
W_PALETTE = 0.10

# ===========================================================================
#  VERDICT THRESHOLDS -- PROVISIONAL, see CALIBRATION
#  These are the numbers to retune first. Nothing else in the module needs to
#  change to move where the line sits.
# ===========================================================================
# CONFORMING_MIN is the one number here with a real lower bound and no upper
# bound. The best non-Fortiche frame in the calibration sample scored 71.3, so
# anything at or below that would certify a frame a human reads as smooth CGI.
# 72.0 sits just above that floor. Nothing in the sample reaches it, and that
# is deliberate: there is no confirmed-Fortiche frame in this repo to place the
# line against from the other side. Treat "conforming" as unproven and rank by
# fortiche_score until a real Fortiche render exists to measure.
CONFORMING_MIN = 72.0
# DRIFT_MIN is bracketed from both sides by measurement: band-limited isotropic
# noise scored 39.4 and the weakest real render scored 42.3. 42.0 is in the gap.
DRIFT_MIN = 42.0        # at or above this -> "drift"; below -> "reject"
ANTI_CGI_FLOOR = 35.0   # hard gate: the spec says "hard rejection", so this is
                        # a gate and not a term. Below it the verdict is
                        # "reject" whatever the composite says.

# Per-dimension floors used only to generate human-readable `reasons`.
REASON_FLOORS = {
    "impasto": 45.0,
    "planarity": 40.0,
    "chiaroscuro": 45.0,
    "anti_cgi": 55.0,
    "realm_fit": 40.0,
}


# ===========================================================================
#  CORPUS
# ===========================================================================
_CORPUS: dict | None = None


def _corpus() -> dict:
    """arcane_prompts.toml, parsed once.

    A missing or unparseable corpus is fatal for the prompt side and harmless
    for the pixel side, so the fallback keeps only what the pixel metrics need:
    the two signature hexes the spec writes out longhand.
    """
    global _CORPUS
    if _CORPUS is None:
        data = None
        if _toml is not None and CORPUS_PATH.is_file():
            with open(CORPUS_PATH, "rb") as fh:
                data = _toml.load(fh)
        if not data:
            data = _FALLBACK_CORPUS
        _CORPUS = data
    return _CORPUS


_FALLBACK_CORPUS = {
    "realm": {
        "zaun": {
            "key": "zaun", "name": "Zaun Undercity",
            "signature_hex": "#00ff88",
            "signature_name": "toxic chemtech emerald",
            "palette": [
                {"hex": "#00ff88", "name": "toxic chemtech emerald",
                 "spec": True, "neutral": False},
                {"hex": "#7a4626", "name": "rusted iron",
                 "spec": True, "neutral": False},
                {"hex": "#6a3fa0", "name": "bruised violet",
                 "spec": True, "neutral": False},
            ],
        },
        "piltover": {
            "key": "piltover", "name": "Piltover Apex",
            "signature_hex": "#00d2ff", "signature_name": "hextech cyan",
            "palette": [
                {"hex": "#00d2ff", "name": "hextech cyan",
                 "spec": True, "neutral": False},
                {"hex": "#c9a227", "name": "gilded brass",
                 "spec": True, "neutral": False},
                {"hex": "#f2efe6", "name": "white marble",
                 "spec": True, "neutral": True},
            ],
        },
    },
    "character": {},
    "rubric": {"pixtral": "", "qwen_structural": ""},
}


def _hex_to_lab(h: str) -> np.ndarray:
    h = h.strip().lstrip("#")
    rgb = np.array([[int(h[i:i + 2], 16) for i in (0, 2, 4)]],
                   dtype=np.float64) / 255.0
    return _srgb_to_lab(rgb)[0]


def _build_realm(spec: dict) -> dict:
    pal = spec.get("palette", [])
    anchors = np.array([_hex_to_lab(p["hex"]) for p in pal], dtype=np.float64) \
        if pal else np.zeros((0, 3))
    sig = spec.get("signature_hex", "#000000")
    sig_lab = _hex_to_lab(sig)
    out = {
        "key": spec.get("key", "?"),
        "name": spec.get("name", "?"),
        "signature": sig,
        "signature_name": spec.get("signature_name", ""),
        "anchors": {p["name"]: p["hex"] for p in pal},
        "palette": pal,
        "palette_words": spec.get("palette_words", []),
        "style": spec.get("style", []),
        "light": spec.get("light", []),
        "setting": spec.get("setting", []),
    }
    out["_lab"] = anchors
    out["_neutral"] = np.array([bool(p.get("neutral", False)) for p in pal],
                               dtype=bool)
    out["_sig_lab"] = sig_lab
    out["_sig_hue"] = math.degrees(math.atan2(sig_lab[2], sig_lab[1]))
    return out


# ===========================================================================
#  NUMERIC PRIMITIVES
# ===========================================================================
def _srgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """sRGB in 0..1 -> CIELAB (D65). Shape (..., 3) in, (..., 3) out."""
    lin = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    m = np.array([[0.4124564, 0.3575761, 0.1804375],
                  [0.2126729, 0.7151522, 0.0721750],
                  [0.0193339, 0.1191920, 0.9503041]], dtype=np.float64)
    xyz = lin @ m.T
    t = xyz / np.array([0.95047, 1.0, 1.08883])
    d = 6.0 / 29.0
    f = np.where(t > d ** 3, np.cbrt(np.maximum(t, 0.0)), t / (3 * d * d) + 4.0 / 29.0)
    return np.stack([116 * f[..., 1] - 16,
                     500 * (f[..., 0] - f[..., 1]),
                     200 * (f[..., 1] - f[..., 2])], axis=-1)


def _box1d(a: np.ndarray, r: int, axis: int) -> np.ndarray:
    n = a.shape[axis]
    k = 2 * r + 1
    pad = [(0, 0)] * a.ndim
    pad[axis] = (r + 1, r)
    c = np.cumsum(np.pad(a, pad, mode="edge"), axis=axis, dtype=np.float64)
    hi = [slice(None)] * a.ndim
    hi[axis] = slice(k, k + n)
    lo = [slice(None)] * a.ndim
    lo[axis] = slice(0, n)
    return ((c[tuple(hi)] - c[tuple(lo)]) / k).astype(np.float32)


def _blur(a: np.ndarray, r: int) -> np.ndarray:
    """Three box passes ~= a Gaussian of sigma ~ r. O(N) and dependency-free."""
    if r < 1:
        return a.astype(np.float32)
    out = a.astype(np.float32)
    for _ in range(3):
        out = _box1d(out, r, 0)
        out = _box1d(out, r, 1)
    return out


def _grad(a: np.ndarray, step: int):
    """Central difference over `step` px, scaled to units per step.

    Dividing by the step is what makes the gradient scale-invariant: the same
    edge measured at any resolution yields the same magnitude, provided `step`
    is a fixed fraction of the frame.
    """
    gx = (np.roll(a, -step, 1) - np.roll(a, step, 1)) / (2.0 * step)
    gy = (np.roll(a, -step, 0) - np.roll(a, step, 0)) / (2.0 * step)
    return gx, gy


def _coherence(gx, gy, r) -> float:
    """Energy-weighted structure-tensor coherence, (l1-l2)/(l1+l2) in 0..1."""
    jxx = _blur(gx * gx, r)
    jyy = _blur(gy * gy, r)
    jxy = _blur(gx * gy, r)
    tr = jxx + jyy
    disc = np.sqrt(np.maximum((jxx - jyy) ** 2 + 4.0 * jxy * jxy, 0.0))
    return float((disc).sum() / (tr.sum() + 1e-9))


def _n01(x: float, lo: float, hi: float) -> float:
    v = (x - lo) / (hi - lo)
    return 0.0 if v < 0.0 else (1.0 if v > 1.0 else float(v))


def _load(path) -> Image.Image:
    im = Image.open(path)
    im.load()
    im = im.convert("RGB")
    s = WORK_SIDE / float(max(im.size))
    if abs(s - 1.0) > 1e-3:
        im = im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))),
                       Image.LANCZOS)
    return im


# ===========================================================================
#  RAW FEATURE EXTRACTION
# ===========================================================================
def features(image_path) -> dict:
    """Every raw, un-normalised measurement, for calibration and debugging.

    conformance() calls this and then squashes. Exposed separately so the
    constants above can be retuned from data without re-deriving anything.
    """
    im = _load(image_path)
    rgb = np.asarray(im, dtype=np.float32) / 255.0
    lab = _srgb_to_lab(rgb.astype(np.float64)).astype(np.float32)
    L, A, B = lab[..., 0], lab[..., 1], lab[..., 2]
    h, w = L.shape
    side = math.sqrt(h * w)

    r_fine = max(1, round(side * _R_FINE))
    r_local = max(2, round(side * _R_LOCAL))
    r_stroke = max(2, round(side * _R_STROKE))
    step = max(1, round(side * _STEP))
    bs = max(4, round(side * _BLOCK))

    f: dict = {"work_px": f"{w}x{h}"}

    # ---- fine texture ------------------------------------------------------
    hp = L - _blur(L, r_fine)
    hf_var = _blur(hp * hp, r_local) - _blur(hp, r_local) ** 2
    hf = np.sqrt(np.maximum(hf_var, 0.0))
    f["tex_density"] = float((np.abs(hp) > 0.75).mean())
    f["hf_median"] = float(np.median(hf))
    # Diagnostic only. See CALIBRATION for why this is NOT scored.
    p90, p50, p10 = (float(np.percentile(hf, q)) for q in (90, 50, 10))
    f["dof_spread"] = (p90 - p10) / (p50 + 0.25)

    # ---- windowed radial power spectrum ------------------------------------
    n = 512
    g = np.asarray(im.convert("L").resize((n, n), Image.LANCZOS), dtype=np.float32)
    win = np.hanning(n).astype(np.float32)
    g = (g - g.mean()) * win[:, None] * win[None, :]
    P = np.abs(np.fft.fftshift(np.fft.fft2(g))) ** 2
    yy, xx = np.ogrid[:n, :n]
    rad = np.sqrt((xx - n / 2.0) ** 2 + (yy - n / 2.0) ** 2) / (n / 2.0)
    keep = (rad > 0.02) & (rad <= 1.0)
    tot = float(P[keep].sum()) + 1e-12
    f["broadband"] = float(P[(rad > 0.10) & (rad <= 1.0)].sum() / tot)

    # ---- gradients ---------------------------------------------------------
    gx, gy = _grad(L, step)
    mag = np.sqrt(gx * gx + gy * gy)
    f["hard_break"] = float((mag > HARD_BREAK_L).mean())
    f["coherence"] = _coherence(gx, gy, max(1, r_fine * 2))

    ang = (np.arctan2(gy, gx) + np.pi) % np.pi
    sel = mag >= np.percentile(mag, 70)
    hist, _ = np.histogram(ang[sel], bins=36, range=(0.0, math.pi),
                           weights=mag[sel])
    p = hist / (hist.sum() + 1e-9)
    nz = p[p > 0]
    f["orient_conc"] = float(1.0 + (nz * np.log(nz)).sum() / math.log(36.0))

    hgx, hgy = _grad(hp, step)
    f["stroke_aniso"] = _coherence(hgx, hgy, r_stroke)

    # ---- luminance distribution -------------------------------------------
    cnt, _ = np.histogram(L, bins=64, range=(0.0, 100.0))
    pr = cnt.astype(np.float64) / max(cnt.sum(), 1)
    centers = (np.arange(64) + 0.5) * (100.0 / 64.0)
    cw = np.cumsum(pr)
    cm = np.cumsum(pr * centers)
    gmean = cm[-1]
    den = cw * (1.0 - cw)
    between = np.where(den > 1e-9, (gmean * cw - cm) ** 2 / np.maximum(den, 1e-9), 0.0)
    t = int(np.argmax(between))
    smooth = np.convolve(pr, np.array([0.06, 0.24, 0.40, 0.24, 0.06]), mode="same")
    lo_peak = float(smooth[:max(t, 1)].max()) if t > 0 else 0.0
    hi_peak = float(smooth[t + 1:].max()) if t + 1 < 64 else 0.0
    valley = float(smooth[max(0, t - 1):t + 2].min())
    f["dip"] = 1.0 - valley / (max(lo_peak, hi_peak) + 1e-9)
    m0 = float((pr[:t + 1] * centers[:t + 1]).sum() / (pr[:t + 1].sum() + 1e-9))
    m1 = float((pr[t + 1:] * centers[t + 1:]).sum() / (pr[t + 1:].sum() + 1e-9))
    f["mode_gap"] = m1 - m0
    f["dyn_range"] = float(np.percentile(L, 99) - np.percentile(L, 1))

    bright = L >= np.percentile(L, 97)
    f["rim_ratio"] = float(mag[bright].mean() / (mag.mean() + 1e-6))

    # ---- skin -------------------------------------------------------------
    # Two gates, because Arcane skin is routinely lit by a coloured key: warm
    # flesh, and desaturated cool-lit flesh. Anything outside both is simply
    # not tested for smoothness, which is the conservative direction for a
    # rejection gate.
    C = np.sqrt(A * A + B * B)
    H = np.degrees(np.arctan2(B, A))
    warm = (L > 25) & (L < 96) & (C > 4) & (C < 48) & (H > -25) & (H < 105)
    cool = (L > 25) & (L < 96) & (C > 2) & (C < 26) & ((H > 150) | (H < -100))
    skin = warm | cool
    f["skin_area"] = float(skin.mean())
    f["skin_break"] = (float((mag[skin] > HARD_BREAK_L).mean())
                       if skin.sum() > 400 else float("nan"))

    hh, ww = (h // bs) * bs, (w // bs) * bs

    def _blocks(m):
        return m[:hh, :ww].reshape(hh // bs, bs, ww // bs, bs).mean(axis=(1, 3))

    hf_b = _blocks(hf)
    skin_b = _blocks(skin.astype(np.float32)) >= 0.80
    mid_b = _blocks(((L > 18) & (L < 92)).astype(np.float32)) >= 0.80
    f["skin_smooth"] = float((skin_b & (hf_b < SKIN_FLAT_HF)).mean())
    f["flat_area"] = float((mid_b & (hf_b < SKIN_FLAT_HF)).mean())

    # ---- palette ----------------------------------------------------------
    # A frame with few distinct colours quantizes to FEWER than PALETTE_K
    # entries, so the palette and the bin counts must be truncated to their
    # common length rather than assumed to be PALETTE_K long.
    q = im.quantize(colors=PALETTE_K, method=Image.Quantize.MEDIANCUT, kmeans=0)
    raw = (q.getpalette() or [])[: PALETTE_K * 3]
    pal_rgb = np.array(raw, dtype=np.float64).reshape(-1, 3) / 255.0
    counts = np.bincount(np.asarray(q, dtype=np.int32).ravel(),
                         minlength=PALETTE_K)[:PALETTE_K].astype(np.float64)
    k = min(len(pal_rgb), len(counts))
    if k == 0:
        f["_pal_lab"] = np.zeros((0, 3))
        f["_pal_w"] = np.zeros(0)
    else:
        f["_pal_lab"] = _srgb_to_lab(pal_rgb[:k])
        f["_pal_w"] = counts[:k] / max(counts[:k].sum(), 1.0)
    f["_L"], f["_C"], f["_H"] = L, C, H
    return f


def _palette_scores(f: dict, realm: dict) -> tuple:
    """(anchor_affinity, accent_fraction) for one realm, both 0..1-ish."""
    plab, pw = f["_pal_lab"], f["_pal_w"]
    anchors, neutral = realm["_lab"], realm["_neutral"]
    total = weight = 0.0
    for i in range(min(len(plab), len(pw))):
        # Crushed black is the same in every palette; it carries no evidence.
        gate = _n01(plab[i, 0], PAL_L_FLOOR, PAL_L_KNEE)
        wt = float(pw[i]) * gate
        if wt <= 0.0 or len(anchors) == 0:
            continue
        d = np.sqrt(((plab[i] - anchors) ** 2).sum(axis=1))
        sig = np.where(neutral, SIGMA_NEUTRAL, SIGMA_CHROMA)
        total += wt * float(np.exp(-((d / sig) ** 2)).max())
        weight += wt
    affinity = total / (weight + 1e-9)

    dh = np.abs(((f["_H"] - realm["_sig_hue"] + 180.0) % 360.0) - 180.0)
    accent = float(((dh < ACCENT_HUE_DEG) & (f["_C"] > ACCENT_CHROMA_MIN)).mean())
    return affinity, accent


# ===========================================================================
#  THE CONTRACT
# ===========================================================================
def conformance(image_path: str) -> dict:
    """Pure-pixel Fortiche conformance. Never needs a model.

    Returns
        {"impasto": 0-100, "planarity": 0-100, "chiaroscuro": 0-100,
         "palette_zaun": 0-100, "palette_piltover": 0-100,
         "realm": "zaun"|"piltover"|"mixed", "anti_cgi": 0-100,
         "fortiche_score": 0-100, "verdict": "conforming"|"drift"|"reject",
         "reasons": [str], "path": str, "details": {...}}

    `path` and `details` are additions to the briefed contract; `details`
    carries every raw measurement so a caller can retune without a second pass.
    Everything else is exactly the briefed key set and value range.

    COMPOSITE WEIGHTS
        anti_cgi     0.30  The spec phrases this one dimension as a HARD
                           REJECTION rather than a preference, so it leads. It
                           is also the dimension with the most headroom on real
                           output -- every frame measured in CALIBRATION fails
                           it to some degree -- and per atelier/aesthetic.py's
                           argument the most-violated rule is the most valuable
                           thing to select on, not the safest.
        impasto      0.25  The single most identifying Fortiche trait: it is
                           what makes a frame read as painted rather than
                           rendered. Second because, unlike anti_cgi, a frame
                           can carry real texture and still be wrong.
        chiaroscuro  0.20  The second most identifying trait, and the one FLUX
                           actually responds to from a prompt, so it earns a
                           real weight without leading.
        planarity    0.15  Honestly measured but the noisiest of the four
                           geometric terms, because it reads the whole frame
                           and the spec's constraint is about the FACE. Weighted
                           in proportion to confidence in it, not importance.
        palette      0.10  Required by the spec, but it is the dimension a
                           prompt satisfies most trivially and the one most
                           contaminated by subject matter -- a crimson gown is
                           neither realm. It uses whichever realm scores
                           higher, so committing to one side of the world
                           matrix is never punished.

    A hard gate sits over the composite: anti_cgi below ANTI_CGI_FLOOR forces
    "reject" regardless of score, because the spec says "hard rejection" and a
    gate is not a weighted term.

    An unreadable image returns a zeroed verdict rather than raising. A scorer
    must never be the reason a wave dies.
    """
    try:
        f = features(image_path)
    except Exception as exc:  # unreadable, truncated, not an image
        return {
            "impasto": 0.0, "planarity": 0.0, "chiaroscuro": 0.0,
            "palette_zaun": 0.0, "palette_piltover": 0.0, "realm": "mixed",
            "anti_cgi": 0.0, "fortiche_score": 0.0, "verdict": "reject",
            "reasons": [f"unreadable image: {exc!r}"],
            "path": str(image_path), "details": {"ok": False},
        }

    # ---- impasto ----------------------------------------------------------
    broad_n = _n01(f["broadband"], BROAD_LO, BROAD_HI)
    tex_n = _n01(f["tex_density"], TEX_LO, TEX_HI)
    aniso_n = _n01(f["stroke_aniso"], ANISO_LO, ANISO_HI)
    energy = W_IMP_BROAD * broad_n + W_IMP_TEX * tex_n
    impasto = 100.0 * energy * (IMP_ANISO_FLOOR + (1.0 - IMP_ANISO_FLOOR) * aniso_n)

    # ---- planarity --------------------------------------------------------
    hb_n = _n01(f["hard_break"], HB_LO, HB_HI)
    coh_n = _n01(f["coherence"], COH_LO, COH_HI)
    oc_n = _n01(f["orient_conc"], OC_LO, OC_HI)
    planarity = 100.0 * (W_PLN_HB * hb_n + W_PLN_COH * coh_n + W_PLN_OC * oc_n)

    # ---- chiaroscuro ------------------------------------------------------
    dip_n = _n01(f["dip"], DIP_LO, DIP_HI)
    gap_n = _n01(f["mode_gap"], GAP_LO, GAP_HI)
    bimod = dip_n * gap_n          # a valley is only evidence if the modes part
    dr_n = _n01(f["dyn_range"], DR_LO, DR_HI)
    rim_n = _n01(f["rim_ratio"], RIM_LO, RIM_HI)
    chiaroscuro = 100.0 * (W_CHI_BIMOD * bimod + W_CHI_DR * dr_n + W_CHI_RIM * rim_n)

    # ---- palettes ---------------------------------------------------------
    pal = {}
    for key, realm in REALMS.items():
        aff, acc = _palette_scores(f, realm)
        pal[key] = (100.0 * (W_PAL_ANCHOR * aff
                             + W_PAL_ACCENT * min(1.0, acc / ACCENT_TARGET)),
                    aff, acc)
    palette_zaun, palette_piltover = pal["zaun"][0], pal["piltover"][0]
    realm_fit = max(palette_zaun, palette_piltover)
    if (abs(palette_zaun - palette_piltover) < REALM_MARGIN
            or realm_fit < REALM_MIN_FIT):
        # Either the two realms are too close to call, or neither fits well
        # enough that naming one would be anything but over-claiming.
        realm_name = "mixed"
    else:
        realm_name = "zaun" if palette_zaun > palette_piltover else "piltover"

    # ---- anti_cgi ---------------------------------------------------------
    ss_n = _n01(f["skin_smooth"], SS_LO, SS_HI)
    flat_n = _n01(f["flat_area"], FLAT_LO, FLAT_HI)
    poverty = 1.0 - broad_n
    sb = f["skin_break"]
    if f["skin_area"] >= SKIN_MIN_AREA and not math.isnan(sb):
        smoothness = 1.0 - _n01(sb, SB_LO, SB_HI)
        skin_basis = "skin"
    else:
        # Too little skin to read: fall back to the whole-frame break density,
        # which asks the same question of whatever IS in the frame.
        smoothness = 1.0 - hb_n
        skin_basis = "frame"
    plasticity = (W_CGI_SKIN_SMOOTH * ss_n
                  + W_CGI_SKIN_BREAK * smoothness
                  + W_CGI_FLAT * flat_n
                  + W_CGI_POVERTY * poverty)
    anti_cgi = 100.0 * (1.0 - min(1.0, max(0.0, plasticity)))

    # ---- composite --------------------------------------------------------
    score = (W_ANTI_CGI * anti_cgi + W_IMPASTO * impasto
             + W_CHIAROSCURO * chiaroscuro + W_PLANARITY * planarity
             + W_PALETTE * realm_fit)

    if anti_cgi < ANTI_CGI_FLOOR:
        verdict = "reject"
    elif score >= CONFORMING_MIN:
        verdict = "conforming"
    elif score >= DRIFT_MIN:
        verdict = "drift"
    else:
        verdict = "reject"

    reasons = []
    if anti_cgi < ANTI_CGI_FLOOR:
        reasons.append(
            f"anti_cgi {anti_cgi:.0f} below the hard floor {ANTI_CGI_FLOOR:.0f}: "
            "the spec rejects smooth skin and flat photographic CGI outright")
    if anti_cgi < REASON_FLOORS["anti_cgi"]:
        if ss_n > 0.4:
            reasons.append(
                f"{f['skin_smooth'] * 100:.1f}% of the frame is large "
                "low-variance skin-chroma area: airbrushed, not painted")
        if smoothness > 0.6:
            reasons.append(
                f"almost no hard tonal breaks on the {skin_basis}: forms are "
                "tonally continuous the way a render is, not faceted the way "
                "paint is")
        if flat_n > 0.4:
            reasons.append(
                f"{f['flat_area'] * 100:.1f}% of the frame is flat mid-tone "
                "with no fine structure")
    if skin_basis == "frame":
        # Say so out loud. With too little skin to read, the smooth-skin clause
        # falls back to a whole-frame break density, which is the generous
        # direction: a frame full of hard-edged props can carry an airbrushed
        # face past this gate. The jury should weight anti_cgi lower here.
        reasons.append(
            f"anti_cgi measured frame-wide, not on skin (skin area "
            f"{f['skin_area'] * 100:.1f}% is below the "
            f"{SKIN_MIN_AREA * 100:.0f}% needed to read it): treat the "
            "smooth-skin clause as unverified for this frame")
    if impasto < REASON_FLOORS["impasto"]:
        if broad_n < 0.4:
            reasons.append(
                f"spectrally poor above r=0.10 (broadband {f['broadband']:.3f}): "
                "no paint layering in the fine band")
        if aniso_n < 0.4:
            reasons.append(
                f"fine texture is isotropic (aniso {f['stroke_aniso']:.2f}): "
                "reads as grain or render noise, not directional brushwork")
        reasons.append(
            f"impasto {impasto:.0f}: surface does not read as paint "
            f"(broadband {f['broadband']:.3f}, texture {f['tex_density']:.3f}, "
            f"stroke anisotropy {f['stroke_aniso']:.3f})")
    if planarity < REASON_FLOORS["planarity"]:
        reasons.append(
            f"planarity {planarity:.0f}: forms are rounded and isotropic, "
            f"hard-break density {f['hard_break']:.3f}, no dominant plane angles")
    if chiaroscuro < REASON_FLOORS["chiaroscuro"]:
        if bimod < 0.25:
            reasons.append(
                f"luminance is unimodal (dip {f['dip']:.2f}, mode gap "
                f"{f['mode_gap']:.0f} L*): single-source lighting, not dual")
        if rim_n < 0.3:
            reasons.append(
                f"no rim evidence (rim ratio {f['rim_ratio']:.2f}): the "
                "brightest 3% sits in flat pools, not on a carved edge")
    if realm_fit < REASON_FLOORS["realm_fit"]:
        reasons.append(
            f"palette commits to neither realm (zaun {palette_zaun:.0f} / "
            f"piltover {palette_piltover:.0f})")
    elif realm_name == "mixed":
        reasons.append(
            f"realm is mixed: zaun {palette_zaun:.0f} vs piltover "
            f"{palette_piltover:.0f} within the {REALM_MARGIN:.0f}-point margin")

    return {
        "impasto": round(impasto, 1),
        "planarity": round(planarity, 1),
        "chiaroscuro": round(chiaroscuro, 1),
        "palette_zaun": round(palette_zaun, 1),
        "palette_piltover": round(palette_piltover, 1),
        "realm": realm_name,
        "anti_cgi": round(anti_cgi, 1),
        "fortiche_score": round(score, 1),
        "verdict": verdict,
        "reasons": reasons,
        "path": str(image_path),
        "details": {
            "work_px": f["work_px"],
            "broadband": round(f["broadband"], 4),
            "tex_density": round(f["tex_density"], 4),
            "stroke_aniso": round(f["stroke_aniso"], 4),
            "hard_break": round(f["hard_break"], 4),
            "coherence": round(f["coherence"], 4),
            "orient_conc": round(f["orient_conc"], 4),
            "dip": round(f["dip"], 4),
            "mode_gap": round(f["mode_gap"], 2),
            "dyn_range": round(f["dyn_range"], 2),
            "rim_ratio": round(f["rim_ratio"], 3),
            "skin_area": round(f["skin_area"], 4),
            "skin_break": (None if math.isnan(f["skin_break"])
                           else round(f["skin_break"], 4)),
            "skin_smooth": round(f["skin_smooth"], 4),
            "flat_area": round(f["flat_area"], 4),
            "dof_spread": round(f["dof_spread"], 3),
            "skin_basis": skin_basis,
            "accent_zaun": round(pal["zaun"][2], 5),
            "accent_piltover": round(pal["piltover"][2], 5),
            "anchor_zaun": round(pal["zaun"][1], 4),
            "anchor_piltover": round(pal["piltover"][1], 4),
            "parts": {
                "broad_n": round(broad_n, 3), "tex_n": round(tex_n, 3),
                "aniso_n": round(aniso_n, 3), "hb_n": round(hb_n, 3),
                "coh_n": round(coh_n, 3), "oc_n": round(oc_n, 3),
                "bimod": round(bimod, 3), "dr_n": round(dr_n, 3),
                "rim_n": round(rim_n, 3), "ss_n": round(ss_n, 3),
                "flat_n": round(flat_n, 3), "poverty": round(poverty, 3),
                "smoothness": round(smoothness, 3),
                "plasticity": round(plasticity, 3),
            },
            "weights": {
                "anti_cgi": W_ANTI_CGI, "impasto": W_IMPASTO,
                "chiaroscuro": W_CHIAROSCURO, "planarity": W_PLANARITY,
                "palette": W_PALETTE,
            },
            "ok": True,
        },
    }


# ===========================================================================
#  PROMPT COMPOSITION
# ===========================================================================
def _rng(seed_hint):
    """Deterministic byte stream from a seed hint. Never uses hash()."""
    if seed_hint is None:
        return None
    digest = hashlib.blake2b(str(seed_hint).encode("utf-8"), digest_size=16).digest()
    return [b for b in digest]


def _pick(items, stream, idx, default_first=True):
    if not items:
        return None
    if stream is None:
        return items[0] if default_first else items[-1]
    return items[stream[idx % len(stream)] % len(items)]


def _view_table():
    v = _corpus().get("views", {})
    canon = v.get("canonical", [])
    if not canon:
        canon = [{"deg": d, "name": n} for d, n in (
            (0, "front view, facing camera"),
            (45, "left three-quarter view, head turned left"),
            (90, "left side profile"),
            (135, "back left three-quarter view, looking away over shoulder"),
            (180, "back view, face mostly turned away"),
            (225, "back right three-quarter view, looking away over shoulder"),
            (270, "right side profile"),
            (315, "right three-quarter view, head turned right"))]
    return canon, v.get("descriptor", "camera orbit {deg} degrees"), \
        v.get("alias", {})


def views(n: int) -> list:
    """N turnaround view descriptors for an n-cell character orbit.

    Evenly spaced over a full 360 degree yaw. Each descriptor names the nearest
    canonical bucket and then states the exact orbit angle, so a 64-cell atlas
    gets 64 distinct strings that still collapse onto the 8 prompt-embedding
    buckets the existing yaw-bucket drafts cache. n <= 8 snaps exactly onto the
    canonical eight.
    """
    if n is None or n <= 0:
        return []
    canon, desc, _ = _view_table()
    out = []
    for i in range(int(n)):
        deg = (360.0 * i) / float(n)
        best = min(canon, key=lambda c: min(abs(c["deg"] - deg),
                                            360.0 - abs(c["deg"] - deg)))
        d = int(round(deg)) % 360
        if abs(best["deg"] - d) < 1e-6:
            out.append(best["name"])
        else:
            out.append(f"{best['name']}, {desc.format(deg=d)}")
    return out


def _resolve_view(view):
    canon, desc, alias = _view_table()
    if view is None:
        return canon[0]["name"]
    if isinstance(view, (int, float)):
        deg = int(round(float(view))) % 360
    else:
        key = str(view).strip().lower()
        for c in canon:
            if key == c["name"].lower():
                return c["name"]
        if key in alias:
            deg = int(alias[key]) % 360
        else:
            try:
                deg = int(round(float(key.rstrip("d").rstrip("deg").strip()))) % 360
            except ValueError:
                # Not a known bucket and not a number: pass it through. The
                # caller may know a pose this table does not.
                return str(view)
    best = min(canon, key=lambda c: min(abs(c["deg"] - deg), 360 - abs(c["deg"] - deg)))
    if abs(best["deg"] - deg) < 1e-6:
        return best["name"]
    return f"{best['name']}, {desc.format(deg=deg)}"


def arcane_prompt(subject: str, realm: str = "piltover", view: str = "front",
                  rose: bool = False, seed_hint: str | None = None) -> str:
    """Compose a spec-conformant Arcane prompt.

    The clause order matches the eight drafts in atlas_drafts/arcane_*.json, so
    a prompt from here can be dropped straight into an existing atlas without
    moving the character the seed lock is holding: subject, hybrid-animation
    core, identity, then the six invariants in spec order, then view, then
    closers, then the anti-CGI steer.

    FLUX.1-dev has no negative-prompt channel at the guidance this pipeline
    runs, so the anti-plastic pressure is applied as positive steering clauses
    plus a trailing "not ..." tail rather than as a negative prompt.

    `subject` is used verbatim when it already reads as a full noun phrase
    (starts with a determiner, or runs longer than eight words). Otherwise it
    is wrapped in the character template from arcane_prompts.toml, which is the
    Phase X Arcane Italian Princess head clause.

    `seed_hint` is deterministic (blake2b, never hash()): the same hint always
    selects the same optional fragments, so an atlas can vary its wording
    across cells without any cell drifting out of conformance.
    """
    c = _corpus()
    ch = c.get("character", {})
    stream = _rng(seed_hint)

    subj = (subject or "").strip().rstrip(",")
    if not subj:
        subj = ch.get("default_subject",
                      "An adult stunning princess with the beauty of an "
                      "Italian supermodel")
    else:
        first = subj.split()[0].lower()
        if first not in ("a", "an", "the") and len(subj.split()) <= 8:
            tmpl = ch.get("subject_template",
                          "An adult stunning {subject} with the beauty of an "
                          "Italian supermodel")
            subj = tmpl.replace("{subject}", subj)

    key = str(realm or "piltover").strip().lower()
    r = REALMS.get(key, PILTOVER)

    parts = [subj]
    core = ch.get("core")
    if core:
        parts.append(core)
    parts.extend(ch.get("identity", []))

    # The six invariants, in spec order.
    brush = c.get("brushwork", {})
    parts.append(brush.get("required", "hand-painted 2D brushwork over 3D forms"))
    frag = _pick(brush.get("fragments", []), stream, 0)
    if frag:
        parts.append(frag)

    planes = c.get("planes", {})
    parts.append(planes.get("required", "sharp angular facial planes"))
    frag = _pick(planes.get("fragments", []), stream, 1)
    if frag:
        parts.append(frag)

    lighting = c.get("lighting", {})
    parts.append(lighting.get("required", "dual-source chiaroscuro"))
    frag = _pick(r.get("light", []), stream, 2)
    if frag:
        parts.append(frag)

    parts.extend(r.get("palette_words", []))
    frag = _pick(r.get("style", []), stream, 3)
    if frag:
        parts.append(frag)
    frag = _pick(r.get("setting", []), stream, 4)
    if frag:
        parts.append(frag)

    if rose:
        rc = c.get("rose", {})
        parts.extend(rc.get("fragments", []))
        tint = (rc.get("realm_tint") or {}).get(r["key"])
        if tint:
            parts.append(tint)

    parts.append(_resolve_view(view))

    closers = ch.get("closers", [])
    pick = _pick(closers, stream, 5)
    if pick:
        parts.append(pick)
    if closers and closers[0] not in parts:
        parts.append(closers[0])

    neg = c.get("negative", {})
    parts.extend(neg.get("positive_steer", []))
    banned = neg.get("anti_cgi", [])
    if banned:
        parts.append("not " + ", not ".join(banned))

    if seed_hint:
        parts.append(f"seed motif {seed_hint}")

    seen, out = set(), []
    for p in parts:
        p = str(p).strip().strip(",")
        if p and p.lower() not in seen:
            seen.add(p.lower())
            out.append(p)
    return ", ".join(out)


# ===========================================================================
#  MODULE CONSTANTS BUILT FROM THE CORPUS
# ===========================================================================
_rc = _corpus().get("realm", {})
ZAUN = _build_realm(_rc.get("zaun", _FALLBACK_CORPUS["realm"]["zaun"]))
PILTOVER = _build_realm(_rc.get("piltover", _FALLBACK_CORPUS["realm"]["piltover"]))
REALMS = {"zaun": ZAUN, "piltover": PILTOVER}

# Spliced into RedHatAI/pixtral-12b-quantized.w4a16's judge system prompt by the
# jury evaluator. Pixtral is the palette / medium / lighting critic and is the
# primary consumer: 12B, strong on colour and painterly medium, weaker at long
# conditional instruction chains, so the rubric is flat, concrete, and asks for
# one fixed JSON shape.
#
# SPLICING NOTE for the jury evaluator: this string contains a literal JSON
# template, so it carries real { } braces. Splice it by CONCATENATION, or pass
# it as an argument to .format() -- never embed it inside a template string that
# is then .format()-ed or f-stringed, because the JSON braces will be read as
# format fields and raise KeyError. There are no intentional placeholders in it.
FORTICHE_RUBRIC: str = (_corpus().get("rubric", {}).get("pixtral") or "").strip()

# Optional companion aimed at unsloth/Qwen3.8-27B-NVFP4, the structural
# inspector. Angular facial geometry is as much a structural judgement as an
# aesthetic one, so the planes/silhouette invariant is asked of both jurors from
# different angles: Pixtral reads it as style, Qwen reads it as construction.
FORTICHE_RUBRIC_STRUCTURAL: str = (
    _corpus().get("rubric", {}).get("qwen_structural") or "").strip()


# ===========================================================================
#  CALIBRATION
# ===========================================================================
CALIBRATION = """
MEASURED WITH
  Python 3.14.7 at /Users/jay/.venvs/mlx/bin/python3, numpy 2.5.2, Pillow
  12.3.0. NOTE: the `python3` first on PATH on this machine
  (/opt/homebrew/bin/python3 -> python@3.14) is a one-line `#!/bin/sh` stub that
  prints nothing and exits 0 for every invocation, including
  `python3 -m py_compile` on a file with a deliberate syntax error. Nothing in
  this block was measured with it.

SAMPLE
  27 real 1024x1024 PNG renders, every image under apps/ in this repo. All are
  BF16 FLUX.1-dev output from this fleet. Five are Arcane-named; the rest are
  the sovereign_masterpiece and throne series. This is a real sample, but it is
  27 frames from one fleet and one prompt family -- it is not a corpus.
  4 synthetic controls with known properties, generated to bracket the scales:
  a faceted broadband-paint plate in each realm palette (upper anchor), an
  airbrushed skin-tone blob under a single soft source (lower anchor), and
  band-limited isotropic noise (a trap for any metric that mistakes energy for
  craft).

WHAT THE SAMPLE IS NOT
  Not one of the 27 real renders is a Fortiche frame. Every one of them is on
  the smooth, photographic, plastic side of the spec's anti-CGI gate -- which
  is precisely the problem this module was written to detect, and it is the
  reason the whole corpus lands in "drift" rather than "conforming". So:

    * the LOW end of every scale is empirically anchored. 27 real negatives
      plus two synthetic floors is a real distribution and the thresholds that
      separate "airbrushed" from "detailed" are earned.
    * the HIGH end is NOT empirically anchored. There is no real Arcane frame
      in this repo to calibrate against. The HI constants come from the
      synthetic paint controls plus the requirement that the observed corpus
      land in the 35-60 band, which is where a human eye puts it. They are
      extrapolation, and CONFORMING_MIN in particular is an assertion, not a
      measurement.

  Every threshold in this module is therefore PROVISIONAL. The first real
  Fortiche-conforming render this pipeline produces should be measured and the
  HI constants and CONFORMING_MIN moved to match it. Until then, treat
  "conforming" as unproven and rank by fortiche_score instead of gating on it.

MEASURED DISTRIBUTION -- the 27 real renders only, at WORK_SIDE=768
                        min     p10     p25     med     p75     p90     max
  broadband           0.204   0.246   0.271   0.324   0.363   0.380   0.425
  tex_density         0.266   0.423   0.481   0.508   0.662   0.684   0.733
  stroke_aniso        0.265   0.331   0.363   0.408   0.476   0.502   0.609
  hard_break          0.102   0.155   0.202   0.253   0.279   0.300   0.349
  coherence           0.414   0.476   0.496   0.531   0.585   0.625   0.675
  orient_conc         0.004   0.006   0.012   0.017   0.023   0.040   0.120
  dip                 0.131   0.194   0.281   0.542   0.646   0.812   0.890
  mode_gap             19.1    27.9    31.2    36.0    42.4    47.4    52.5
  dyn_range            58.3    77.7    82.6    87.9    94.7    95.7    97.6
  rim_ratio           0.531   0.903   1.026   1.576   2.302   2.673   3.533
  skin_area           0.032   0.088   0.233   0.375   0.456   0.628   0.669
  skin_break          0.155   0.190   0.225   0.270   0.335   0.415   0.454
  skin_smooth         0.000   0.000   0.000   0.017   0.065   0.074   0.130
  flat_area           0.000   0.000   0.004   0.038   0.119   0.158   0.266
  dof_spread          1.556   1.722   1.826   2.250   2.611   3.131   4.918

  The four synthetic controls, which set the ends of every scale:
                     broadband tex_den  aniso  hard_br    dip  gap  skin_sm flat
  paint (piltover)      0.597   0.947   0.539   0.786   0.860 56.6   0.000  0.000
  paint (zaun)          0.607   0.937   0.544   0.662   0.972 68.3   0.000  0.000
  isotropic noise       0.951   0.602   0.108   0.007   0.015 16.7   0.000  0.000
  airbrushed blob       0.038   0.018   0.175   0.004   0.851 20.5   0.307  0.969

RESULTING SCORE DISTRIBUTION (27 real renders)
                        min     p10     p25     med     p75     p90     max
  impasto              30.7    32.7    36.4    43.3    48.3    52.9    57.6
  planarity            31.8    38.6    41.1    47.3    53.0    67.2    83.0
  chiaroscuro          27.9    35.2    39.0    48.5    53.0    66.9    73.6
  palette_zaun         12.2    19.1    23.1    27.2    33.9    46.8    57.9
  palette_piltover     24.6    27.8    52.2    63.4    75.2    77.2    86.5
  anti_cgi             35.9    50.6    56.0    68.0    75.1    79.7    83.6
  fortiche_score       42.3    43.9    47.1    54.2    58.8    62.7    71.3

  Controls: paint plates 90.1 and 90.4 (conforming), isotropic noise 39.4
  (reject), airbrushed blob 7.5 (reject). With CONFORMING_MIN at 72.0 every one
  of the 27 real renders lands in "drift", which is what a human eye says about
  all of them.

THE REALMS ARE NOT SYMMETRIC, AND palette IS WEIGHTED ACCORDINGLY
  Piltover's anchor set -- marble neutrals, brass, hextech cyan, apex night
  blue -- covers far more of FLUX's default output distribution than Zaun's
  emerald / rust / violet does. Median palette_piltover is 63.4 against 27.2
  for Zaun on frames that were never prompted for either realm. Dropping the
  one non-spec Piltover anchor (#173a52 apex night blue) was tried and made
  things worse, not better: it pulled the median to 35.9 but also collapsed the
  deliberately-Piltover synthetic control from 87.7 to 50.6, i.e. it destroyed
  discrimination rather than removing bias. The anchor stays, the skew is real,
  and it is the main reason `palette` carries the lowest composite weight and
  `realm` is reported as information rather than scored as merit.

THREE DESIGN DECISIONS THE DATA FORCED

  1. Energy alone is not impasto. Band-limited isotropic noise scores 0.95 on
     broadband -- higher than any real render and higher than the paint
     control. Measured on energy alone it would be the most "impasto" frame in
     the sample. Multiplying by stroke anisotropy (noise: 0.09, renders:
     0.24-0.60) is what makes the term mean "brushwork" rather than "detail".

  2. A histogram valley alone is not chiaroscuro. The airbrushed control scores
     0.85 on dip, near the top of the sample, purely because a blob on a
     backdrop makes two clean populations. Its mode gap is 20.5 L* against
     52-63 for the paint controls. bimod is dip TIMES normalised mode gap for
     exactly that reason; either factor alone is winnable by a frame with no
     lighting design at all.

  3. dof_spread is measured and deliberately NOT scored. Sharp-subject /
     soft-background disparity is the cleanest optical-lens tell in the data
     (bokeh-heavy renders 3.9-4.8, the paint controls 1.5-1.9) and it was the
     obvious fourth anti_cgi term. It is not used, because a real Fortiche
     frame often pairs a heavily painted subject with a deliberately flat
     simplified background, which would produce the same disparity for the
     opposite reason. With no real Fortiche frame available to check which way
     it fires, shipping it in a gate would be guessing. It stays in `details`
     as a diagnostic so it can be validated later.

WHAT THIS MODULE CANNOT DO
  It separates strongly at the failure end and weakly at the excellence end. It
  will reliably catch airbrushed skin, spectrally dead surfaces, single-source
  lighting, rounded isotropic form, and a palette in neither realm. It cannot
  tell a very good painted frame from a merely competent one -- a detailed
  photoreal render carries real high-frequency energy from fabric and hair, and
  no model-free statistic cleanly separates "detailed" from "painted". That
  discrimination is the jury's job. This is the floor, not the ceiling.
"""


# ===========================================================================
#  CLI
# ===========================================================================
def _bar(v, width=18):
    n = int(round(max(0.0, min(100.0, v)) / 100.0 * width))
    return "#" * n + "." * (width - n)


def _plain_table(rows):
    cols = ("impasto", "planar", "chiaro", "zaun", "pilt", "anti", "SCORE")
    keys = ("impasto", "planarity", "chiaroscuro", "palette_zaun",
            "palette_piltover", "anti_cgi", "fortiche_score")
    print(f"{'frame':38s}" + "".join(f"{c:>8s}" for c in cols)
          + f"  {'realm':<9s}{'verdict':<11s}")
    print("-" * (38 + 8 * len(cols) + 22))
    for r in rows:
        name = os.path.basename(r["path"])[:38]
        print(f"{name:38s}" + "".join(f"{r[k]:8.1f}" for k in keys)
              + f"  {r['realm']:<9s}{r['verdict']:<11s}")
    print("-" * (38 + 8 * len(cols) + 22))
    if rows:
        arr = {k: np.array([r[k] for r in rows]) for k in keys}
        for tag, fn in (("min", np.min), ("median", np.median), ("max", np.max)):
            print(f"{tag:38s}" + "".join(f"{fn(arr[k]):8.1f}" for k in keys))
        counts = {}
        for r in rows:
            counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
        print("\nverdicts: " + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))


def _main(argv):
    args = [a for a in argv if not a.startswith("-")]
    if args:
        paths = [pathlib.Path(a) for a in args]
    else:
        roots = [HERE / "apps"]
        if _pipeline_paths is not None:
            extra = getattr(_pipeline_paths, "OUTPUT_DIR", None)
            if extra:
                roots.append(pathlib.Path(extra))
        paths = []
        for root in roots:
            if root.is_dir():
                paths.extend(sorted(root.rglob("*.png")))
    if not paths:
        print("no PNGs found")
        return 1

    if "--json" in argv:
        print(json.dumps([conformance(str(p)) for p in paths], indent=2))
        return 0

    rows = [conformance(str(p)) for p in paths]

    # arcane_log is owned by another agent and may be absent or mid-rewrite.
    # Nothing below is allowed to depend on it existing.
    log = None
    try:
        from arcane_log import get_logger  # type: ignore
        cand = get_logger("fortiche")
        if hasattr(cand, "fortiche") and hasattr(cand, "table"):
            log = cand
    except Exception:
        log = None

    keys = ("impasto", "planarity", "chiaroscuro", "palette_zaun",
            "palette_piltover", "anti_cgi", "fortiche_score")
    best = max(rows, key=lambda r: r["fortiche_score"])
    worst = min(rows, key=lambda r: r["fortiche_score"])
    counts: dict = {}
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1

    if log is None:
        _plain_table(rows)
    else:
        try:
            log.header("FORTICHE CONFORMANCE SWEEP",
                       f"{len(rows)} frames  ·  WORK_SIDE={WORK_SIDE}  ·  "
                       f"pure numpy + Pillow, no model")
            body = []
            for r in rows:
                body.append([os.path.basename(r["path"])[:34]]
                            + [f"{r[k]:.1f}" for k in keys]
                            + [r["realm"], r["verdict"]])
            arr = {k: np.array([r[k] for r in rows]) for k in keys}
            for tag, fn in (("min", np.min), ("median", np.median),
                            ("max", np.max)):
                body.append([tag] + [f"{fn(arr[k]):.1f}" for k in keys]
                            + ["", ""])
            log.table(headers=["frame", "impasto", "planar", "chiaro", "zaun",
                               "pilt", "anti-cgi", "SCORE", "realm", "verdict"],
                      rows=body,
                      aligns=["l"] + ["r"] * 7 + ["l", "l"])
            log.rule("thresholds")
            log.kv(conforming_min=CONFORMING_MIN, drift_min=DRIFT_MIN,
                   anti_cgi_floor=ANTI_CGI_FLOOR, status="PROVISIONAL")
            log.kv(**{k: v for k, v in sorted(counts.items())})
            log.rule("extremes")
            for tag, r in (("HIGHEST", best), ("LOWEST", worst)):
                log.info(f"{tag}  {os.path.basename(r['path'])}")
                log.fortiche(r)
                for why in r["reasons"]:
                    log.warn(why)
            return 0
        except Exception as exc:
            print(f"[arcane_log unavailable: {exc!r}; falling back]")
            _plain_table(rows)

    for tag, r in (("HIGHEST", best), ("LOWEST", worst)):
        print(f"\n{tag}: {os.path.basename(r['path'])}  "
              f"score={r['fortiche_score']} verdict={r['verdict']}")
        for k in keys:
            print(f"   {k:<17s} {_bar(r[k])} {r[k]:5.1f}")
        for why in r["reasons"]:
            print(f"   - {why}")
    print(f"\nthresholds (PROVISIONAL): conforming>={CONFORMING_MIN} "
          f"drift>={DRIFT_MIN} anti_cgi_floor={ANTI_CGI_FLOOR}")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
