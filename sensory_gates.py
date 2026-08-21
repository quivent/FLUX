#!/usr/bin/env python3
"""High-frequency micro-sensory triage gates for the Arcane / MoJ pipeline.

`jury_continuum.toml` declares `[gates.sensory]` with `siglip_aesthetic_head`,
`dinov2_semantic_novelty` and `palette_delta_evidence` at a 2.5 GiB budget, and
`provision_jury.sh` / `docs/jury_pipeline.md` promise "Fast Sensory Gates
(DINOv2-Giant + SigLIP Head), <10ms". Until this module existed there was no
implementing code behind any of those three flags. This is it.

WHAT THIS IS FOR
Every settled 1024x1024 frame passes through here BEFORE the expensive VLM jury
(Qwen3-VL 8B + Pixtral 12B + a 31B Governor). A frame that fails the gate must
not burn a jury pass. So this module is a triage filter, not a judge: it is
allowed to be blunt, it is not allowed to be slow, and it is *never* allowed to
present a guess as a model measurement.


HONESTY CONTRACT  (the single most important rule in this file)
--------------------------------------------------------------
The pipeline this replaces was emitting invented numbers. Therefore:

  * `backend` always names the tier that actually ran: "siglip+dinov2", "clip"
    or "heuristic". It is never aspirational.
  * `degraded` is True whenever the real SigLIP + DINOv2 path did not run.
  * `measured` maps every score to its provenance, and any score that could not
    be measured at all is reported as `"unavailable"` with a neutral value that
    is *excluded from the pass/fail decision* rather than silently counted.
  * `calibration` says whether the constants turning raw model numbers into
    0-100 were measured on this corpus ("measured") or are engineering defaults
    awaiting a calibration run on the node ("provisional").

A heuristic number is a real measurement of pixels. It is simply not a model
score, and this module never lets a caller confuse the two.


DINOv2 IS MANDATORY
-------------------
Operator direction: "Pixtral and Dino on always." DINOv2 is an always-on
component of every hardware profile, not an optional tier. The fallbacks below
still exist -- they are what makes this file importable on a CPU-only dev Mac,
and they are what keeps a wave alive if a model dies mid-run -- but on
production hardware a fallback is an ERROR CONDITION, not a steady state:

  * `available()["tier"]` is "full" | "degraded" | "emergency", and
    `available()["mandatory_satisfied"]` is True only for "full".
  * `warm(require_full=True)` RAISES `SensoryGateUnavailable` instead of
    silently stepping down, so `arcane_pipeline.py preflight` can hard-fail a
    production node that cannot bring DINOv2 up.
  * Whenever the full tier is not running, `gate_scores()` sets
    `degraded: true` and puts a "DEGRADED: ... DINOv2 ..." line at the head of
    `reasons`.

`passed` remains the triage verdict and is computed from gate FAILURES only --
see `failures` in the returned dict. Degradation is reported loudly but does
not by itself reject a frame, because a dev box must still be able to triage.


TIER CHAIN
----------
  1. siglip+dinov2   tier "full", the mandatory one. SigLIP zero-shot aesthetic
                     head + image<->prompt alignment; DINOv2-Giant CLS
                     embeddings for semantic novelty.
  2. clip            tier "degraded". The already-proven path in
                     atelier/aesthetic.py: openai/clip-vit-base-patch32 with six
                     positive and six negative craft probes, plus image<->prompt
                     cosine. Probe text and calibration constants are imported
                     from that module when it is importable so there is one
                     source of truth; a vendored copy keeps this file standalone.
  3. heuristic       tier "emergency". Pure numpy/Pillow. Never fails, needs no
                     weights, no GPU. Novelty falls back to uniqueness_tracker's
                     128-d handcrafted perceptual fingerprint (chromatic moments
                     + FFT radial energy + gradient histograms).

`palette_delta` is deliberately outside the chain: it is LAB-space histogram
distance against the rolling frame memory, it needs no ML, and it works
identically on all three tiers. That is why the declared `palette_delta_evidence`
gate is the one gate that is always real.


VRAM BUDGET -- LOAD-BEARING, READ THE VERDICT LINE
--------------------------------------------------
VERDICT: DINOv2-Giant + SigLIP DOES NOT FIT 2.5 GiB. It needs ~2.85 GiB
resident. The `[gates.sensory] vram_expected_gib` line item must be raised
from 2.5 to 3.0 GiB in all three hardware profiles. On a 96 GB RTX PRO 6000
(and on B200 192 GB / B300 288 GB) that 0.5 GiB is free; the number in the
config is simply wrong today, and stepping DINOv2 down to fit it would violate
the always-on mandate. Raise the config, do not shrink the model.

Weights = params x 2 bytes (fp16), CALCULATED, not measured. "Resident" adds
batch-1 activations plus cuBLAS/cuDNN workspace and is an ESTIMATE (+-15%);
re-measure with torch.cuda.max_memory_allocated() on the node. The CUDA context
itself (~0.3 GiB, shared with the FLUX worker in the same process) is excluded.

  SENSORY_DINOV2_TIER   model                     params   weights   resident
  -------------------------------------------------------------------------
  giant  <- DEFAULT     facebook/dinov2-giant     1136 M   2.116 GiB
  large                 facebook/dinov2-large      304 M   0.566 GiB
  base                  facebook/dinov2-base        87 M   0.161 GiB

  SigLIP head           google/siglip-base-patch16-224   203 M   0.378 GiB
                        google/siglip-large-patch16-256  652 M   1.214 GiB
                        google/siglip-so400m-patch14-384 877 M   1.633 GiB

  COMBINED, as actually loaded:
    full,  giant + siglip-base    2.494 GiB weights  ~2.85 GiB resident  <- ship
    full,  large + siglip-base    0.944 GiB weights  ~1.25 GiB resident
    full,  base  + siglip-base    0.539 GiB weights  ~0.82 GiB resident
    degraded, clip-vit-base-p32   0.281 GiB weights  ~0.43 GiB resident
    emergency, numpy only         0                   0        (CPU)

  If 2.5 GiB were ever a hard ceiling that could not move, the only ways to
  keep Giant are: (a) hold SigLIP's 110 M-param text tower on CPU and only its
  vision tower on GPU -- prompt embeddings are cached per unique prompt so the
  amortised cost is near zero -- which lands at ~2.59 GiB, STILL over; or
  (b) fp8/int8 weights for Giant (~1.06 GiB, total ~1.75 GiB), which needs a
  quantisation stack this module is not permitted to add. Neither is necessary
  at 96 GB. Raise the line item to 3.0 GiB.

LATENCY: the heuristic-tier number reported by __main__ is MEASURED on a
CPU-only Mac. Every GPU figure below is an ESTIMATE for batch-1 fp16 on
Blackwell and must be re-measured on the node before anyone quotes it:
  dinov2-giant 224px (257 tokens, 40 layers) ~4-6 ms
  + siglip-base image tower                  ~1.0 ms
  + CPU palette / pixel measures / decode     ~2.0 ms
  =>  ~7-9 ms per frame: inside the <10 ms promise, but not comfortably.
  With `large` the same path is ~4-5 ms. Prompt text embeddings are cached, so
  a repeated prompt costs zero text-tower passes. If measurement on the node
  comes in over 10 ms, the levers in order are: CUDA-graph capture of the two
  vision towers, then batching settled frames, then stepping to `large`. Do not
  reach for the last one first -- every frame waved through here costs three
  VLM passes (Qwen3.8-27B + Pixtral-12B + Gemma-4-31B), so a slightly slower
  gate is still a bargain.


PUBLIC API
----------
    available() -> dict     cheap, no downloads, no torch import, safe anywhere
    warm(require_full=False) -> dict
                            load the best available tier, then report it.
                            require_full=True raises SensoryGateUnavailable
                            instead of degrading -- the preflight contract.
    gate_scores(image_path, prompt) -> dict
    is_mode_collapsed(window=3) -> bool

ROLLING MEMORY
Embeddings, palettes and verdicts persist in SQLite (own table
`sensory_embeddings`, alongside but never mixing with uniqueness_tracker's
`visual_fingerprints`) so novelty survives a restart. Embeddings are namespaced
by the backend that produced them and only ever compared within a namespace --
a CLIP vector and a DINOv2 vector are not on speaking terms. If SQLite is
unavailable the module degrades to an in-process ring buffer and says so.

LOGGING
Rendering goes through `arcane_log.get_logger("gates")` when that module is
importable, and through a small plain-text renderer with the same method names
when it is not -- imported defensively, method by method, so a half-landed
arcane_log cannot take a wave down. Every frame emits one `log.gates(result)`
line and one `log.event("sensory_gate", ...)` structured record; a fallback
tier raises a `log.degraded(...)` banner once at warm() and again every
DEGRADED_REMINDER_EVERY degraded frames, because an always-on model being off
is an incident and should read like one. `SENSORY_GATES_LOG=0` silences all of
it for bulk scoring.

ENVIRONMENT
    SENSORY_GATES_LOG         0 to silence all rendering    (default: on)
    SENSORY_GATES_EVENTS      JSONL sink for the fallback logger's log.event
    SENSORY_DEGRADED_REMINDER frames between degraded banners (default: 100)
    SENSORY_GATES_DEVICE      cuda | mps | cpu            (default: autodetect)
    SENSORY_GATES_BACKEND     auto | siglip+dinov2 | clip | heuristic
    SENSORY_DINOV2_TIER       giant | large | base        (default: large)
    SENSORY_SIGLIP_MODEL      HF id                       (default: siglip-base-patch16-224)
    SENSORY_GATES_DB          path to the SQLite file
    SENSORY_GATES_ALLOW_DOWNLOAD  0 to forbid any weight fetch
    SENSORY_MIN_AESTHETIC / _NOVELTY / _ADHERENCE / _PALETTE   pass floors
    SENSORY_MEMORY_WINDOW     frames compared for novelty      (default: 128)
"""

import contextlib
import importlib
import importlib.util
import json
import math
import os
import pathlib
import sqlite3
import sys
import textwrap
import threading
import time
from collections import OrderedDict, deque

import numpy as np
from PIL import Image

__all__ = [
    "available",
    "warm",
    "gate_scores",
    "is_mode_collapsed",
    "memory_stats",
    "SensoryGateUnavailable",
]


class SensoryGateUnavailable(RuntimeError):
    """The mandatory SigLIP + DINOv2 tier could not be brought up.

    Raised only by warm(require_full=True), which is what a production preflight
    should call. Nothing else in this module raises: a scorer must never be the
    reason a wave dies.
    """

# --------------------------------------------------------------------------- env


def _env(name, default=""):
    return os.environ.get(name, default).strip()


def _env_float(name, default):
    try:
        raw = _env(name)
        return float(raw) if raw else float(default)
    except (TypeError, ValueError):
        return float(default)


def _env_int(name, default):
    try:
        raw = _env(name)
        return int(raw) if raw else int(default)
    except (TypeError, ValueError):
        return int(default)


def _env_bool(name, default):
    raw = _env(name).lower()
    if not raw:
        return bool(default)
    return raw not in ("0", "false", "no", "off")


# ----------------------------------------------------------------------- logging
# arcane_log is the house renderer (agent 7). It is imported defensively because
# it is being written concurrently and because this module must stay importable
# and runnable on a bare box. The fallback below is a small plain-text renderer
# that speaks the same method names, so nothing here has to branch on which one
# is live.

LOG_ENABLED = _env_bool("SENSORY_GATES_LOG", True)
LOG_EVENTS_PATH = _env("SENSORY_GATES_EVENTS")     # fallback JSONL sink
DEGRADED_REMINDER_EVERY = _env_int("SENSORY_DEGRADED_REMINDER", 100)


class _PlainLogger:
    """Enough of arcane_log's surface to keep this module standalone.

    Deliberately not clever: one line per frame, a real banner for a degraded
    tier, and a JSONL sink when one is configured. If arcane_log is present its
    renderer wins for every method it implements.
    """

    _ANSI = {"dim": "2", "red": "31", "green": "32", "yellow": "33",
             "blue": "34", "magenta": "35", "cyan": "36", "bold": "1"}

    def __init__(self, name="gates", stream=None):
        self.name = name
        self.stream = stream or sys.stderr

    def _tty(self):
        try:
            return self.stream.isatty()
        except Exception:
            return False

    def _c(self, colour, text):
        if not self._tty():
            return text
        return f"\033[{self._ANSI.get(colour, '0')}m{text}\033[0m"

    def _write(self, text):
        try:
            self.stream.write(text + "\n")
            self.stream.flush()
        except Exception:
            pass

    @staticmethod
    def _bar(value, width=10):
        if value is None:
            return "·" * width
        filled = int(round(width * max(0.0, min(100.0, float(value))) / 100.0))
        return "█" * filled + "░" * (width - filled)

    def info(self, message):
        self._write(f"{self._c('cyan', '[' + self.name + ']')} {message}")

    def warn(self, message):
        self._write(f"{self._c('yellow', '[' + self.name + '] warn')} {message}")

    def error(self, message):
        self._write(f"{self._c('red', '[' + self.name + '] error')} {message}")

    def kv(self, **fields):
        body = "  ".join(f"{self._c('dim', k)}={v}" for k, v in fields.items())
        self._write(f"{self._c('cyan', '[' + self.name + ']')} {body}")

    def table(self, rows, headers=None):
        rows = [[str(c) for c in row] for row in rows]
        if headers:
            rows = [[str(h) for h in headers]] + rows
        if not rows:
            return
        widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
        for n, row in enumerate(rows):
            line = "  ".join(c.ljust(widths[i]) for i, c in enumerate(row))
            self._write(("  " + self._c("bold", line)) if (headers and n == 0)
                        else "  " + line)

    @contextlib.contextmanager
    def timer(self, label):
        start = time.perf_counter()
        try:
            yield
        finally:
            self.info(f"{label} {(time.perf_counter() - start) * 1000.0:.2f} ms")

    def degraded(self, what, why):
        width = 76
        inner = width - 2
        title = " DEGRADED "
        self._write(self._c("red", "┌─" + title
                            + "─" * (width - len(title) - 1) + "┐"))
        blocks = [textwrap.wrap(str(what), inner) or [""], [""],
                  textwrap.wrap(str(why), inner) or [""]]
        for block in blocks:
            for line in block:
                self._write(self._c("red", "│ ") + line.ljust(inner)
                            + self._c("red", " │"))
        self._write(self._c("red", "└" + "─" * width + "┘"))

    def gates(self, result):
        verdict = (self._c("green", "PASS  ") if result.get("passed")
                   else self._c("red", "REJECT"))
        adh_unmeasured = result.get("measured", {}).get("adherence") == "unavailable"
        cells = [
            ("aes", result.get("aesthetic"), False),
            ("nov", result.get("novelty"), False),
            ("adh", result.get("adherence"), adh_unmeasured),
            ("pal", result.get("palette_delta"), False),
        ]
        parts = []
        for label, value, unmeasured in cells:
            if unmeasured:
                parts.append(f"{self._c('dim', label)} {'·' * 10}  n/a ")
            else:
                parts.append(f"{self._c('dim', label)} {self._bar(value)} "
                             f"{float(value or 0.0):5.1f}")
        tag = result.get("backend", "?")
        if result.get("degraded"):
            tag = self._c("yellow", tag + "!")
        self._write(f"{self._c('cyan', '[' + self.name + ']')} {verdict}  "
                    + "  ".join(parts)
                    + f"  {tag}  {result.get('latency_ms', 0.0):.2f}ms  "
                    + self._c("dim", os.path.basename(str(
                        result.get("image_path", "")))))
        for reason in result.get("reasons", []):
            text = str(reason)
            head, _, tail = text.partition(" ")
            if head == "DEGRADED:":
                # The banner already told the whole story once; repeating six
                # lines of it per frame would bury the actual verdicts.
                tail = tail.split(".")[0] + "."
                colour = "red"
            else:
                colour = "yellow"
            self._write("        " + self._c(colour, "· " + head) + " " + tail)

    def event(self, kind, **fields):
        record = {"ts": round(time.time(), 3), "logger": self.name, "kind": kind}
        record.update(fields)
        if not LOG_EVENTS_PATH:
            return
        try:
            pathlib.Path(LOG_EVENTS_PATH).parent.mkdir(parents=True, exist_ok=True)
            with open(LOG_EVENTS_PATH, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, default=str) + "\n")
        except Exception:
            pass


class _LogProxy:
    """arcane_log when it is there and implements the method, plain text when not.

    Written to survive agent 7's module landing half-finished: a missing or
    throwing method on the real logger falls through to the plain renderer
    rather than taking a wave down with it.
    """

    def __init__(self, name):
        self.name = name
        self.fallback = _PlainLogger(name)
        self.real = None
        self.source = "plain"
        try:
            from arcane_log import get_logger  # noqa: F401
            self.real = get_logger(name)
            self.source = "arcane_log"
        except Exception:
            self.real = None

    def __getattr__(self, method):
        def call(*args, **kwargs):
            if not LOG_ENABLED:
                return contextlib.nullcontext() if method == "timer" else None
            if self.real is not None:
                fn = getattr(self.real, method, None)
                if fn is not None:
                    try:
                        return fn(*args, **kwargs)
                    except Exception:
                        pass
            fn = getattr(self.fallback, method, None)
            if fn is not None:
                try:
                    return fn(*args, **kwargs)
                except Exception:
                    pass
            return contextlib.nullcontext() if method == "timer" else None
        return call


log = _LogProxy("gates")
_log_counters = {"frames": 0, "degraded_frames": 0, "degraded_announced": False}


# ------------------------------------------------------------------- model roster

# (hf id, params in millions, fp16 weight GiB) -- weights are params * 2 / 2**30.
DINOV2_TIERS = {
    "giant": ("facebook/dinov2-giant", 1136, 2.116),
    "large": ("facebook/dinov2-large", 304, 0.566),
    "base": ("facebook/dinov2-base", 87, 0.161),
}
# Giant, per the always-on mandate. It overruns the declared 2.5 GiB line item
# by ~0.35 GiB; the config is what moves, not the model. See the VRAM table.
DINOV2_DEFAULT_TIER = "giant"
SIGLIP_MODEL_DEFAULT = "google/siglip-base-patch16-224"
SIGLIP_WEIGHTS_GIB = {
    "google/siglip-base-patch16-224": 0.378,
    "google/siglip-large-patch16-256": 1.214,
    "google/siglip-so400m-patch14-384": 1.633,
}
CLIP_MODEL_ID = "openai/clip-vit-base-patch32"
CLIP_WEIGHTS_GIB = 0.281
# Batch-1 activations + cuBLAS workspace, per tier. Estimate, not measurement.
ACTIVATION_GIB = {"giant": 0.35, "large": 0.30, "base": 0.28, "clip": 0.15}

BACKEND_SIGLIP = "siglip+dinov2"
BACKEND_CLIP = "clip"
BACKEND_HEURISTIC = "heuristic"

# The tier the fleet is required to run. Anything else is an error condition on
# production hardware, however gracefully this module handles it.
MANDATORY_BACKEND = BACKEND_SIGLIP
TIER_LABEL = {
    BACKEND_SIGLIP: "full",
    BACKEND_CLIP: "degraded",
    BACKEND_HEURISTIC: "emergency",
}

# ------------------------------------------------------------------ pass thresholds
# Defaults, all overridable by env. They are triage floors, not quality bars:
# the job is to refuse the obviously-wasted 27B jury pass, so they sit low
# enough that a merely-mediocre frame still reaches the jury.

PASS_AESTHETIC = _env_float("SENSORY_MIN_AESTHETIC", 45.0)
PASS_NOVELTY = _env_float("SENSORY_MIN_NOVELTY", 30.0)
PASS_ADHERENCE = _env_float("SENSORY_MIN_ADHERENCE", 35.0)
PASS_PALETTE_DELTA = _env_float("SENSORY_MIN_PALETTE", 10.0)

# Mode collapse: the run has stopped exploring.
MODE_COLLAPSE_NOVELTY = _env_float("SENSORY_COLLAPSE_NOVELTY", 40.0)
MODE_COLLAPSE_PALETTE = _env_float("SENSORY_COLLAPSE_PALETTE", 8.0)

MEMORY_WINDOW = _env_int("SENSORY_MEMORY_WINDOW", 128)   # compared against
MEMORY_KEEP = _env_int("SENSORY_MEMORY_KEEP", 1024)      # retained on disk
COLD_START_FRAMES = 3          # below this, novelty is unknowable, not zero
COLD_START_NOVELTY = 85.0      # matches uniqueness_tracker's cold-start posture
NEUTRAL_SCORE = 50.0           # the value used when a metric is unmeasurable

# ------------------------------------------------------------------- calibration
# CLIP constants below are MEASURED: they are atelier/aesthetic.py's p5/p95 over
# 30 real cards sampled across runs/gen-0001..gen-0223 (see its CALIBRATION
# block). They are imported from that module when it is importable; the copies
# here exist only so this file stays standalone-importable on a torch-less box.
ADH_LO, ADH_HI = 0.24, 0.36
QUAL_MID, QUAL_TEMP = 0.040, 45.0

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
_PROBE_SOURCE = "vendored"

# SigLIP constants are PROVISIONAL. SigLIP is trained with a pairwise sigmoid
# loss, so its cosines live in a different, much tighter range than CLIP's and
# atelier's measured p5/p95 do not transfer. These are engineering defaults;
# `calibration` in the returned dict says "provisional" whenever they are used,
# and they should be replaced by a real p5/p95 sweep over node output.
SIGLIP_QUAL_MID, SIGLIP_QUAL_TEMP = 0.020, 60.0
SIGLIP_ADH_LO, SIGLIP_ADH_HI = 0.02, 0.14

# Novelty normalisation per embedding namespace: (d_min lo, d_min hi,
# d_avg lo, d_avg hi) where d = 1 - cosine similarity. Also provisional for the
# model tiers; the heuristic row mirrors uniqueness_tracker's observed ranges.
NOVELTY_CAL = {
    "dinov2": (0.08, 0.45, 0.20, 0.60),
    "clip": (0.05, 0.35, 0.15, 0.50),
    # Block-balanced 128-d fingerprint (see _balance_fingerprint). Derived from
    # the synthetic set in __main__, where a near-duplicate sits at d_min 0.0006
    # and genuinely different frames at 0.08-0.13. Re-measure on real renders.
    "heuristic": (0.010, 0.200, 0.050, 0.350),
}

# Palette: Hellinger distance between 8x8x8 LAB histograms. 0 is the same
# palette, ~0.5-0.8 is a genuinely different colourway.
PALETTE_MIN_LO, PALETTE_MIN_HI = 0.05, 0.55
LAB_BINS = 8
LAB_L_RANGE = (0.0, 100.0)
LAB_A_RANGE = (-90.0, 100.0)
LAB_B_RANGE = (-110.0, 95.0)

# Heuristic aesthetic normalisation, measured in the same units as
# atelier.aesthetic.bands() on a 256px-wide luma downscale.
H_EDGE_LO, H_EDGE_HI = 3.0, 11.0        # too flat below, healthy detail above
H_EDGE_NOISE_LO, H_EDGE_NOISE_HI = 28.0, 45.0   # past this it is noise, not detail
H_CONTRAST_LO, H_CONTRAST_HI = 8.0, 45.0        # std of luma
H_COLORFUL_LO, H_COLORFUL_HI = 8.0, 60.0        # Hasler-Susstrunk colourfulness
EDGE_LO, EDGE_HI = 5.0, 22.0            # atelier: absolute bottom-third energy
RATIO_LO, RATIO_HI = 0.35, 1.25         # atelier: bottom-third / top-two-thirds
H_W_DETAIL, H_W_CONTRAST, H_W_COLOR, H_W_BLANK = 0.30, 0.25, 0.20, 0.25
H_FLAT_LUMA_STD = 3.0                   # below this the frame is a flat plate

# ------------------------------------------------------------------ module state

_lock = threading.RLock()
_state = {
    "backend": None,        # tier actually loaded
    "device": None,
    "dtype": None,
    "siglip": None,         # (model, processor, pos_emb, neg_emb)
    "dinov2": None,         # (model, processor, size, mean, std)
    "clip": None,           # (model, processor, pos_emb, neg_emb)
    "load_error": {},       # tier -> str, why it is not up
}
_text_cache = OrderedDict()
_TEXT_CACHE_MAX = 256
_process_memory = deque(maxlen=MEMORY_KEEP)   # used only when SQLite is unusable
_db_state = {"path": None, "mode": None}


# --------------------------------------------------------------- optional imports


def _try_module(name):
    """Import by name, returning None instead of raising. Never downloads."""
    try:
        return importlib.import_module(name)
    except Exception:
        return None


def _load_atelier_aesthetic():
    """atelier/aesthetic.py by file path, so cwd and sys.path do not matter.

    It imports torch and transformers at module scope, so this returns None on
    a box without them -- which is exactly the signal that the CLIP tier is not
    available anyway.
    """
    path = pathlib.Path(__file__).resolve().parent / "atelier" / "aesthetic.py"
    if not path.is_file():
        return None
    try:
        spec = importlib.util.spec_from_file_location("_atelier_aesthetic", path)
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


def _adopt_atelier_probes():
    """Prefer atelier's probe text and measured constants over our vendored copy."""
    global POS_PROBES, NEG_PROBES, ADH_LO, ADH_HI, QUAL_MID, QUAL_TEMP
    global EDGE_LO, EDGE_HI, RATIO_LO, RATIO_HI, _PROBE_SOURCE
    mod = _load_atelier_aesthetic()
    if mod is None:
        return None
    try:
        POS_PROBES = list(mod.POS_PROBES)
        NEG_PROBES = list(mod.NEG_PROBES)
        ADH_LO, ADH_HI = float(mod.ADH_LO), float(mod.ADH_HI)
        QUAL_MID, QUAL_TEMP = float(mod.QUAL_MID), float(mod.QUAL_TEMP)
        EDGE_LO, EDGE_HI = float(mod.EDGE_LO), float(mod.EDGE_HI)
        RATIO_LO, RATIO_HI = float(mod.RATIO_LO), float(mod.RATIO_HI)
        _PROBE_SOURCE = "atelier.aesthetic"
    except Exception:
        pass
    return mod


def _pipeline_paths_db():
    """Ask agent 2's pipeline_paths for a database location, if it exists yet."""
    mod = _try_module("pipeline_paths")
    if mod is None:
        return ""
    for attr in ("jury_sqlite", "JURY_SQLITE", "sqlite_db", "SQLITE_DB",
                 "db_path", "DB_PATH"):
        value = getattr(mod, attr, None)
        if callable(value):
            try:
                value = value()
            except Exception:
                value = None
        if isinstance(value, (str, pathlib.Path)) and str(value):
            return str(value)
    for attr in ("output_dir", "OUTPUT_DIR", "out_dir", "OUT_DIR"):
        value = getattr(mod, attr, None)
        if callable(value):
            try:
                value = value()
            except Exception:
                value = None
        if isinstance(value, (str, pathlib.Path)) and str(value):
            return str(pathlib.Path(value) / "jury.sqlite3")
    return ""


def _flux_paths_out_dir():
    mod = _try_module("flux_paths")
    if mod is None:
        return ""
    try:
        return str(mod.default_out_dir())
    except Exception:
        return ""


# ------------------------------------------------------------------------ device


def resolve_device():
    """cuda -> mps -> cpu, overridable by SENSORY_GATES_DEVICE.

    Reported without importing torch when torch is absent: a box with no torch
    is a cpu box by definition, and available() must stay cheap.
    """
    forced = _env("SENSORY_GATES_DEVICE") or _env("FLUX_DEVICE")
    if forced:
        return forced
    torch = _try_module("torch") if importlib.util.find_spec("torch") else None
    if torch is None:
        return "cpu"
    try:
        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    try:
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


def _dtype_for(device, torch):
    if device in ("cuda", "mps"):
        return torch.float16
    return torch.float32


# ------------------------------------------------------------- weight cache probe


def _hf_cache_roots():
    roots = []
    for key in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE"):
        value = _env(key)
        if value:
            roots.append(pathlib.Path(value))
    home = _env("HF_HOME")
    if home:
        roots.append(pathlib.Path(home) / "hub")
    roots.append(pathlib.Path(os.path.expanduser("~")) / ".cache" / "huggingface" / "hub")
    roots.append(pathlib.Path("/root/.cache/huggingface/hub"))
    return roots


def _weights_cached(repo_id):
    """True when the repo already has a materialised snapshot on disk.

    Filesystem only. This is how available() can answer honestly without a
    single network call.
    """
    folder = "models--" + repo_id.replace("/", "--")
    for root in _hf_cache_roots():
        snap = root / folder / "snapshots"
        try:
            if snap.is_dir() and any(snap.iterdir()):
                return True
        except Exception:
            continue
    return False


def _downloads_allowed():
    if _env("HF_HUB_OFFLINE") in ("1", "true", "TRUE") or _env("TRANSFORMERS_OFFLINE") == "1":
        return False
    return _env_bool("SENSORY_GATES_ALLOW_DOWNLOAD", True)


def _dinov2_tier():
    tier = _env("SENSORY_DINOV2_TIER").lower() or DINOV2_DEFAULT_TIER
    return tier if tier in DINOV2_TIERS else DINOV2_DEFAULT_TIER


def _dinov2_model_id():
    override = _env("SENSORY_DINOV2_MODEL")
    return override or DINOV2_TIERS[_dinov2_tier()][0]


def _siglip_model_id():
    return _env("SENSORY_SIGLIP_MODEL") or SIGLIP_MODEL_DEFAULT


# ----------------------------------------------------------------- colour science


def _srgb_to_lab(rgb01):
    """(H, W, 3) sRGB in 0..1 -> CIE L*a*b* under D65. Pure numpy, ~1 ms at 96px."""
    a = np.asarray(rgb01, dtype=np.float32)
    lin = np.where(a <= 0.04045, a / 12.92, ((a + 0.055) / 1.055) ** 2.4)
    m = np.array([[0.4124564, 0.3575761, 0.1804375],
                  [0.2126729, 0.7151522, 0.0721750],
                  [0.0193339, 0.1191920, 0.9503041]], dtype=np.float32)
    xyz = lin @ m.T
    white = np.array([0.95047, 1.00000, 1.08883], dtype=np.float32)
    t = xyz / white
    eps = 216.0 / 24389.0
    kappa = 24389.0 / 27.0
    f = np.where(t > eps, np.cbrt(np.maximum(t, 1e-12)), (kappa * t + 16.0) / 116.0)
    lab = np.empty_like(f)
    lab[..., 0] = 116.0 * f[..., 1] - 16.0
    lab[..., 1] = 500.0 * (f[..., 0] - f[..., 1])
    lab[..., 2] = 200.0 * (f[..., 1] - f[..., 2])
    return lab


def palette_histogram(rgb01):
    """512-bin joint LAB histogram, L1-normalised. This is the palette evidence.

    A joint histogram, not three marginals: a frame that is half teal and half
    orange and a frame that is uniformly grey-brown have near-identical
    marginals and completely different palettes.
    """
    lab = _srgb_to_lab(rgb01).reshape(-1, 3)
    edges = (LAB_L_RANGE, LAB_A_RANGE, LAB_B_RANGE)
    idx = np.zeros(lab.shape[0], dtype=np.int64)
    for channel, (lo, hi) in enumerate(edges):
        scaled = (lab[:, channel] - lo) / max(1e-6, (hi - lo)) * LAB_BINS
        b = np.clip(scaled.astype(np.int64), 0, LAB_BINS - 1)
        idx = idx * LAB_BINS + b
    hist = np.bincount(idx, minlength=LAB_BINS ** 3).astype(np.float32)
    total = hist.sum()
    if total > 0:
        hist /= total
    return hist


def _hellinger(p, q):
    """sqrt(1 - Bhattacharyya). 0 = identical palette, 1 = disjoint. In [0, 1]."""
    bc = float(np.sqrt(np.maximum(p * q, 0.0)).sum())
    return math.sqrt(max(0.0, 1.0 - min(1.0, bc)))


# ------------------------------------------------------------- pixel measurements


def _clamp01(x):
    if not np.isfinite(x):
        return 0.0
    return 0.0 if x < 0 else (1.0 if x > 1 else float(x))


def _band_energy(block):
    if block.size == 0:
        return 0.0
    dx = np.abs(np.diff(block, axis=1)).mean() if block.shape[1] > 1 else 0.0
    dy = np.abs(np.diff(block, axis=0)).mean() if block.shape[0] > 1 else 0.0
    return float(dx + dy)


def bands(luma):
    """(edge_top, edge_bottom, ratio) split at 2/3 height.

    Same measurement and same split as atelier.aesthetic.bands(): compose()
    stamps type over the bottom of the card, so detail down there is type on
    top of a drawing.
    """
    cut = max(1, int(luma.shape[0] * 2 / 3))
    top = _band_energy(luma[:cut, :])
    bot = _band_energy(luma[cut:, :])
    return top, bot, bot / max(1e-6, top)


def _colorfulness(rgb255):
    """Hasler & Susstrunk (2003), the standard no-reference colourfulness metric."""
    r = rgb255[..., 0]
    g = rgb255[..., 1]
    b = rgb255[..., 2]
    rg = r - g
    yb = 0.5 * (r + g) - b
    std = math.sqrt(float(rg.std()) ** 2 + float(yb.std()) ** 2)
    mean = math.sqrt(float(rg.mean()) ** 2 + float(yb.mean()) ** 2)
    return std + 0.3 * mean


def heuristic_aesthetic(rgb255, luma):
    """A pixel-only craft proxy in 0..100. NOT a model score -- labelled as such.

    Four terms, each naming a distinct way a settled frame is wasted effort:
      detail      a flat or a noise-blizzard frame, both useless
      contrast    dead dynamic range, or blown highlights and crushed blacks
      colour      a grey mush with no colourway at all
      blank_lower atelier's format rule: keep the type band quiet
    """
    edge = _band_energy(luma)
    colourfulness = _colorfulness(rgb255)
    detail = _clamp01((edge - H_EDGE_LO) / (H_EDGE_HI - H_EDGE_LO))
    noise = _clamp01((edge - H_EDGE_NOISE_LO) / (H_EDGE_NOISE_HI - H_EDGE_NOISE_LO))
    detail *= (1.0 - 0.5 * noise)

    std = float(luma.std())
    contrast = _clamp01((std - H_CONTRAST_LO) / (H_CONTRAST_HI - H_CONTRAST_LO))
    clipped = float(((luma < 2.0) | (luma > 253.0)).mean())
    contrast *= (1.0 - _clamp01((clipped - 0.20) / 0.40))

    colour = _clamp01((colourfulness - H_COLORFUL_LO)
                      / (H_COLORFUL_HI - H_COLORFUL_LO))

    top, bot, ratio = bands(luma)
    b_abs = _clamp01((EDGE_HI - bot) / (EDGE_HI - EDGE_LO))
    b_rel = _clamp01((RATIO_HI - ratio) / (RATIO_HI - RATIO_LO))
    blank = 0.5 * b_abs + 0.5 * b_rel

    score = (H_W_DETAIL * detail + H_W_CONTRAST * contrast
             + H_W_COLOR * colour + H_W_BLANK * blank)
    flat = std < H_FLAT_LUMA_STD
    if flat:
        score = min(score, 0.05)
    parts = {
        "edge_energy": round(edge, 3),
        "luma_std": round(std, 3),
        "clipped_fraction": round(clipped, 4),
        "colorfulness": round(colourfulness, 3),
        "edge_top": round(top, 3),
        "edge_bottom": round(bot, 3),
        "edge_ratio": round(ratio, 3),
        "detail": round(detail, 4),
        "contrast": round(contrast, 4),
        "colour": round(colour, 4),
        "blank_lower": round(blank, 4),
        "flat_frame": flat,
    }
    return round(100.0 * _clamp01(score), 2), parts


def _fingerprint_inline(rgb255_128, luma64):
    """96 chromatic moments + 16 FFT radial rings + 16 gradient bins, L2-normed.

    A vendored equivalent of uniqueness_tracker.extract_visual_fingerprint, kept
    so this module works when that one is not importable. The real one is
    preferred at runtime so novelty stays comparable across the repo.
    """
    a = rgb255_128.astype(np.float32) / 255.0
    color = []
    for h_s in np.array_split(a, 4, axis=0):
        for block in np.array_split(h_s, 4, axis=1):
            color.extend(block.mean(axis=(0, 1)).tolist())
            color.extend(block.std(axis=(0, 1)).tolist())

    fft = np.log1p(np.abs(np.fft.fftshift(np.fft.fft2(luma64))))
    y, x = np.ogrid[:64, :64]
    dist = np.sqrt((x - 32) ** 2 + (y - 32) ** 2)
    radii = np.linspace(2, 30, 17)
    freq = []
    for i in range(16):
        mask = (dist >= radii[i]) & (dist < radii[i + 1])
        freq.append(float(fft[mask].mean()) if np.any(mask) else 0.0)

    gy, gx = np.gradient(luma64)
    mag = np.sqrt(gx ** 2 + gy ** 2)
    ang = (np.arctan2(gy, gx) + np.pi) % np.pi
    hist, _ = np.histogram(ang, bins=16, weights=mag, range=(0, np.pi))
    hist = (hist / (hist.sum() + 1e-6)).tolist()

    vec = np.asarray(color + freq + hist, dtype=np.float32)
    norm = float(np.linalg.norm(vec))
    return vec / norm if norm > 1e-6 else vec


FINGERPRINT_BLOCKS = (96, 16, 16)   # colour moments | FFT rings | gradient bins


def _balance_fingerprint(vec):
    """L2-normalise each of the three blocks before normalising the whole vector.

    This matters, and it is measurable. The 128-d fingerprint concatenates
    colour moments in 0..1 with FFT ring energies that are log-magnitudes in
    4..9. One global L2 norm therefore hands the FFT block almost the entire
    unit vector, and cosine distance between visually unrelated frames collapses
    to ~0.01. Measured on the synthetic set in __main__:

        raw               off-diagonal distances  min 0.0000  med 0.0163
        block-normalised  off-diagonal distances  min 0.0006  med 0.1250

    i.e. the raw form cannot tell a near-duplicate from a different picture,
    which would make the novelty gate reject everything. The transform is
    applied identically to both sides of every comparison, so it changes the
    scale, not the ordering, and uniqueness_tracker's own table is untouched.
    """
    v = np.asarray(vec, dtype=np.float32)
    if v.size != sum(FINGERPRINT_BLOCKS):
        n = float(np.linalg.norm(v))
        return v / n if n > 1e-6 else v
    out, start = [], 0
    for width in FINGERPRINT_BLOCKS:
        block = v[start:start + width]
        n = float(np.linalg.norm(block))
        out.append(block / n if n > 1e-6 else block)
        start += width
    w = np.concatenate(out)
    n = float(np.linalg.norm(w))
    return (w / n if n > 1e-6 else w).astype(np.float32)


def _fingerprint(image_path, rgb255_128, luma64):
    tracker = _try_module("uniqueness_tracker")
    if tracker is not None and hasattr(tracker, "extract_visual_fingerprint"):
        try:
            vec = np.asarray(tracker.extract_visual_fingerprint(image_path),
                             dtype=np.float32)
            if vec.ndim == 1 and vec.size == 128 and np.isfinite(vec).all():
                return _balance_fingerprint(vec), "uniqueness_tracker+balanced"
        except Exception:
            pass
    return (_balance_fingerprint(_fingerprint_inline(rgb255_128, luma64)),
            "inline+balanced")


# -------------------------------------------------------------------- model tiers


def _feat(out):
    """The projected embedding whichever shape the installed transformers returns.

    transformers 5 wraps get_*_features in a BaseModelOutputWithPooling;
    transformers 4 returned the bare tensor. Both are in the wild on this fleet.
    Lifted from atelier/aesthetic.py, where the same trap was already paid for.
    """
    torch = importlib.import_module("torch")
    if isinstance(out, torch.Tensor):
        return out
    po = getattr(out, "pooler_output", None)
    return po if po is not None else out[0]


def _unit(x):
    return x / x.norm(dim=-1, keepdim=True).clamp_min(1e-8)


def _from_pretrained(cls, model_id, torch, device, dtype, vision_only=False):
    kwargs = {}
    if not _downloads_allowed():
        kwargs["local_files_only"] = True
    try:
        model = cls.from_pretrained(model_id, dtype=dtype, **kwargs)
    except TypeError:
        model = cls.from_pretrained(model_id, torch_dtype=dtype, **kwargs)
    return model.to(device).eval()


def _processor(cls, model_id):
    kwargs = {}
    if not _downloads_allowed():
        kwargs["local_files_only"] = True
    return cls.from_pretrained(model_id, **kwargs)


def _proc_geometry(proc, default_size=224):
    """(size, mean, std) pulled off an HF image processor, with safe defaults."""
    size = default_size
    crop = getattr(proc, "crop_size", None)
    if isinstance(crop, dict) and crop.get("height"):
        size = int(crop["height"])
    else:
        s = getattr(proc, "size", None)
        if isinstance(s, dict):
            size = int(s.get("height") or s.get("shortest_edge") or default_size)
        elif isinstance(s, int):
            size = int(s)
    mean = np.asarray(getattr(proc, "image_mean", [0.5, 0.5, 0.5]), dtype=np.float32)
    std = np.asarray(getattr(proc, "image_std", [0.5, 0.5, 0.5]), dtype=np.float32)
    return size, mean, std


def _prep(img, size, mean, std, torch, device, dtype):
    """PIL resize + numpy normalise, then straight to a tensor.

    Deliberately not the HF processor's __call__: at batch 1 the processor's
    python-side work is a meaningful slice of a 10 ms budget, and the geometry
    it would apply is reproduced here exactly (shortest-edge resize plus centre
    crop for non-square input, direct resize for the square frames FLUX emits).
    """
    w, h = img.size
    if w != h:
        scale = size / float(min(w, h))
        img = img.resize((max(size, int(round(w * scale))),
                          max(size, int(round(h * scale)))), Image.BICUBIC)
        w, h = img.size
        left, top = (w - size) // 2, (h - size) // 2
        img = img.crop((left, top, left + size, top + size))
    elif (w, h) != (size, size):
        img = img.resize((size, size), Image.BICUBIC)
    a = np.asarray(img, dtype=np.float32) / 255.0
    a = (a - mean) / std
    t = torch.from_numpy(np.ascontiguousarray(a.transpose(2, 0, 1)))
    return t.unsqueeze(0).to(device=device, dtype=dtype)


def _load_siglip_tier():
    """SigLIP head + DINOv2 embeddings. Returns True when both are resident."""
    torch = _try_module("torch")
    transformers = _try_module("transformers")
    if torch is None or transformers is None:
        _state["load_error"][BACKEND_SIGLIP] = "torch or transformers not importable"
        return False
    device = resolve_device()
    dtype = _dtype_for(device, torch)
    siglip_id, dinov2_id = _siglip_model_id(), _dinov2_model_id()
    try:
        from transformers import AutoImageProcessor, AutoModel, AutoProcessor
        sproc = _processor(AutoProcessor, siglip_id)
        smodel = _from_pretrained(AutoModel, siglip_id, torch, device, dtype)
        probes = POS_PROBES + NEG_PROBES
        with torch.inference_mode():
            tok = sproc(text=probes, return_tensors="pt", padding="max_length",
                        truncation=True, max_length=64)
            tok = {k: v.to(device) for k, v in tok.items() if k != "pixel_values"}
            emb = _unit(_feat(smodel.get_text_features(**tok)).float())
        _state["siglip"] = (smodel, sproc, emb[:len(POS_PROBES)], emb[len(POS_PROBES):])

        dproc = _processor(AutoImageProcessor, dinov2_id)
        dmodel = _from_pretrained(AutoModel, dinov2_id, torch, device, dtype)
        size, mean, std = _proc_geometry(dproc, 224)
        _state["dinov2"] = (dmodel, dproc, size, mean, std)
    except Exception as exc:
        _state["siglip"] = None
        _state["dinov2"] = None
        _state["load_error"][BACKEND_SIGLIP] = f"{type(exc).__name__}: {exc}"
        return False
    _state["device"], _state["dtype"] = device, dtype
    return True


def _load_clip_tier():
    """atelier's proven CLIP path, re-hosted here so the device is ours to pick."""
    torch = _try_module("torch")
    transformers = _try_module("transformers")
    if torch is None or transformers is None:
        _state["load_error"][BACKEND_CLIP] = "torch or transformers not importable"
        return False
    device = resolve_device()
    dtype = _dtype_for(device, torch)
    try:
        from transformers import CLIPModel, CLIPProcessor
        proc = _processor(CLIPProcessor, CLIP_MODEL_ID)
        model = _from_pretrained(CLIPModel, CLIP_MODEL_ID, torch, device, dtype)
        probes = POS_PROBES + NEG_PROBES
        with torch.inference_mode():
            tok = proc(text=probes, return_tensors="pt", padding=True,
                       truncation=True, max_length=77)
            tok = {k: v.to(device) for k, v in tok.items() if k != "pixel_values"}
            emb = _unit(_feat(model.get_text_features(**tok)).float())
        _state["clip"] = (model, proc, emb[:len(POS_PROBES)], emb[len(POS_PROBES):])
    except Exception as exc:
        _state["clip"] = None
        _state["load_error"][BACKEND_CLIP] = f"{type(exc).__name__}: {exc}"
        return False
    _state["device"], _state["dtype"] = device, dtype
    return True


def _text_embedding(backend, prompt):
    """Prompt embedding, cached. A run reuses prompts; the text tower should not."""
    key = (backend, prompt)
    with _lock:
        if key in _text_cache:
            _text_cache.move_to_end(key)
            return _text_cache[key]
    torch = importlib.import_module("torch")
    if backend == BACKEND_SIGLIP:
        model, proc = _state["siglip"][0], _state["siglip"][1]
        tok = proc(text=[prompt or ""], return_tensors="pt", padding="max_length",
                   truncation=True, max_length=64)
    else:
        model, proc = _state["clip"][0], _state["clip"][1]
        tok = proc(text=[prompt or ""], return_tensors="pt", padding=True,
                   truncation=True, max_length=77)
    device = _state["device"]
    tok = {k: v.to(device) for k, v in tok.items() if k != "pixel_values"}
    with torch.inference_mode():
        emb = _unit(_feat(model.get_text_features(**tok)).float())
    with _lock:
        _text_cache[key] = emb
        while len(_text_cache) > _TEXT_CACHE_MAX:
            _text_cache.popitem(last=False)
    return emb


# ------------------------------------------------------------------ rolling memory


def _db_path():
    if _db_state["path"]:
        return _db_state["path"]
    candidates = [
        _env("SENSORY_GATES_DB"),
        _pipeline_paths_db(),
        (str(pathlib.Path(_env("FLUX_OUTPUT_DIR")) / "jury.sqlite3")
         if _env("FLUX_OUTPUT_DIR") else ""),
        (str(pathlib.Path(_env("OUT_DIR")) / "jury.sqlite3")
         if _env("OUT_DIR") else ""),
        "/root/Models/flux-output/jury.sqlite3",
        (str(pathlib.Path(_flux_paths_out_dir()) / "jury.sqlite3")
         if _flux_paths_out_dir() else ""),
        str(pathlib.Path(os.path.expanduser("~")) / "Models" / "flux-output"
            / "jury.sqlite3"),
    ]
    for cand in candidates:
        if not cand:
            continue
        try:
            if _writable_eventually(pathlib.Path(cand).parent):
                _db_state["path"] = cand
                return cand
        except Exception:
            continue
    _db_state["path"] = ""
    return ""


def _writable_eventually(directory):
    """Could we create a file here later, without creating anything now?

    Resolving the path must not have side effects: available() calls this, and
    available() is documented as cheap and safe to call anywhere. Creating a
    directory tree under a user's home just to answer a capability question is
    not cheap and not safe.
    """
    d = pathlib.Path(directory)
    for parent in [d] + list(d.parents):
        if parent.exists():
            return parent.is_dir() and os.access(str(parent), os.W_OK)
    return False


_SCHEMA = """
CREATE TABLE IF NOT EXISTS sensory_embeddings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    frame_key TEXT NOT NULL,
    filepath TEXT NOT NULL,
    namespace TEXT NOT NULL,
    dim INTEGER NOT NULL,
    vector BLOB NOT NULL,
    palette BLOB NOT NULL,
    aesthetic REAL NOT NULL,
    novelty REAL NOT NULL,
    adherence REAL NOT NULL,
    palette_delta REAL NOT NULL,
    backend TEXT NOT NULL,
    degraded INTEGER NOT NULL,
    passed INTEGER NOT NULL,
    created_at INTEGER NOT NULL
)
"""
_INDEX = ("CREATE INDEX IF NOT EXISTS sensory_embeddings_ns_id "
          "ON sensory_embeddings (namespace, id DESC)")


def _connect(create=True):
    """A connection to our own table, or None. Never raises, never blocks long.

    create=False refuses to bring a database into existence, which is what a
    read-only status probe wants.
    """
    path = _db_path()
    if not path:
        return None
    if not create and not os.path.exists(path):
        return None
    try:
        pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(path, timeout=2.0)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        with con:
            con.execute(_SCHEMA)
            con.execute(_INDEX)
        _db_state["mode"] = "sqlite"
        return con
    except Exception:
        _db_state["mode"] = "process"
        return None


def _recent(namespace, limit):
    """(embeddings for this namespace, palettes across all namespaces)."""
    con = _connect()
    if con is None:
        rows = list(_process_memory)[-limit:]
        vecs = [r["vector"] for r in rows if r["namespace"] == namespace]
        pals = [r["palette"] for r in rows]
        return vecs, pals, "process"
    try:
        cur = con.execute(
            "SELECT namespace, dim, vector, palette FROM sensory_embeddings "
            "ORDER BY id DESC LIMIT ?", (int(limit),))
        vecs, pals = [], []
        for ns, dim, vblob, pblob in cur.fetchall():
            try:
                pals.append(np.frombuffer(pblob, dtype=np.float32))
                if ns == namespace:
                    v = np.frombuffer(vblob, dtype=np.float32)
                    if v.size == dim:
                        vecs.append(v)
            except Exception:
                continue
        return vecs, pals, "sqlite"
    except Exception:
        return [], [], "sqlite-error"
    finally:
        try:
            con.close()
        except Exception:
            pass


def _remember(frame_key, filepath, namespace, vector, palette, scores,
              backend, degraded, passed):
    record = {
        "frame_key": frame_key,
        "filepath": filepath,
        "namespace": namespace,
        "vector": np.asarray(vector, dtype=np.float32),
        "palette": np.asarray(palette, dtype=np.float32),
        "novelty": float(scores["novelty"]),
        "palette_delta": float(scores["palette_delta"]),
        "created_at": int(time.time()),
    }
    con = _connect()
    if con is None:
        _process_memory.append(record)
        return "process"
    try:
        with con:
            con.execute(
                "INSERT INTO sensory_embeddings (frame_key, filepath, namespace, "
                "dim, vector, palette, aesthetic, novelty, adherence, "
                "palette_delta, backend, degraded, passed, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (frame_key, filepath, namespace, int(record["vector"].size),
                 record["vector"].tobytes(), record["palette"].tobytes(),
                 float(scores["aesthetic"]), float(scores["novelty"]),
                 float(scores["adherence"]), float(scores["palette_delta"]),
                 backend, int(bool(degraded)), int(bool(passed)),
                 record["created_at"]))
            con.execute(
                "DELETE FROM sensory_embeddings WHERE id <= "
                "(SELECT MAX(id) FROM sensory_embeddings) - ?", (int(MEMORY_KEEP),))
        return "sqlite"
    except Exception:
        _process_memory.append(record)
        return "process"
    finally:
        try:
            con.close()
        except Exception:
            pass


def memory_stats():
    """How many frames the rolling memory holds, and where it lives.

    Read-only: it will not create the database just to report on it.
    """
    path = _db_path()
    con = _connect(create=False)
    if con is None:
        if path and not os.path.exists(path):
            return {"store": "sqlite", "path": path, "frames": 0,
                    "exists": False}
        return {"store": "process", "path": "", "frames": len(_process_memory),
                "exists": bool(_process_memory)}
    try:
        n = con.execute("SELECT COUNT(*) FROM sensory_embeddings").fetchone()[0]
        return {"store": "sqlite", "path": path, "frames": int(n), "exists": True}
    except Exception:
        return {"store": "sqlite-error", "path": path, "frames": 0, "exists": True}
    finally:
        try:
            con.close()
        except Exception:
            pass


# ------------------------------------------------------------------ score mapping


def _novelty_from(vec, history, namespace_key):
    """0-100 novelty from cosine distance to the rolling memory.

    d_min is the honest signal ("is this a near-duplicate of anything I have
    seen") and carries most of the weight; d_avg stabilises it so one outlier
    neighbour cannot swing the verdict.
    """
    if len(history) < COLD_START_FRAMES:
        return COLD_START_NOVELTY, {
            "cold_start": True,
            "neighbours": len(history),
            "d_min": None,
            "d_avg": None,
        }
    mat = np.stack(history, axis=0)
    sims = mat @ vec
    dists = np.clip(1.0 - sims, 0.0, 2.0)
    d_min, d_avg = float(dists.min()), float(dists.mean())
    lo_m, hi_m, lo_a, hi_a = NOVELTY_CAL.get(namespace_key, NOVELTY_CAL["heuristic"])
    n_min = _clamp01((d_min - lo_m) / (hi_m - lo_m))
    n_avg = _clamp01((d_avg - lo_a) / (hi_a - lo_a))
    score = 100.0 * _clamp01(0.7 * n_min + 0.3 * n_avg)
    return round(score, 2), {
        "cold_start": False,
        "neighbours": len(history),
        "d_min": round(d_min, 4),
        "d_avg": round(d_avg, 4),
    }


def _palette_delta_from(hist, palettes):
    """0-100 distance from this frame's palette to the nearest recent palette."""
    if len(palettes) < 1:
        return COLD_START_NOVELTY, {"cold_start": True, "neighbours": 0,
                                    "h_min": None, "h_avg": None}
    good = [p for p in palettes if p.size == hist.size]
    if not good:
        return COLD_START_NOVELTY, {"cold_start": True, "neighbours": 0,
                                    "h_min": None, "h_avg": None}
    ds = [_hellinger(hist, p) for p in good]
    h_min, h_avg = float(min(ds)), float(sum(ds) / len(ds))
    n_min = _clamp01((h_min - PALETTE_MIN_LO) / (PALETTE_MIN_HI - PALETTE_MIN_LO))
    n_avg = _clamp01((h_avg - PALETTE_MIN_LO) / (PALETTE_MIN_HI - PALETTE_MIN_LO))
    score = 100.0 * _clamp01(0.75 * n_min + 0.25 * n_avg)
    return round(score, 2), {"cold_start": False, "neighbours": len(good),
                             "h_min": round(h_min, 4), "h_avg": round(h_avg, 4)}


def _probe_quality(img_emb, pos_emb, neg_emb, mid, temp):
    """Zero-shot craft contrast: mean(cos to positives) - mean(cos to negatives).

    Six probes a side and averaged, because any single probe is a wording
    lottery -- atelier's reasoning, and its measured offset for CLIP. Recentring
    on the corpus median matters: the positive side wins on essentially every
    real render, so the raw sign carries no information.
    """
    mp = float((img_emb @ pos_emb.T).mean())
    mn = float((img_emb @ neg_emb.T).mean())
    margin = mp - mn
    p_good = 1.0 / (1.0 + math.exp(-temp * (margin - mid)))
    return round(100.0 * _clamp01(p_good), 2), margin


# ------------------------------------------------------------------- public API


def _predicted_backend():
    forced = _env("SENSORY_GATES_BACKEND").lower()
    if forced in (BACKEND_SIGLIP, BACKEND_CLIP, BACKEND_HEURISTIC):
        return forced, True
    return "", False


def _tier_ready(model_ids):
    """Could this tier come up without a surprise? deps present and weights reachable."""
    if importlib.util.find_spec("torch") is None:
        return False
    if importlib.util.find_spec("transformers") is None:
        return False
    if all(_weights_cached(mid) for mid in model_ids):
        return True
    return _downloads_allowed()


def available():
    """What this box can actually do. Cheap, filesystem-only, never downloads.

    Booleans mean "this tier can be brought up here": the deps import and the
    weights are either already cached or fetchable. Once warm() has run they
    mean "this tier is resident right now".
    """
    forced, is_forced = _predicted_backend()
    loaded = _state["backend"]
    if loaded:
        siglip = loaded == BACKEND_SIGLIP
        dinov2 = loaded == BACKEND_SIGLIP
        clip = loaded == BACKEND_CLIP
        backend = loaded
    else:
        siglip = _tier_ready([_siglip_model_id()])
        dinov2 = _tier_ready([_dinov2_model_id()])
        clip = _tier_ready([CLIP_MODEL_ID])
        if is_forced:
            backend = forced
        elif siglip and dinov2:
            backend = BACKEND_SIGLIP
        elif clip:
            backend = BACKEND_CLIP
        else:
            backend = BACKEND_HEURISTIC
    return {
        "siglip": bool(siglip),
        "dinov2": bool(dinov2),
        "clip": bool(clip),
        "backend": backend,
        "device": _state["device"] or resolve_device(),
        # everything below is extra context; the four keys above are the contract
        "tier": TIER_LABEL.get(backend, "emergency"),
        "mandatory_satisfied": backend == MANDATORY_BACKEND,
        "mandatory_backend": MANDATORY_BACKEND,
        "loaded": bool(loaded),
        "degraded": backend != BACKEND_SIGLIP,
        "dinov2_tier": _dinov2_tier(),
        "dinov2_model": _dinov2_model_id(),
        "siglip_model": _siglip_model_id(),
        "clip_model": CLIP_MODEL_ID,
        "vram_gib_estimate": _vram_estimate(backend),
        "torch": importlib.util.find_spec("torch") is not None,
        "transformers": importlib.util.find_spec("transformers") is not None,
        "weights_cached": {
            "siglip": _weights_cached(_siglip_model_id()),
            "dinov2": _weights_cached(_dinov2_model_id()),
            "clip": _weights_cached(CLIP_MODEL_ID),
        },
        "downloads_allowed": _downloads_allowed(),
        "probe_source": _PROBE_SOURCE,
        "memory": memory_stats(),
        "forced": forced or None,
        "load_errors": dict(_state["load_error"]),
    }


def _vram_estimate(backend):
    """Resident GiB for a tier: fp16 weights (calculated) + activations (estimated)."""
    if backend == BACKEND_SIGLIP:
        tier = _dinov2_tier()
        dino = DINOV2_TIERS[tier][2]
        siglip = SIGLIP_WEIGHTS_GIB.get(_siglip_model_id(), 0.378)
        return {
            "weights_gib": round(dino + siglip, 3),
            "resident_gib": round(dino + siglip + ACTIVATION_GIB[tier], 2),
            "fits_declared_2_5": (dino + siglip + ACTIVATION_GIB[tier]) <= 2.5,
            "basis": "weights calculated from parameter counts; "
                     "activations estimated, re-measure on the node",
        }
    if backend == BACKEND_CLIP:
        return {
            "weights_gib": CLIP_WEIGHTS_GIB,
            "resident_gib": round(CLIP_WEIGHTS_GIB + ACTIVATION_GIB["clip"], 2),
            "fits_declared_2_5": True,
            "basis": "weights calculated; activations estimated",
        }
    return {"weights_gib": 0.0, "resident_gib": 0.0, "fits_declared_2_5": True,
            "basis": "no weights, CPU only"}


def _announce_tier(report):
    """Say once, loudly, which tier came up -- and treat a fallback as an incident.

    A degraded sensory gate on production hardware is not a footnote: DINOv2 is
    always-on by operator direction, so a run triaging on CLIP or on pixels is a
    fault that someone has to see. It is announced once at warm() and then
    re-announced every DEGRADED_REMINDER_EVERY frames, so it neither scrolls
    away nor drowns the per-frame log.
    """
    if _log_counters["degraded_announced"]:
        return
    _log_counters["degraded_announced"] = True
    vram = report.get("vram_gib_estimate", {})
    if report["mandatory_satisfied"]:
        log.info("sensory gates up on the mandatory tier")
        log.kv(tier=report["tier"], backend=report["backend"],
               device=report["device"], dinov2=report["dinov2_model"],
               siglip=report["siglip_model"],
               resident_gib=vram.get("resident_gib"),
               fits_2_5_gib=vram.get("fits_declared_2_5"),
               probes=report["probe_source"])
    else:
        errors = report.get("load_errors") or {}
        detail = "; ".join(f"{k}: {v}" for k, v in errors.items()) or "not attempted"
        log.degraded(
            f"sensory gates running tier '{report['tier']}' "
            f"(backend={report['backend']}) instead of the mandatory "
            f"{MANDATORY_BACKEND}",
            f"DINOv2 is always-on by operator direction and is NOT loaded on "
            f"this node. Novelty and aesthetic are therefore not model scores. "
            f"device={report['device']} torch={report['torch']} "
            f"transformers={report['transformers']} "
            f"dinov2={report['dinov2_model']} "
            f"cached={report['weights_cached']['dinov2']} "
            f"downloads_allowed={report['downloads_allowed']}. "
            f"Load errors: {detail}. "
            f"Call warm(require_full=True) at preflight to fail fast.")
    log.event("sensory_warm", tier=report["tier"], backend=report["backend"],
              device=report["device"],
              mandatory_satisfied=report["mandatory_satisfied"],
              dinov2_model=report["dinov2_model"],
              dinov2_tier=report["dinov2_tier"],
              siglip_model=report["siglip_model"],
              vram_resident_gib=vram.get("resident_gib"),
              vram_weights_gib=vram.get("weights_gib"),
              probe_source=report["probe_source"],
              load_errors=report.get("load_errors"))


def _log_frame(result):
    """One rendered triage line per frame, plus the structured record the studio tails."""
    _log_counters["frames"] += 1
    if result.get("degraded"):
        _log_counters["degraded_frames"] += 1
        n = _log_counters["degraded_frames"]
        if DEGRADED_REMINDER_EVERY > 0 and n % DEGRADED_REMINDER_EVERY == 0:
            log.degraded(
                f"still triaging without DINOv2 after {n} frames",
                f"tier='{result.get('tier')}' backend='{result.get('backend')}'. "
                f"Every one of those frames was gated on a proxy, not on the "
                f"declared sensory model. This is an incident, not a mode.")
    log.gates(result)
    log.event(
        "sensory_gate",
        image=result.get("image_path"),
        passed=result.get("passed"),
        backend=result.get("backend"),
        tier=result.get("tier"),
        degraded=result.get("degraded"),
        mandatory_satisfied=result.get("mandatory_satisfied"),
        aesthetic=result.get("aesthetic"),
        novelty=result.get("novelty"),
        adherence=result.get("adherence"),
        palette_delta=result.get("palette_delta"),
        latency_ms=result.get("latency_ms"),
        calibration=result.get("calibration"),
        measured=result.get("measured"),
        failures=result.get("failures"),
        reasons=result.get("reasons"),
        notes=result.get("notes"),
        parts=result.get("parts"),
        memory_store=result.get("memory_store"),
        device=result.get("device"),
    )


def warm(require_full=False):
    """Load the best tier this box can serve, then report what actually came up.

    Nothing above this line ever touches the network, and neither does import.
    This is the only function allowed to fetch weights.

    require_full=True is the production preflight contract: it RAISES
    SensoryGateUnavailable if the mandatory SigLIP + DINOv2 tier is not resident
    afterwards, so a node that cannot bring DINOv2 up fails loudly at startup
    instead of quietly triaging a whole run on CLIP or on pixel heuristics.
    """
    forced, is_forced = _predicted_backend()
    with _lock:
        if _state["backend"] is None:
            _adopt_atelier_probes()
            order = [BACKEND_SIGLIP, BACKEND_CLIP]
            if is_forced:
                order = [] if forced == BACKEND_HEURISTIC else [forced]
            for tier in order:
                ok = (_load_siglip_tier() if tier == BACKEND_SIGLIP
                      else _load_clip_tier())
                if ok:
                    _state["backend"] = tier
                    break
            if _state["backend"] is None:
                _state["backend"] = BACKEND_HEURISTIC
                _state["device"] = _state["device"] or resolve_device()
    report = available()
    _announce_tier(report)
    if require_full and not report["mandatory_satisfied"]:
        errors = report.get("load_errors") or {}
        detail = "; ".join(f"{k}: {v}" for k, v in errors.items()) or "no tier attempted"
        raise SensoryGateUnavailable(
            f"DINOv2 is mandatory on this fleet and the {MANDATORY_BACKEND} tier is "
            f"not resident. Best available tier is '{report['tier']}' "
            f"(backend={report['backend']}, device={report['device']}). "
            f"dinov2={report['dinov2_model']} cached="
            f"{report['weights_cached']['dinov2']}, "
            f"siglip={report['siglip_model']} cached="
            f"{report['weights_cached']['siglip']}, "
            f"downloads_allowed={report['downloads_allowed']}. Load errors: {detail}")
    return report


def _degraded_reason(backend, tier):
    """The line that names DINOv2 out loud on every frame of a degraded run."""
    if backend == BACKEND_CLIP:
        what = ("running the CLIP fallback, so the DINOv2 semantic-novelty gate "
                "and the SigLIP aesthetic head are BOTH absent; novelty is CLIP "
                "image-embedding cosine instead")
    else:
        what = ("running the pure-pixel emergency tier, so the DINOv2 "
                "semantic-novelty gate and the SigLIP aesthetic head are BOTH "
                "absent; novelty is a handcrafted 128-d fingerprint and "
                "aesthetic is a pixel proxy, neither of which is a model score")
    return (f"DEGRADED: DINOv2 is mandatory on this fleet and is NOT loaded. "
            f"This node is {what}. Tier is '{tier}', not 'full'. On production "
            f"hardware this is a fault to fix, not a mode to run in; call "
            f"warm(require_full=True) at preflight to fail fast instead.")


def _load_image(image_path):
    """One decode, three views: model-sized PIL, 256px luma, 128px RGB, 96px LAB src."""
    img = Image.open(image_path)
    img.load()
    img = img.convert("RGB")

    w256 = 256
    h256 = max(1, round(w256 * img.height / max(1, img.width)))
    small = img.resize((w256, h256), Image.BILINEAR)
    rgb256 = np.asarray(small, dtype=np.float32)
    luma256 = rgb256.mean(-1)

    rgb128 = np.asarray(img.resize((128, 128), Image.BILINEAR), dtype=np.float32)
    luma64 = np.asarray(img.convert("L").resize((64, 64), Image.BILINEAR),
                        dtype=np.float32)
    rgb96 = np.asarray(img.resize((96, 96), Image.BILINEAR), dtype=np.float32) / 255.0
    return img, rgb256, luma256, rgb128, luma64, rgb96


def gate_scores(image_path, prompt=""):
    """Triage one settled frame. Never raises; a scorer must not kill a wave.

    Returns the contract dict. `passed` is the triage verdict: False means do
    not spend a jury pass on this frame -- and a jury pass now costs three VLM
    passes (Qwen3.8-27B structure, Pixtral-12B palette, Gemma-4-31B synthesis),
    so this decision is worth making well.

    `passed` == `not result["failures"]`, exactly. `reasons` is `failures` plus,
    on any non-mandatory tier, a leading "DEGRADED: ..." line naming DINOv2 --
    so a degraded run cannot be read as a healthy one, while a dev box can
    still triage. `notes` carries the softer disclosures: cold start,
    unmeasurable metrics, provisional calibration.
    """
    t0 = time.perf_counter()
    warm()
    backend = _state["backend"] or BACKEND_HEURISTIC
    degraded = backend != BACKEND_SIGLIP
    notes = []
    measured = {}

    try:
        img, rgb256, luma256, rgb128, luma64, rgb96 = _load_image(image_path)
    except Exception as exc:
        tier = TIER_LABEL.get(backend, "emergency")
        failure = f"the image could not be read: {type(exc).__name__}: {exc}"
        reasons = [failure]
        if backend != MANDATORY_BACKEND:
            reasons.insert(0, _degraded_reason(backend, tier))
        broken = {
            "aesthetic": 0.0, "novelty": 0.0, "adherence": 0.0,
            "palette_delta": 0.0, "backend": backend, "passed": False,
            "reasons": reasons,
            "latency_ms": round((time.perf_counter() - t0) * 1000.0, 3),
            "degraded": True,
            "failures": [failure],
            "tier": tier,
            "mandatory_satisfied": backend == MANDATORY_BACKEND,
            "notes": ["no measurement was possible; nothing was written to memory"],
            "measured": {k: "unavailable" for k in
                         ("aesthetic", "novelty", "adherence", "palette_delta")},
            "calibration": "n/a", "parts": {}, "memory_store": "none",
            "device": _state["device"] or resolve_device(),
            "image_path": str(image_path),
        }
        log.error(f"unreadable frame {image_path}: {type(exc).__name__}: {exc}")
        _log_frame(broken)
        return broken

    # ---- palette evidence: always real, on every tier ----------------------
    palette = palette_histogram(rgb96)
    measured["palette_delta"] = "lab-histogram-hellinger"

    heur_aesthetic, heur_parts = heuristic_aesthetic(rgb256, luma256)
    parts = {"heuristic": heur_parts}

    aesthetic = heur_aesthetic
    adherence = NEUTRAL_SCORE
    calibration = "provisional"
    namespace_key = "heuristic"
    namespace = "heuristic:fp128"
    embedding = None

    torch = _try_module("torch") if backend != BACKEND_HEURISTIC else None

    if backend == BACKEND_SIGLIP and torch is not None and _state["siglip"]:
        try:
            smodel, sproc, pos_emb, neg_emb = _state["siglip"]
            dmodel, dproc, dsize, dmean, dstd = _state["dinov2"]
            device, dtype = _state["device"], _state["dtype"]
            ssize, smean, sstd = _proc_geometry(sproc, 224)
            with _lock, torch.inference_mode():
                spx = _prep(img, ssize, smean, sstd, torch, device, dtype)
                iemb = _unit(_feat(smodel.get_image_features(pixel_values=spx)).float())
                aesthetic, margin = _probe_quality(iemb, pos_emb, neg_emb,
                                                   SIGLIP_QUAL_MID, SIGLIP_QUAL_TEMP)
                temb = _text_embedding(BACKEND_SIGLIP, prompt or "")
                raw_adh = float((iemb @ temb.T).mean())
                adherence = round(100.0 * _clamp01(
                    (raw_adh - SIGLIP_ADH_LO) / (SIGLIP_ADH_HI - SIGLIP_ADH_LO)), 2)

                dpx = _prep(img, dsize, dmean, dstd, torch, device, dtype)
                dout = dmodel(pixel_values=dpx)
                cls = getattr(dout, "pooler_output", None)
                if cls is None:
                    cls = dout.last_hidden_state[:, 0]
                cls = _unit(cls.float())
                embedding = cls[0].detach().cpu().numpy().astype(np.float32)
            measured["aesthetic"] = "siglip-zeroshot-craft-probes"
            measured["adherence"] = "siglip-image-text-cosine"
            measured["novelty"] = f"dinov2-{_dinov2_tier()}-cls-cosine"
            namespace_key = "dinov2"
            namespace = f"dinov2:{_dinov2_tier()}:{embedding.size}"
            parts["siglip"] = {"probe_margin": round(margin, 5),
                               "adherence_cosine": round(raw_adh, 5)}
            notes.append("SigLIP/DINOv2 score mapping is provisional: the 0-100 "
                         "constants are engineering defaults, not a measured "
                         "p5/p95 sweep over this corpus")
        except Exception as exc:
            degraded = True
            backend = BACKEND_HEURISTIC
            notes.append(f"the SigLIP/DINOv2 pass failed at runtime and this frame "
                         f"fell back to the heuristic tier: {type(exc).__name__}: {exc}")
            aesthetic, adherence, embedding = heur_aesthetic, NEUTRAL_SCORE, None

    elif backend == BACKEND_CLIP and torch is not None and _state["clip"]:
        try:
            model, proc, pos_emb, neg_emb = _state["clip"]
            device, dtype = _state["device"], _state["dtype"]
            csize, cmean, cstd = _proc_geometry(proc.image_processor
                                                if hasattr(proc, "image_processor")
                                                else proc, 224)
            with _lock, torch.inference_mode():
                px = _prep(img, csize, cmean, cstd, torch, device, dtype)
                iemb = _unit(_feat(model.get_image_features(pixel_values=px)).float())
                aesthetic, margin = _probe_quality(iemb, pos_emb, neg_emb,
                                                   QUAL_MID, QUAL_TEMP)
                temb = _text_embedding(BACKEND_CLIP, prompt or "")
                raw_adh = float((iemb @ temb.T).mean())
                adherence = round(100.0 * _clamp01(
                    (raw_adh - ADH_LO) / (ADH_HI - ADH_LO)), 2)
                embedding = iemb[0].detach().cpu().numpy().astype(np.float32)
            measured["aesthetic"] = "clip-zeroshot-craft-probes"
            measured["adherence"] = "clip-image-text-cosine"
            measured["novelty"] = "clip-image-embedding-cosine"
            calibration = "measured" if _PROBE_SOURCE == "atelier.aesthetic" else "vendored-measured"
            namespace_key = "clip"
            namespace = f"clip:vit-b32:{embedding.size}"
            parts["clip"] = {"probe_margin": round(margin, 5),
                             "adherence_cosine": round(raw_adh, 5)}
            notes.append("running the CLIP fallback tier, not SigLIP/DINOv2: these "
                         "are real model scores, calibrated on 30 sampled cards, "
                         "but they are not the declared sensory gate")
        except Exception as exc:
            degraded = True
            backend = BACKEND_HEURISTIC
            notes.append(f"the CLIP pass failed at runtime and this frame fell back "
                         f"to the heuristic tier: {type(exc).__name__}: {exc}")
            aesthetic, adherence, embedding = heur_aesthetic, NEUTRAL_SCORE, None

    if embedding is None:
        backend = BACKEND_HEURISTIC
        degraded = True
        embedding, fp_source = _fingerprint(str(image_path), rgb128, luma64)
        namespace_key = "heuristic"
        namespace = f"heuristic:fp128:{embedding.size}"
        measured["aesthetic"] = "pixel-heuristic"
        measured["novelty"] = f"handcrafted-fp128-cosine ({fp_source})"
        measured["adherence"] = "unavailable"
        calibration = "heuristic"
        aesthetic = heur_aesthetic
        adherence = NEUTRAL_SCORE
        notes.append("no vision model is resident, so this is the pure-pixel "
                     "heuristic tier: aesthetic is a pixel proxy and NOT a model "
                     "score, and adherence cannot be measured at all")

    # ---- novelty and palette against the rolling memory ---------------------
    history, palettes, store = _recent(namespace, MEMORY_WINDOW)
    novelty, nov_parts = _novelty_from(embedding, history, namespace_key)
    palette_delta, pal_parts = _palette_delta_from(palette, palettes)
    parts["novelty"] = nov_parts
    parts["palette"] = pal_parts
    parts["namespace"] = namespace

    if nov_parts["cold_start"]:
        notes.append(f"novelty is a cold-start placeholder: only "
                     f"{nov_parts['neighbours']} frame(s) of this embedding kind "
                     f"are in memory, fewer than the {COLD_START_FRAMES} needed")
    if pal_parts["cold_start"]:
        notes.append("palette delta is a cold-start placeholder: the rolling "
                     "memory has no comparable palette yet")

    # ---- the verdict --------------------------------------------------------
    # `failures` are gate failures and are the only thing `passed` depends on.
    # `reasons` is failures plus the mandatory-tier degradation line, because
    # the operator requires a degraded run to say DINOv2 out loud on every
    # frame -- but a dev box must still be able to triage, so degradation
    # alone never rejects a frame.
    failures = []
    if aesthetic < PASS_AESTHETIC:
        label = ("craft probe score" if measured.get("aesthetic", "").endswith("probes")
                 else "pixel-heuristic craft proxy")
        failures.append(
            f"aesthetic {aesthetic:.1f} is under the {PASS_AESTHETIC:.1f} floor "
            f"({label}); the frame does not look finished enough to be worth a "
            f"jury pass")
    if not nov_parts["cold_start"] and novelty < PASS_NOVELTY:
        failures.append(
            f"novelty {novelty:.1f} is under the {PASS_NOVELTY:.1f} floor; this "
            f"frame is a near-duplicate of something already in the last "
            f"{nov_parts['neighbours']} frames (closest cosine distance "
            f"{nov_parts['d_min']})")
    if measured.get("adherence") == "unavailable":
        notes.append("adherence was excluded from the verdict because no "
                     "image-text model was available to measure it")
    elif adherence < PASS_ADHERENCE:
        failures.append(
            f"adherence {adherence:.1f} is under the {PASS_ADHERENCE:.1f} floor; "
            f"the render drifted away from what the prompt asked for")
    if not pal_parts["cold_start"] and palette_delta < PASS_PALETTE_DELTA:
        failures.append(
            f"palette delta {palette_delta:.1f} is under the "
            f"{PASS_PALETTE_DELTA:.1f} floor; the colourway is nearly identical "
            f"to a recent frame, which is what mode collapse looks like early")
    if heur_parts["flat_frame"]:
        failures.append("the frame is nearly flat (luma std "
                       f"{heur_parts['luma_std']:.2f}); this is a blank or failed "
                       "render, not an image")

    passed = not failures

    reasons = list(failures)
    tier = TIER_LABEL.get(backend, "emergency")
    if backend != MANDATORY_BACKEND:
        reasons.insert(0, _degraded_reason(backend, tier))

    scores = {"aesthetic": aesthetic, "novelty": novelty,
              "adherence": adherence, "palette_delta": palette_delta}
    store_used = _remember(str(image_path), str(image_path), namespace, embedding,
                           palette, scores, backend, degraded, passed)

    result = {
        "aesthetic": float(aesthetic),
        "novelty": float(novelty),
        "adherence": float(adherence),
        "palette_delta": float(palette_delta),
        "backend": backend,
        "passed": bool(passed),
        "reasons": reasons,
        "latency_ms": round((time.perf_counter() - t0) * 1000.0, 3),
        "degraded": bool(degraded),
        # extras, for callers that want the provenance rather than just the number
        "failures": failures,          # `passed` is exactly `not failures`
        "tier": tier,
        "mandatory_satisfied": backend == MANDATORY_BACKEND,
        "notes": notes,
        "measured": measured,
        "calibration": calibration,
        "device": _state["device"] or resolve_device(),
        "memory_store": store_used if store_used else store,
        "parts": parts,
        "image_path": str(image_path),
    }
    _log_frame(result)
    return result


def is_mode_collapsed(window=3):
    """True when the last `window` frames stopped exploring.

    Two independent ways to detect it, either sufficient: the semantic
    embeddings have converged (novelty floor), or the colourways have
    (palette floor). The second catches the case the first misses -- a run that
    keeps changing subject while every frame comes out the same teal-and-amber.
    """
    window = max(1, int(window))
    con = _connect(create=False)
    rows = []
    if con is None:
        rows = [(r["novelty"], r["palette_delta"])
                for r in list(_process_memory)[-window:]]
    else:
        try:
            rows = con.execute(
                "SELECT novelty, palette_delta FROM sensory_embeddings "
                "ORDER BY id DESC LIMIT ?", (window,)).fetchall()
        except Exception:
            rows = []
        finally:
            try:
                con.close()
            except Exception:
                pass
    if len(rows) < window:
        return False
    nov = [float(r[0]) for r in rows]
    pal = [float(r[1]) for r in rows]
    return ((sum(nov) / len(nov)) < MODE_COLLAPSE_NOVELTY
            or (sum(pal) / len(pal)) < MODE_COLLAPSE_PALETTE)


# ------------------------------------------------------------------------- demo


def _synthetic_frames(out_dir):
    """A handful of frames that exercise the gate the way real output would."""
    paths = []
    rng = np.random.default_rng(7)

    def save(name, arr):
        p = pathlib.Path(out_dir) / name
        Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).save(p)
        paths.append(str(p))

    h, w = 512, 512
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)

    # 1. a plausible render: warm gradient, a focal disc, quiet lower third
    base = np.zeros((h, w, 3), np.float32)
    base[..., 0] = 90 + 110 * (1 - yy / h)
    base[..., 1] = 70 + 80 * (1 - yy / h)
    base[..., 2] = 60 + 40 * (xx / w)
    disc = ((xx - w * 0.42) ** 2 + (yy - h * 0.36) ** 2) < (h * 0.17) ** 2
    base[disc] = np.array([238, 226, 198], np.float32)
    ring = (np.abs(np.sqrt((xx - w * 0.42) ** 2 + (yy - h * 0.36) ** 2)
                   - h * 0.22) < 2.0)
    base[ring] = np.array([32, 30, 40], np.float32)
    base[: int(h * 0.66)] += rng.normal(0, 7, (int(h * 0.66), w, 1))
    save("frame_composed.png", base)

    # 2. a near-duplicate of 1: this is what the novelty gate exists to catch
    save("frame_near_duplicate.png", base + rng.normal(0, 2.5, (h, w, 3)))

    # 3. a genuinely different colourway and composition
    other = np.zeros((h, w, 3), np.float32)
    other[..., 0] = 30 + 60 * np.sin(xx / 28.0) ** 2
    other[..., 1] = 120 + 70 * np.cos(yy / 34.0) ** 2
    other[..., 2] = 150 + 60 * np.sin((xx + yy) / 40.0) ** 2
    other[int(h * 0.2):int(h * 0.55), int(w * 0.55):int(w * 0.9)] = \
        np.array([250, 120, 60], np.float32)
    save("frame_other_palette.png", other)

    # 4. a dead render: flat plate, the cheapest thing to reject
    save("frame_flat.png", np.full((h, w, 3), 128.0, np.float32))

    # 5. a third distinct look, so the rolling memory clears cold start
    third = np.zeros((h, w, 3), np.float32)
    third[..., 0] = 40 + 150 * np.abs(np.sin(xx / 90.0 + yy / 240.0))
    third[..., 1] = 35 + 90 * np.abs(np.cos(yy / 70.0))
    third[..., 2] = 55 + 120 * np.abs(np.sin((xx * 0.6 - yy) / 110.0))
    third[: int(h * 0.62)] += rng.normal(0, 16, (int(h * 0.62), w, 3))
    third[int(h * 0.18):int(h * 0.5), int(w * 0.2):int(w * 0.46)] = \
        np.array([18, 22, 30], np.float32)
    save("frame_third_look.png", third)

    prompts = [
        "a sumi-e ink study of a crane over still water, wide margin, muted indigo",
        "a sumi-e ink study of a crane over still water, wide margin, muted indigo",
        "a riso-printed botanical poster, acid teal and orange, bold flat shapes",
        "an oil portrait in raking window light, umber ground",
        "a woodblock harbour at dusk, layered ochre and slate, coarse grain",
    ]
    return paths, prompts


if __name__ == "__main__":
    import json
    import tempfile

    work = tempfile.mkdtemp(prefix="sensory_gates_")
    os.environ.setdefault("SENSORY_GATES_DB", os.path.join(work, "jury.sqlite3"))
    if not LOG_EVENTS_PATH:
        LOG_EVENTS_PATH = os.path.join(work, "sensory_events.jsonl")

    print("=" * 78)
    print("sensory_gates self-test")
    print("=" * 78)
    print("available():")
    print(json.dumps(available(), indent=2, default=str))

    print("\nwarm():")
    print(json.dumps(warm(), indent=2, default=str))

    print("\nwarm(require_full=True)  -- the production preflight contract:")
    try:
        warm(require_full=True)
        print("  OK: the mandatory SigLIP + DINOv2 tier is resident.")
    except SensoryGateUnavailable as exc:
        print(f"  raised SensoryGateUnavailable, as it must on a node without "
              f"DINOv2:\n    {exc}")

    frames, prompts = _synthetic_frames(work)

    print("\n" + "=" * 78)
    print("gating synthetic frames (rendered log above, full dicts below)")
    print("=" * 78)

    latencies = []
    results = []
    for path, prompt in zip(frames, prompts):
        result = gate_scores(path, prompt)
        latencies.append(result["latency_ms"])
        results.append(result)
        print("\n" + "-" * 78)
        print(os.path.basename(path))
        print(json.dumps(result, indent=2, default=str))

    # The novelty gate needs COLD_START_FRAMES neighbours before it can fire, so
    # re-gate the near-duplicate now that the memory is warm. This is the whole
    # point of the gate: the second sighting of a frame must be rejected.
    print("\n" + "=" * 78)
    print("re-gating the near-duplicate now that the rolling memory is warm")
    print("=" * 78)
    dup = gate_scores(frames[1], prompts[1])
    print(json.dumps({k: dup[k] for k in
                      ("aesthetic", "novelty", "adherence", "palette_delta",
                       "backend", "passed", "reasons", "latency_ms", "degraded",
                       "failures", "tier", "mandatory_satisfied")},
                     indent=2, default=str))
    print("novelty parts:", json.dumps(dup["parts"]["novelty"], default=str))
    print("palette parts:", json.dumps(dup["parts"]["palette"], default=str))

    # Measured latency for the heuristic tier on this box.
    print("\n" + "=" * 78)
    os.environ["SENSORY_GATES_LOG"] = "0"
    globals()["LOG_ENABLED"] = False
    bench = []
    for i in range(30):
        bench.append(gate_scores(frames[i % len(frames)],
                                 prompts[i % len(prompts)])["latency_ms"])
    globals()["LOG_ENABLED"] = True
    bench.sort()
    print(f"first-call latency (includes warm + import): {latencies[0]:.2f} ms")
    print(f"heuristic tier, 30 frames, 512x512 PNG, CPU-only: "
          f"min {bench[0]:.2f} / median {bench[len(bench) // 2]:.2f} / "
          f"p95 {bench[int(len(bench) * 0.95)]:.2f} / max {bench[-1]:.2f} ms")
    print(f"is_mode_collapsed(window=3) -> {is_mode_collapsed(3)}")
    print(f"logger source: {log.source}  (arcane_log present: "
          f"{log.real is not None})")
    print(f"memory: {memory_stats()}")
    try:
        with open(LOG_EVENTS_PATH, encoding="utf-8") as fh:
            lines = fh.readlines()
        print(f"structured events: {len(lines)} JSONL records at {LOG_EVENTS_PATH}")
        print("last record:", lines[-1].strip()[:300] + " ...")
    except Exception as exc:
        print(f"structured events unavailable: {exc}")
    print(f"scratch: {work}")
