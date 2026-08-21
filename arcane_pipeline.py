#!/usr/bin/env python3
"""Arcane pipeline -- the runner that binds draft -> atlas -> jury -> promote -> publish.

The pieces already existed and nothing bound them:

  * ``atlas_drafts/arcane_*.json``  -- eight ratified latent-sphere drafts.
  * ``worker.py``                   -- the real SO(4) latent cartographer and the
                                       ``atlas-xframe-cache`` cross-frame residual engine
                                       (``submit_atlas`` / ``_render_atlas``).
  * ``cmd/flux/main.go``            -- ``flux atlas sphere --draft <path>``, which submits
                                       over the ``.fluxd`` unix socket.
  * ``perpetual_feeder.py``         -- a perpetual loop that never once mentions Arcane.

This module is the spine. It resolves a draft, builds the exact payload
``worker.submit_atlas`` consumes, submits it, follows the render, runs settled cells
through the jury, routes them by tier, optionally refines crowned cells with Kontext, and
publishes a live run manifest for the ``/arcane`` surface.

Transport
---------
``internal/daemon/daemon.go`` speaks one newline-delimited JSON object per connection over
a unix socket, and adopts a live ``flux-gpu{N}`` fleet worker before falling back to
``flux.sock``. The ``flux`` binary is preferred for submission and is used whenever it can
express the request. It cannot express ``shard_id``/``shard_total``/``shard_block``,
``batch_size`` or ``sample_mode``, and it prints human tables rather than JSON for job
state, so this module also carries a ~20-line UDS client for those cases and for polling.
``--transport`` forces one or the other.

Roster and hardware
-------------------
The model roster, the VRAM arithmetic and the hardware facts are DATA, read from
``jury_continuum.toml`` through ``pipeline_paths.load_continuum()``. Three profiles exist
-- ``rtx-pro-6000`` (sm_120, 96 GiB, 4-bit judges), ``b200`` (sm_100, 192 GiB, FP8 judges)
and ``b300`` (sm_100, 288 GiB, BF16 judges). Kontext is the only toggle; flux, witness,
governor, pixtral and the DINOv2/SigLIP gates are mandatory in every profile. The
generator stays BF16 everywhere per ``docs/BF16_NATIVE_PRECISION_SPEC.md``.

Environment reality
-------------------
Everything here is stdlib-only and import-clean on Python 3.9 with no numpy, no Pillow, no
torch, no CUDA and no running daemon. Sibling modules built alongside this one
(``pipeline_paths``, ``moj_evaluator``, ``sensory_gates``, ``arcane_aesthetic``,
``arcane_log``, ``provision_surfaces.py``) are used defensively and their absence is
reported, never faked.

Honesty rules this module holds to
----------------------------------
1. Cache hit rate and seconds-per-cell come from the worker's own job record and progress
   file. The protocol spec's 94.1% / 1.50 s-per-cell figures are printed only when
   explicitly labelled as an unverified spec claim.
2. A jury verdict is never invented. An unreachable jury marks cells ``unscored``.
3. Hardware that cannot be detected is reported as undetectable, not assumed.
4. ``--dry-run`` prints the exact payload that would go over the wire and touches nothing.
"""

import argparse
import glob
import contextlib
import importlib
import importlib.util
import io
import json
import os
import pathlib
import random
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

__version__ = "1.1.0"

# ---------------------------------------------------------------------------
# Protocol constants
# ---------------------------------------------------------------------------

# docs/ARCANE_LATENT_CARTOGRAPHY_PROTOCOL_SPEC.md section 2.2.
DEFAULT_ADAPTER = "atlas-xframe-cache"
DEFAULT_CACHE_THRESHOLD = 0.30
DEFAULT_CACHE_DOWNSAMPLE = 1
DEFAULT_CACHE_WARMUP = 0

# Claims made by the spec. Printed only when labelled as claims.
SPEC_CLAIM_HIT_RATE = 0.941
SPEC_CLAIM_SECONDS_PER_CELL = 1.50

# jury_continuum.toml [verdict] masterpiece_threshold, on a 0-100 scale.
DEFAULT_CROWN_THRESHOLD = 90.0
# Protocol spec section 5: "< 7.0 Drift" on the jury's 0-10 scale.
DEFAULT_DRIFT_THRESHOLD = 70.0

TIER_CROWNED = "crowned"
TIER_KEPT = "kept"
TIER_DRIFT = "drift"
TIER_UNSCORED = "unscored"
TIER_ORDER = (TIER_CROWNED, TIER_KEPT, TIER_DRIFT, TIER_UNSCORED)

# Tenant order for every roster and budget table. Kontext is last because it is
# the only optional one.
TENANT_ORDER = ("flux", "witness", "governor", "pixtral", "gates", "kontext")
MANDATORY_TENANTS = ("flux", "witness", "governor", "pixtral", "gates")

DEFAULT_PROFILE = "rtx-pro-6000"

# ---------------------------------------------------------------------------
# The three modes.
#
# Operator direction: "we should have arcane character, arcane latent, and arcane
# scenes". These are not presets over one runner -- they have DIFFERENT objective
# functions, and one of them inverts a gate the other two rely on.
#
# The finding that forces the split lives in worker.py:1645:
#
#     prompt_changed = previous_prompt_text is not None and batch_prompts[0] != previous_prompt_text
#     if prompt_changed:
#         xframe_cache_context.clear_buffers()
#
# Four drafts carry eight `view_prompts` each (yaw_buckets_64, yaw_hard_64,
# wide_space_64, animation_still_24). They steer rotation with TEXT, so the
# cross-frame residual cache is flushed eight times per orbit.
#
#   * In `character` that is fatal: the residual cache IS the continuity
#     mechanism, so flushing it is what breaks identity. Rotation must come from
#     SO(4) geometry (rates / offsets / shell_coupling / seed_lock) instead.
#   * In `scenes` the same view_prompts are a legitimate feature.
#
# Same code, opposite verdict by mode. Encoded below as per-mode draft rules.
# ---------------------------------------------------------------------------

MODE_CHARACTER = "character"
MODE_LATENT = "latent"
MODE_SCENES = "scenes"

MODES = {
    MODE_CHARACTER: {
        "objective": "identity coherence across a rotation orbit",
        # Adjacent frames SHOULD look alike. A novelty gate reading high inside an
        # orbit is a failure signal here, not a success signal.
        "novelty_polarity": "inverted",
        "cache_role": "load-bearing -- residual persistence is what holds identity constant",
        "view_prompts": "refuse",
        "size": 768,
        "cells": 64,
        "layout": "balanced",
        "select": "dense",
        "adapter": DEFAULT_ADAPTER,
        "cache_threshold": DEFAULT_CACHE_THRESHOLD,
        "jury_weight": "identity-led; per-frame Fortiche is necessary but not sufficient",
        "drafts": ("turntable", "turntable_elliptic", "animation_still_24",
                   "yaw_buckets", "yaw_hard"),
    },
    MODE_LATENT: {
        "objective": "novelty and exploration -- find the good regions of the manifold",
        "novelty_polarity": "normal",
        "cache_role": "pure speedup",
        "view_prompts": "allow",
        "size": 512,
        "cells": 0,
        "layout": "dense",
        "select": "",
        "adapter": DEFAULT_ADAPTER,
        "cache_threshold": DEFAULT_CACHE_THRESHOLD,
        "jury_weight": "novelty-led; is_mode_collapsed() is a true signal here",
        "drafts": ("65k", "wide_space"),
    },
    MODE_SCENES: {
        "objective": "composition and world -- Piltover <-> Zaun, the glass garden",
        "novelty_polarity": "mild",
        "cache_role": "speedup; prompt variation is expected and its cache flush is fine",
        "view_prompts": "expected",
        "size": 1024,
        "cells": 256,
        "layout": "balanced",
        "select": "",
        "adapter": DEFAULT_ADAPTER,
        "cache_threshold": DEFAULT_CACHE_THRESHOLD,
        "jury_weight": "Fortiche-conformance led; novelty mild",
        "drafts": ("rose_princess_hybrid_64",),
    },
}

# Drafts that steer rotation with text rather than geometry. Named explicitly so
# the check does not depend on the file happening to be readable.
VIEW_PROMPT_DRAFTS = ("yaw_buckets", "yaw_hard", "wide_space", "animation_still_24")

SURFACE_ENDPOINTS = {
    "portal": "https://b300.influx.vision/",
    "arcane": "https://b300.influx.vision/arcane",
    "jury": "https://b300.influx.vision/jury",
    "exhibition": "https://b300.influx.vision/exhibition",
    "engine": "https://b300.influx.vision/engine",
    "atlas": "https://b300.influx.vision/atlas/",
}

# Fallback facts, used only when jury_continuum.toml cannot be read at all. The
# toml is authoritative; this exists so preflight still says something true on a
# checkout with no config rather than crashing.
FALLBACK_PROFILES = {
    "rtx-pro-6000": {
        "gpu": "NVIDIA RTX PRO 6000 Blackwell Server Edition",
        "sm": "sm_120", "vram_gib": 96.0, "reserve_gib": 2.0,
        "vllm_min_version": "0.13.0",
        "native_nvfp4_dense": True, "native_nvfp4_moe": False,
        "prebuilt_wheel_available": False,
        "tenants": {
            "flux": {"model": "black-forest-labs/FLUX.1-dev", "precision": "bf16",
                     "vram_expected_gib": 35.0, "weights_gib": 35.0, "dense": True,
                     "enabled": True, "mandatory": True, "kind": "uds"},
            "witness": {"model": "unsloth/Qwen3.8-27B-NVFP4", "precision": "nvfp4",
                        "vram_expected_gib": 26.88, "weights_gib": 24.6, "dense": True,
                        "enabled": True, "mandatory": True, "kind": "vllm"},
            "governor": {"model": "nvidia/Gemma-4-31B-IT-NVFP4", "precision": "nvfp4",
                         "vram_expected_gib": 20.16, "weights_gib": 19.0, "dense": True,
                         "enabled": True, "mandatory": True, "kind": "vllm", "remote": False},
            "pixtral": {"model": "RedHatAI/pixtral-12b-quantized.w4a16", "precision": "w4a16",
                        "vram_expected_gib": 8.64, "weights_gib": 7.0, "dense": True,
                        "enabled": True, "mandatory": True, "kind": "vllm"},
            # 3.0 GiB, not the 2.5 the older continuum declared: DINOv2-Giant +
            # SigLIP-SO400M measure ~2.85 GiB resident (2.494 GiB of fp16 weights).
            # jury_continuum.toml is authoritative; this is only the no-config fallback.
            "gates": {"model": "facebook/dinov2-giant+google/siglip-so400m-patch14-384",
                      "precision": "fp16", "vram_expected_gib": 3.0, "weights_gib": 2.494,
                      "dense": True, "enabled": True, "mandatory": True, "kind": "inproc"},
            "kontext": {"model": "city96/FLUX.1-Kontext-dev-gguf", "precision": "q4_k_s",
                        "vram_expected_gib": 9.0, "weights_gib": 6.8, "dense": True,
                        "enabled": False, "mandatory": False, "toggleable": True, "kind": "uds"},
        },
    },
}

GOVERNOR_REMOTE_URL = "https://governor.influx.vision/v1"
GOVERNOR_LOCAL_URL = "http://127.0.0.1:8000/v1"

GENERATOR_Q4_WARNING = (
    "quantizing the GENERATOR costs precisely the fine impasto and brush-texture detail "
    "the Fortiche rubric exists to measure, and contradicts docs/BF16_NATIVE_PRECISION_SPEC.md; "
    "quantizing a judge is harmless, this is not"
)

# Fortiche aesthetic invariants, protocol spec section 3. The local prompt-mutation
# vocabulary used when the Governor is unreachable.
FORTICHE_INVARIANTS = {
    "brush": [
        "visible oil impasto layering with dry-brush breaks across the highlights",
        "gouache-weight paint edges, pigment sitting proud of the surface",
        "hand-painted 2D texture passes laid over the 3D character forms",
    ],
    "silhouette": [
        "sharp angular facial geometry with planar cheekbones",
        "hard-edged silhouette reading cleanly against the backdrop",
        "sculpted plane changes rather than soft rounded blending",
    ],
    "lighting": [
        "dual-source lighting: high-contrast ambient with a graphic rim light",
        "chiaroscuro key with a single saturated rim separating figure from ground",
        "hard graphic rim light tracing the jaw and shoulder line",
    ],
    "palette_zaun": [
        "Zaun undercity palette: toxic chemtech emerald, rusted iron, bruised violet",
        "chemtech emerald haze over rusted iron plating and violet undercity fog",
    ],
    "palette_piltover": [
        "Piltover apex palette: gilded brass, white marble, hextech cyan",
        "gilded brass filigree against white marble with hextech cyan glow",
    ],
    "anti_plastic": [
        "no smooth plastic skin, no flat photographic CGI render",
        "painterly skin with visible brush breaks, never airbrushed CGI",
    ],
}

DRAFT_ALIASES = {
    "65k": "arcane_italian_princess_65k",
    "turntable": "arcane_italian_princess_turntable_64",
    "turntable_elliptic": "arcane_italian_princess_turntable_elliptic_64",
    "yaw_buckets": "arcane_italian_princess_yaw_buckets_64",
    "yaw_hard": "arcane_italian_princess_yaw_hard_64",
    "wide_space": "arcane_italian_princess_wide_space_64",
    "animation_still_24": "arcane_italian_princess_animation_still_24",
    "rose_princess_hybrid_64": "arcane_rose_princess_hybrid_64",
}

OK, WARN, FAIL, SKIP, UNAVAIL = "OK", "WARN", "FAIL", "SKIP", "UNAVAIL"


# ---------------------------------------------------------------------------
# Defensive imports
# ---------------------------------------------------------------------------

_MODULE_CACHE = {}


def module_present(name):
    """True when ``name`` is importable, without actually importing it."""
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def load_module(name):
    """Import ``name`` lazily. Returns None (never raises) when unavailable.

    Sibling pipeline modules are owned by other agents and may not exist yet, may
    pull in torch, or may reach for a GPU at import time. None of that may take
    this module down.
    """
    if name in _MODULE_CACHE:
        return _MODULE_CACHE[name]
    try:
        module = importlib.import_module(name)
    except Exception:
        module = None
    _MODULE_CACHE[name] = module
    return module


# ---------------------------------------------------------------------------
# Logging -- arcane_log when present, a faithful plain-text fallback otherwise
# ---------------------------------------------------------------------------


class Log(object):
    """Proxy onto ``arcane_log.get_logger``.

    Agent 7 owns ``arcane_log.py`` and is writing it concurrently, so every call is
    attempted against the real logger and silently falls back per-method. That means a
    half-built module degrades one renderer at a time instead of taking the run down.
    """

    def __init__(self, name="arcane"):
        self.impl = None
        self.backend = "plain"
        module = load_module("arcane_log")
        factory = getattr(module, "get_logger", None) if module is not None else None
        if callable(factory):
            try:
                self.impl = factory(name)
                self.backend = "arcane_log"
            except Exception:
                self.impl = None

    # -- plumbing -----------------------------------------------------------

    def _try(self, method, *args, **kwargs):
        fn = getattr(self.impl, method, None) if self.impl is not None else None
        if not callable(fn):
            return False
        try:
            fn(*args, **kwargs)
            return True
        except TypeError:
            try:
                fn(*args)
                return True
            except Exception:
                return False
        except Exception:
            return False

    @staticmethod
    def line(text=""):
        sys.stdout.write(text + "\n")
        sys.stdout.flush()

    # -- renderers ----------------------------------------------------------

    def header(self, title, subtitle=""):
        if self._try("header", title, subtitle):
            return
        width = 78
        head = "== %s " % title
        self.line("")
        self.line(head + "=" * max(0, width - len(head)))
        if subtitle:
            self.line("   " + subtitle)

    def rule(self, title=""):
        if self._try("rule", title):
            return
        width = 78
        if not title:
            self.line("-" * width)
            return
        head = "-- %s " % title
        self.line(head + "-" * max(0, width - len(head)))

    def kv(self, key, value):
        if self._try("kv", key, value):
            return
        self.line("  %-22s %s" % (key, value))

    def table(self, headers, rows):
        if self._try("table", headers, rows):
            return
        self.line(render_table(rows, headers))

    def panel(self, title, body=""):
        if self._try("panel", title, body):
            return
        self.rule(title)
        for chunk in str(body).splitlines():
            self.line("  " + chunk)

    def progress(self, current, total, label="", detail="", rate=None):
        if self._try("progress", current, total, label=label, detail=detail, rate=rate):
            return
        pct = (100.0 * current / total) if total else 0.0
        bar_width = 28
        filled = int(bar_width * (current / float(total))) if total else 0
        bar = "#" * filled + "." * (bar_width - filled)
        self.line("  [%s] %6.2f%%  %s/%s  %s%s"
                  % (bar, pct, current, total if total else "?", label,
                     ("  " + detail) if detail else ""))

    def progress_done(self, summary=""):
        if self._try("progress_done", summary):
            return
        if summary:
            self.line("  " + str(summary))

    def vram(self, budget):
        if self._try("vram", budget):
            return
        render_vram_plain(self, budget)

    def roster(self, tenants):
        if self._try("roster", tenants):
            return
        rows = [[t.get("key", ""), t.get("precision", ""), "%.2f" % float(t.get("gib", 0.0) or 0.0),
                 t.get("model", ""), t.get("role", "")] for t in tenants]
        self.table(["tenant", "precision", "GiB", "model", "role"], rows)

    def verdict(self, receipt):
        if self._try("verdict", receipt):
            return
        self.line("  verdict %s" % json.dumps(receipt, sort_keys=True, default=str)[:300])

    def gates(self, receipt):
        if self._try("gates", receipt):
            return

    def fortiche(self, receipt):
        if self._try("fortiche", receipt):
            return

    def event(self, kind, **fields):
        if self._try("event", kind, **fields):
            return
        # No structured sink available; keep it out of the human stream entirely
        # rather than polluting it with JSON.
        return

    def warn(self, text):
        if self._try("warn", text):
            return
        self.line("  ! %s" % text)

    def error(self, text):
        if self._try("error", text):
            return
        self.line("  x %s" % text)


LOG = None


def log():
    global LOG
    if LOG is None:
        LOG = Log("arcane")
    return LOG


def render_table(rows, headers):
    if not rows:
        return "  (none)"
    body = [[("" if c is None else str(c)) for c in row] for row in rows]
    columns = list(zip(*([[str(h) for h in headers]] + body)))
    widths = [max(len(c) for c in col) for col in columns]
    out = ["  " + "  ".join(str(h).ljust(w) for h, w in zip(headers, widths)).rstrip()]
    out.append("  " + "  ".join("-" * w for w in widths))
    for row in body:
        out.append("  " + "  ".join(c.ljust(w) for c, w in zip(row, widths)).rstrip())
    return "\n".join(out)


def render_vram_plain(logger, budget):
    tenants = budget.get("tenants") or []
    rows = []
    for tenant in tenants:
        rows.append([
            tenant.get("key", ""),
            tenant.get("precision", ""),
            "%.2f" % float(tenant.get("gib", 0.0) or 0.0),
            "%.2f" % float(tenant.get("weights_gib", 0.0) or 0.0),
            tenant.get("model", "") or tenant.get("note", ""),
        ])
    capacity = float(budget.get("capacity_gib") or 0.0)
    usable = float(budget.get("usable_gib") or capacity)
    total = float(budget.get("total_gib") or 0.0)
    rows.append(["TOTAL", "", "%.2f" % total, "", "of %.2f GiB usable (%.1f GiB card)" % (usable, capacity)])
    logger.table(["tenant", "precision", "reserved", "weights", "model"], rows)
    bar_width = 44
    filled = int(bar_width * min(1.0, (total / usable) if usable else 0.0))
    bar = "#" * filled + "." * (bar_width - filled)
    logger.line("  [%s]  %.2f / %.2f GiB  free %.2f" % (bar, total, usable, usable - total))
    if budget.get("fits"):
        margin = float(budget.get("headroom_gib") or 0.0)
        if margin < 2.0:
            logger.warn("margin is %.2f GiB (%.1f%% of the card). This will run and it will be the "
                        "first thing to OOM. Consider --governor-remote." % (margin, 100.0 * margin / capacity))
    else:
        logger.error("REFUSED: %s" % budget.get("overcommit_reason", "configuration overcommits the card"))
        if budget.get("shed"):
            logger.table(
                ["shed this", "frees GiB", "tenant dropped", "what it costs you"],
                [[o.get("action", ""), "%.2f" % float(o.get("frees_gib") or 0.0),
                  o.get("tenant", ""), o.get("cost", "")] for o in budget["shed"]],
            )


# ---------------------------------------------------------------------------
# Minimal TOML reader
#
# tomllib landed in 3.11 and this must import on 3.9 (the only working interpreter
# on the dev Mac is /usr/bin/python3, 3.9.6, stdlib-only). pipeline_paths.load_continuum()
# is authoritative when present; this covers the subset jury_continuum.toml uses:
# nested sections, dotted keys, inline tables, multi-line arrays and quoted strings
# containing commas and hashes.
# ---------------------------------------------------------------------------


def read_continuum(path, notes=None):
    path = pathlib.Path(path)
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        if notes is not None:
            notes.append("could not read %s: %s" % (path, _short(exc)))
        return {}
    try:
        import tomllib  # type: ignore
        return tomllib.loads(text)
    except ImportError:
        pass
    except Exception as exc:
        if notes is not None:
            notes.append("tomllib rejected %s (%s); falling back to the minimal reader" % (path, _short(exc)))
    try:
        return _mini_toml(text)
    except Exception as exc:
        if notes is not None:
            notes.append("continuum parse failed (%s); continuing without it" % _short(exc))
        return {}


def _mini_toml(text):
    root = {}
    table = root
    pending = ""
    for raw in text.splitlines():
        line = (pending + " " + raw.strip()).strip() if pending else raw.strip()
        pending = ""
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and "=" not in line.split("]")[0]:
            name = line.split("]")[0].lstrip("[").strip()
            table = root
            for part in [p.strip().strip('"') for p in _split_dotted(name) if p.strip()]:
                node = table.get(part)
                if not isinstance(node, dict):
                    node = {}
                    table[part] = node
                table = node
            continue
        if "=" not in line:
            continue
        key, _sep, value = line.partition("=")
        value = value.strip()
        if _unbalanced(value):
            pending = line
            continue
        node = table
        parts = [p.strip().strip('"') for p in _split_dotted(key.strip())]
        for part in parts[:-1]:
            child = node.get(part)
            if not isinstance(child, dict):
                child = {}
                node[part] = child
            node = child
        node[parts[-1]] = _mini_toml_value(value)
    return root


def _split_dotted(text):
    out, buf, quoted = [], [], False
    for ch in text:
        if ch == '"':
            quoted = not quoted
        if ch == "." and not quoted:
            out.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    out.append("".join(buf))
    return out


def _unbalanced(text):
    depth, quoted = 0, False
    for ch in text:
        if ch == '"':
            quoted = not quoted
            continue
        if quoted:
            continue
        if ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
    return depth > 0


def _strip_comment(text):
    out, quoted = [], False
    for index, ch in enumerate(text):
        if ch == '"':
            quoted = not quoted
        if ch == "#" and not quoted:
            break
        out.append(ch)
    return "".join(out).strip()


def _mini_toml_value(text):
    text = _strip_comment(text).strip()
    if not text:
        return ""
    if text[0] == '"':
        end = text.rfind('"')
        return text[1:end] if end > 0 else text.strip('"')
    if text[0] == "'":
        return text.strip("'")
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        return [_mini_toml_value(p) for p in _split_top(inner)] if inner else []
    if text.startswith("{") and text.endswith("}"):
        out = {}
        for part in _split_top(text[1:-1]):
            if "=" not in part:
                continue
            k, _sep, v = part.partition("=")
            out[k.strip().strip('"')] = _mini_toml_value(v.strip())
        return out
    low = text.lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    return text


def _split_top(text):
    parts, depth, quoted, buf = [], 0, False, []
    for ch in text:
        if ch == '"':
            quoted = not quoted
        if not quoted:
            if ch in "[{":
                depth += 1
            elif ch in "]}":
                depth -= 1
            elif ch == "," and depth == 0:
                parts.append("".join(buf))
                buf = []
                continue
        buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return [p.strip() for p in parts if p.strip()]


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _as_path(value):
    if value in (None, ""):
        return None
    try:
        return pathlib.Path(str(value)).expanduser()
    except Exception:
        return None


def _first_env(*keys):
    for key in keys:
        value = os.environ.get(key, "")
        if value:
            return value
    return ""


def _dig(mapping, *keys):
    node = mapping
    for key in keys:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


def _short(exc):
    text = str(exc).strip() or exc.__class__.__name__
    return text if len(text) <= 200 else text[:197] + "..."


def _env_flag(name):
    value = os.environ.get(name, "")
    if not value:
        return None
    return value.strip().lower() not in ("0", "false", "no", "off")


def clamp_int(value, default, low, high):
    try:
        out = int(value)
    except (TypeError, ValueError):
        out = int(default)
    return max(low, min(high, out))


def clamp_float(value, default, low, high):
    try:
        out = float(value)
    except (TypeError, ValueError):
        out = float(default)
    return max(low, min(high, out))


def _wrap(text, width, indent):
    words, lines, line = str(text).split(), [], ""
    for word in words:
        if len(line) + len(word) + 1 > width:
            lines.append(line)
            line = word
        else:
            line = (line + " " + word).strip()
    if line:
        lines.append(line)
    return ("\n" + indent).join(lines) if lines else ""


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


class Paths(object):
    """Resolved filesystem layout.

    Order of authority: ``pipeline_paths`` (agent 5) -> environment -> repo-relative
    defaults. Nothing is hardcoded to ``/root``; the existing daemons do that and it
    is a bug, not a pattern to copy.
    """

    def __init__(self):
        self.source = "repo-relative defaults"
        self.home = pathlib.Path(__file__).resolve().parent
        self.out_dir = None
        self.atlas_dir = None
        self.jobs_ledger = None
        self.flux_bin = None
        self.fluxd_sock = None
        self.governor_base_url = None
        self.governor_remote = None
        self.continuum = {}          # pipeline_paths.load_continuum(): ACTIVE profile, resolved
        self.raw_continuum = {}      # jury_continuum.toml as written: every profile, unresolved
        self.continuum_source = ""
        self.notes = []

    @classmethod
    def resolve(cls):
        self = cls()
        pp = load_module("pipeline_paths")
        if pp is not None:
            self.source = "pipeline_paths"
            self.home = _as_path(getattr(pp, "FLUX_HOME", None)) or self.home
            self.out_dir = _as_path(getattr(pp, "OUT_DIR", None))
            self.atlas_dir = _as_path(getattr(pp, "ATLAS_DIR", None))
            self.jobs_ledger = _as_path(getattr(pp, "JOBS_LEDGER", None))
            self.flux_bin = _as_path(getattr(pp, "FLUX_BIN", None))
            self.fluxd_sock = _as_path(getattr(pp, "FLUXD_SOCK", None))
            self.governor_base_url = getattr(pp, "GOVERNOR_BASE_URL", None)
            remote = getattr(pp, "GOVERNOR_REMOTE", None)
            if remote is not None:
                self.governor_remote = bool(remote)
            loader = getattr(pp, "load_continuum", None)
            if callable(loader):
                try:
                    loaded = loader()
                    if isinstance(loaded, dict) and loaded:
                        self.continuum = loaded
                        self.continuum_source = "pipeline_paths.load_continuum()"
                except Exception as exc:
                    self.notes.append("pipeline_paths.load_continuum() failed: %s" % _short(exc))
        else:
            self.notes.append("pipeline_paths not importable; env + repo-relative fallbacks in use")

        env_home = _first_env("FLUX_HOME", "ARCANE_FLUX_HOME")
        if env_home:
            self.home = pathlib.Path(env_home).expanduser()

        if self.out_dir is None:
            env_out = _first_env("OUT_DIR", "FLUX_OUTPUT_DIR", "ARCANE_OUT_DIR")
            if env_out:
                self.out_dir = pathlib.Path(env_out).expanduser()
            else:
                fp = load_module("flux_paths")
                if fp is not None and hasattr(fp, "default_out_dir"):
                    try:
                        self.out_dir = pathlib.Path(fp.default_out_dir()).expanduser()
                    except Exception:
                        self.out_dir = None
                if self.out_dir is None:
                    self.out_dir = (pathlib.Path("/runs/flux-output") if pathlib.Path("/runs").exists()
                                    else pathlib.Path.home() / "Models" / "flux-output")

        if self.atlas_dir is None:
            self.atlas_dir = self.out_dir / "atlas"

        worker_name = self._worker_name()
        if self.fluxd_sock is None:
            env_sock = _first_env("FLUXD_SOCK", "FLUX_SOCKET")
            self.fluxd_sock = (pathlib.Path(env_sock).expanduser() if env_sock
                               else self.home / ".fluxd" / ("%s.sock" % worker_name))
        if self.jobs_ledger is None:
            env_ledger = _first_env("JOBS_LEDGER", "FLUX_JOBS_LEDGER")
            if env_ledger:
                self.jobs_ledger = pathlib.Path(env_ledger).expanduser()
            else:
                stem = "jobs.jsonl" if worker_name == "flux" else "%s.jobs.jsonl" % worker_name
                self.jobs_ledger = self.home / ".fluxd" / stem

        if self.flux_bin is None:
            env_bin = _first_env("FLUX_BIN", "ARCANE_FLUX_BIN")
            if env_bin:
                self.flux_bin = pathlib.Path(env_bin).expanduser()
            else:
                local = self.home / "flux"
                if local.exists() and os.access(str(local), os.X_OK):
                    self.flux_bin = local
                else:
                    found = shutil.which("flux")
                    self.flux_bin = pathlib.Path(found) if found else None

        # Always parse the toml directly as well: load_continuum() resolves only the
        # ACTIVE profile, and preflight has to be able to enumerate and describe the
        # others (and to recover hardware flags the resolver does not surface).
        toml_path = _as_path(_dig(self.continuum, "paths", "continuum_toml")) \
            or _as_path(_first_env("ARCANE_CONTINUUM")) or (self.home / "jury_continuum.toml")
        self.raw_continuum = read_continuum(toml_path, self.notes)
        if not self.continuum:
            self.continuum_source = ("jury_continuum.toml (minimal reader)" if self.raw_continuum
                                     else "none -- built-in fallback profile facts")
        return self

    def _worker_name(self):
        """Mirror daemon.go: adopt a live per-GPU fleet worker before the default."""
        if os.environ.get("FLUX_NO_FLEET_ADOPT"):
            return "flux"
        base = self.home / ".fluxd"
        for gpu in range(8):
            name = "flux-gpu%d" % gpu
            if socket_alive(base / ("%s.sock" % name), timeout=0.25):
                return name
        for gpu in range(8):
            name = "flux-gpu%d" % gpu
            if (base / ("%s.sock" % name)).exists():
                return name
        return "flux"

    @property
    def drafts_dir(self):
        return self.home / "atlas_drafts"

    @property
    def surface_dir(self):
        return self.out_dir / "arcane"

    @property
    def runs_dir(self):
        """Live run manifests. Deliberately separate from the static surface manifest
        provision_surfaces.py writes, so the dashboard can consume both unambiguously."""
        return self.surface_dir / "runs"

    @property
    def genome_path(self):
        return self.surface_dir / "crowned_genome.jsonl"

    def sphere_dir(self, job_id):
        return self.atlas_dir / ("%s.sphere" % job_id)

    def state_path(self, job_id):
        primary = self.sphere_dir(job_id) / "arcane_pipeline.state.json"
        if primary.exists():
            return primary
        fallback = self.fallback_state_path(job_id)
        return fallback if fallback.exists() else primary

    def fallback_state_path(self, job_id):
        return self.home / ".fluxd" / "arcane" / ("%s.state.json" % job_id)

    def as_dict(self):
        return {
            "source": self.source,
            "FLUX_HOME": str(self.home),
            "OUT_DIR": str(self.out_dir),
            "ATLAS_DIR": str(self.atlas_dir),
            "JOBS_LEDGER": str(self.jobs_ledger),
            "FLUX_BIN": str(self.flux_bin) if self.flux_bin else "",
            "FLUXD_SOCK": str(self.fluxd_sock),
            "continuum": self.continuum_source,
            "continuum_toml_parsed": bool(self.raw_continuum),
            "notes": self.notes,
        }


# ---------------------------------------------------------------------------
# Profiles and the roster
# ---------------------------------------------------------------------------


def _env_overrides(args):
    """Translate CLI flags into the environment contract pipeline_paths already
    defines, BEFORE anything imports or calls it.

    pipeline_paths.py documents ARCANE_PROFILE, ARCANE_KONTEXT,
    ARCANE_GOVERNOR_REMOTE and ARCANE_<TENANT>_PRECISION. Setting them here means
    agent 5's resolver, agent 5's vram_budget() and this module cannot disagree
    about what was requested -- there is exactly one source of truth and the flags
    feed it rather than shadowing it.
    """
    def put(name, value):
        if value is not None:
            os.environ[name] = str(value)

    put("ARCANE_PROFILE", getattr(args, "profile", None) or None)
    kontext = getattr(args, "kontext", None)
    if kontext is not None:
        put("ARCANE_KONTEXT", "1" if kontext else "0")
    remote = getattr(args, "governor_remote", None)
    if remote is not None:
        put("ARCANE_GOVERNOR_REMOTE", "1" if remote else "0")
    put("ARCANE_FLUX_PRECISION", getattr(args, "flux_precision", None) or None)
    put("ARCANE_KONTEXT_PRECISION", getattr(args, "kontext_precision", None) or None)
    layout = getattr(args, "layout", None)
    if layout:
        put("ARCANE_LAYOUT", layout)


class Profile(object):
    """One hardware profile, resolved.

    pipeline_paths.load_continuum() returns the ACTIVE profile already resolved
    (hardware facts, tenants with their variant applied, retired model list).
    pipeline_paths.vram_budget(profile=...) resolves any profile's tenant table.
    The raw jury_continuum.toml is parsed here only to enumerate profiles and to
    recover hardware flags for a profile that is not the active one.
    """

    def __init__(self, name, source="unknown"):
        self.name = name
        self.source = source
        self.hardware = {}
        self.tenants = {}
        self.retired_models = []
        self.layouts = {}

    # -- hardware facts, with honest absence -------------------------------
    def fact(self, key, default=None):
        value = self.hardware.get(key)
        return default if value is None else value

    @property
    def gpu(self):
        return str(self.fact("gpu", "") or "")

    @property
    def sm(self):
        return str(self.fact("sm", "") or "")

    @property
    def gpu_count(self):
        return int(self.fact("gpu_count", 1) or 1)

    @property
    def vram_gib(self):
        return float(self.fact("vram_gib", 0.0) or 0.0)

    @property
    def vram_per_gpu_gib(self):
        return float(self.fact("vram_per_gpu_gib", self.vram_gib) or self.vram_gib)

    @property
    def reserve_gib(self):
        return float(self.fact("reserve_gib", 0.0) or 0.0)

    @property
    def interconnect(self):
        """Declared interconnect. Never inferred -- an absent declaration is 'undeclared'."""
        for key in ("interconnect", "gpu_interconnect", "link"):
            value = self.fact(key)
            if value:
                return str(value)
        return "undeclared"

    @property
    def tensor_parallel_viable(self):
        return self.fact("tensor_parallel_viable")

    def tenant(self, key):
        value = self.tenants.get(key)
        return value if isinstance(value, dict) else None

    def description(self):
        return str(self.fact("description", "") or self.fact("notes", "") or "")


def _raw_profiles(paths):
    """profiles.<name> tables straight out of jury_continuum.toml."""
    raw = getattr(paths, "raw_continuum", None) or {}
    profiles = raw.get("profiles")
    return profiles if isinstance(profiles, dict) else {}


def profile_names(paths):
    names = set(_raw_profiles(paths))
    resolved = paths.continuum or {}
    if resolved.get("profile"):
        names.add(str(resolved["profile"]))
    if not names:
        names.update(FALLBACK_PROFILES)
    return sorted(names)


def resolve_profile_name(paths, requested):
    if requested:
        name = str(requested).strip()
    else:
        name = _first_env("ARCANE_PROFILE")
        if not name:
            pp = load_module("pipeline_paths")
            getter = getattr(pp, "active_profile", None) if pp is not None else None
            if callable(getter):
                try:
                    name = str(getter() or "")
                except Exception:
                    name = ""
        if not name:
            name = str((paths.continuum or {}).get("profile") or "") \
                or str(_dig(getattr(paths, "raw_continuum", {}) or {}, "continuum", "default_profile") or "") \
                or DEFAULT_PROFILE
    available = profile_names(paths)
    if name not in available:
        raise SystemExit("unknown profile %r; available: %s" % (name, ", ".join(available)))
    return name


def get_profile(paths, name):
    """Assemble a Profile from whichever sources are actually present."""
    profile = Profile(name)
    resolved = paths.continuum or {}
    raw = _raw_profiles(paths).get(name) or {}

    if resolved.get("profile") == name and isinstance(resolved.get("hardware"), dict):
        profile.hardware = dict(resolved["hardware"])
        profile.tenants = {k: dict(v) for k, v in (resolved.get("tenants") or {}).items()
                           if isinstance(v, dict)}
        profile.retired_models = list(resolved.get("retired_models") or [])
        profile.layouts = dict(resolved.get("layouts") or {})
        profile.source = "pipeline_paths.load_continuum()"
        # The raw toml carries hardware flags the resolver may not surface yet.
        for key, value in raw.items():
            if key != "tenants":
                profile.hardware.setdefault(key, value)
        return profile

    if raw:
        profile.hardware = {k: v for k, v in raw.items() if k != "tenants"}
        profile.layouts = dict(raw.get("layouts") or {})
        profile.source = "jury_continuum.toml"
        # Ask agent 5's resolver for this profile's tenant table so variant
        # selection stays in one place rather than being duplicated here.
        pp = load_module("pipeline_paths")
        fn = getattr(pp, "vram_budget", None) if pp is not None else None
        if callable(fn):
            try:
                budget = fn(profile=name, kontext=True, pixtral=True, governor_remote=False)
                for tenant in budget.get("tenants") or []:
                    key = str(tenant.get("name") or "")
                    if key:
                        profile.tenants[key] = dict(tenant)
            except Exception:
                profile.tenants = {}
        if not profile.tenants:
            profile.tenants = {k: _resolve_raw_tenant(raw, k, v)
                               for k, v in (raw.get("tenants") or {}).items() if isinstance(v, dict)}
        profile.retired_models = list(_dig(getattr(paths, "raw_continuum", {}) or {}, "retired", "models") or [])
        return profile

    fallback = FALLBACK_PROFILES.get(name)
    if fallback:
        profile.hardware = {k: v for k, v in fallback.items() if k != "tenants"}
        profile.tenants = {k: dict(v, name=k) for k, v in fallback["tenants"].items()}
        profile.source = "built-in fallback (no continuum readable)"
        return profile
    profile.source = "missing"
    return profile


def _resolve_raw_tenant(raw_profile, key, tenant):
    """Apply a tenant's declared precision against its own variants map."""
    view = dict(tenant)
    view["name"] = key
    precision = str(os.environ.get("ARCANE_%s_PRECISION" % key.upper(), "").strip()
                    or tenant.get("precision") or "").lower()
    variant = _dig(tenant, "variants", precision)
    view["precision"] = precision
    view["variants_available"] = sorted(tenant.get("variants", {})) \
        if isinstance(tenant.get("variants"), dict) else []
    if isinstance(variant, dict):
        view["model"] = variant.get("model", view.get("model", ""))
        if variant.get("vram_expected_gib") is not None:
            view["vram_expected_gib"] = float(variant["vram_expected_gib"])
        elif variant.get("gpu_memory_utilization") is not None:
            view["vram_expected_gib"] = round(float(variant["gpu_memory_utilization"])
                                              * float(raw_profile.get("vram_gib") or 0.0), 3)
        if variant.get("weights_gib") is not None:
            view["weights_gib"] = float(variant["weights_gib"])
        view["note"] = variant.get("note", view.get("note", ""))
        view["gguf_file"] = variant.get("gguf_file", "")
    view.pop("variants", None)
    return view


def tenant_view(profile, key, precision=None):
    """One tenant as a flat view. ``precision`` is honoured only when the raw
    variants map is still attached; otherwise the resolver already applied it."""
    tenant = profile.tenant(key) if isinstance(profile, Profile) else None
    if tenant is None:
        return None
    view = dict(tenant)
    view.setdefault("name", key)
    view["key"] = key
    view["gib"] = float(view.get("vram_expected_gib") or view.get("vram_gib") or 0.0)
    view["weights_gib"] = float(view.get("weights_gib") or 0.0)
    view["dense"] = bool(view.get("dense", True))
    view["enabled"] = bool(view.get("enabled", True))
    view["mandatory"] = bool(view.get("mandatory", key in MANDATORY_TENANTS))
    view["purpose"] = view.get("role", "")
    if precision and view.get("variants_available") and precision != view.get("precision"):
        view["precision_requested"] = precision
        view["note"] = ("requested precision %s; the resolver applied %s "
                        "(set ARCANE_%s_PRECISION before invoking to change it)"
                        % (precision, view.get("precision"), key.upper()))
    return view


def roster(profile, opts):
    """The tenants this configuration actually stands up, in table order."""
    out = []
    for key in TENANT_ORDER:
        view = tenant_view(profile, key,
                           opts.get("flux_precision") if key == "flux" else
                           opts.get("kontext_precision") if key == "kontext" else None)
        if view is None:
            continue
        if key == "kontext":
            view["enabled"] = bool(opts.get("kontext"))
        if key == "governor" and opts.get("governor_remote"):
            view["remote"] = True
            view["base_url"] = opts.get("governor_url")
            view["gib"] = 0.0
            view["vram_expected_gib"] = 0.0
            view["note"] = "off-card at %s; 0 GiB resident" % opts.get("governor_url")
        out.append(view)
    return out


def budget_allocated(budget):
    """The number co-tenancy has to respect. agent 5 calls it allocated_gib and
    uses total_gib for the card's own capacity, so never confuse the two."""
    for key in ("allocated_gib", "reserved_gib", "used_gib"):
        if budget.get(key) is not None:
            return float(budget[key])
    return float(budget.get("total_gib") or 0.0)


def budget_usable(budget):
    for key in ("usable_gib", "capacity_gib"):
        if budget.get(key) is not None:
            return float(budget[key])
    return float(budget.get("total_gib") or 0.0)


def budget_per_gpu(budget):
    """Per-GPU accounting when the budget carries it. `fits` is per-GPU, never
    aggregate: a 4x96 GiB fleet that overflows one card does not fit."""
    for key in ("per_gpu", "gpus", "devices", "placement"):
        value = budget.get(key)
        if isinstance(value, list) and value:
            return value
        if isinstance(value, dict) and value:
            return [dict(v, gpu=k) if isinstance(v, dict) else {"gpu": k, "value": v}
                    for k, v in sorted(value.items())]
    return []


def _tenant_names(tenants):
    """Tenant lists come back as dicts from some producers and bare names from others."""
    out = []
    for tenant in tenants or []:
        if isinstance(tenant, dict):
            out.append(str(tenant.get("name") or tenant.get("key") or tenant.get("model") or "?"))
        else:
            out.append(str(tenant))
    return ", ".join(out)


def local_vram_budget(profile, opts):
    """Continuum-driven fallback arithmetic, in agent 5's key names.

    Used only when pipeline_paths.vram_budget is unavailable. Reservations, not
    weights: vLLM reserves gpu_memory_utilization * vram_gib up front and that is
    the figure co-tenancy actually has to respect.
    """
    capacity = profile.vram_per_gpu_gib or profile.vram_gib
    reserve = profile.reserve_gib
    usable = round(capacity - reserve, 3)
    tenants = [t for t in roster(profile, opts) if t["enabled"]]
    allocated = round(sum(t["gib"] for t in tenants), 3)
    weights = round(sum(t["weights_gib"] for t in tenants), 3)
    fits = allocated <= usable
    over = round(allocated - usable, 3)

    shed = []
    if not fits:
        if opts.get("kontext"):
            kx = tenant_view(profile, "kontext", opts.get("kontext_precision"))
            if kx:
                shed.append({"action": "--no-kontext (ARCANE_KONTEXT=0)", "frees_gib": kx["gib"],
                             "tenant": kx.get("model", ""),
                             "cost": "no refinement pass on crowned frames; otherwise identical"})
        if not opts.get("governor_remote"):
            gov = tenant_view(profile, "governor")
            if gov:
                shed.append({"action": "--governor-remote (ARCANE_GOVERNOR_REMOTE=1)", "frees_gib": gov["gib"],
                             "tenant": gov.get("model", ""),
                             "cost": "governor round-trips leave the box and must tolerate being unreachable"})
        if opts.get("flux_precision") != "q4_k_s":
            flux = tenant_view(profile, "flux")
            if flux:
                shed.append({"action": "--flux-precision q4_k_s (ARCANE_FLUX_PRECISION=q4_k_s)",
                             "frees_gib": round(flux["gib"] - 18.0, 3), "tenant": flux.get("model", ""),
                             "cost": GENERATOR_Q4_WARNING})
        shed.sort(key=lambda s: -s["frees_gib"])

    minimal, freed = [], 0.0
    for option in shed:
        minimal.append(option)
        freed += option["frees_gib"]
        if freed >= over:
            break

    reason = ""
    if not fits:
        names = ", ".join("%s (frees %.2f GiB)" % (o["action"], o["frees_gib"]) for o in minimal) or "no option"
        reason = ("%s needs %.2f GiB of a %.2f GiB usable per-GPU budget (%.1f GiB card less %.1f GiB "
                  "reserve): over by %.2f GiB. Shed: %s"
                  % (profile.name, allocated, usable, capacity, reserve, over, names))

    return {
        "source": "arcane_pipeline.local_vram_budget (fallback)",
        "profile": profile.name,
        "gpu": profile.gpu,
        "sm": profile.sm,
        "total_gib": capacity,
        "reserve_gib": reserve,
        "usable_gib": usable,
        "allocated_gib": allocated,
        "weights_gib": weights,
        "free_gib": round(capacity - allocated, 3),
        "headroom_gib": round(usable - allocated, 3),
        "fits": fits,
        "kontext": bool(opts.get("kontext")),
        "pixtral": True,
        "governor_remote": bool(opts.get("governor_remote")),
        "overcommit_reason": reason,
        "tenants": tenants,
        "shed": shed,
        "shed_minimal": minimal,
    }


def budget_for(paths, profile, opts):
    """Prefer agent 5's arithmetic; render what it returns, never recompute it."""
    pp = load_module("pipeline_paths")
    fn = getattr(pp, "vram_budget", None) if pp is not None else None
    if callable(fn):
        attempts = (
            dict(profile=profile.name, kontext=opts["kontext"], pixtral=True,
                 governor_remote=opts["governor_remote"], layout=opts.get("layout")),
            dict(profile=profile.name, kontext=opts["kontext"], pixtral=True,
                 governor_remote=opts["governor_remote"]),
            dict(profile=profile.name, kontext=opts["kontext"], governor_remote=opts["governor_remote"]),
            dict(kontext=opts["kontext"], governor_remote=opts["governor_remote"]),
        )
        for kwargs in attempts:
            try:
                result = fn(**kwargs)
            except TypeError:
                continue
            except Exception as exc:
                budget = local_vram_budget(profile, opts)
                budget["source_note"] = "pipeline_paths.vram_budget raised (%s); local model used" % _short(exc)
                return budget
            if isinstance(result, dict) and "fits" in result:
                result.setdefault("source", "pipeline_paths.vram_budget")
                result.setdefault("source_note", "pipeline_paths.vram_budget")
                result.setdefault("profile", profile.name)
                result.setdefault("tenants", [])
                result.setdefault("shed", [])
                result.setdefault("shed_minimal", result.get("shed") or [])
                return result
        budget = local_vram_budget(profile, opts)
        budget["source_note"] = "pipeline_paths.vram_budget present but no signature matched; local model used"
        return budget
    budget = local_vram_budget(profile, opts)
    budget["source_note"] = "pipeline_paths.vram_budget unavailable; local continuum-driven model used"
    return budget


# ---------------------------------------------------------------------------
# Hardware detection -- sm_120 is not sm_100
# ---------------------------------------------------------------------------


def detect_gpu():
    """Best-effort compute-capability detection. Undetectable is a first-class answer."""
    out = {"detected": False, "reason": "", "name": "", "compute_cap": "", "sm": "", "memory_gib": None,
           "driver": "", "source": ""}
    exe = shutil.which("nvidia-smi")
    if exe:
        try:
            proc = subprocess.run(
                [exe, "--query-gpu=name,compute_cap,memory.total,driver_version", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=10,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                first = proc.stdout.strip().splitlines()[0]
                parts = [p.strip() for p in first.split(",")]
                out["detected"] = True
                out["source"] = "nvidia-smi"
                out["name"] = parts[0] if parts else ""
                if len(parts) > 1 and parts[1]:
                    out["compute_cap"] = parts[1]
                    out["sm"] = "sm_%s" % parts[1].replace(".", "")
                if len(parts) > 2 and parts[2]:
                    try:
                        out["memory_gib"] = round(float(parts[2]) / 1024.0, 2)
                    except ValueError:
                        pass
                if len(parts) > 3:
                    out["driver"] = parts[3]
                return out
            out["reason"] = (proc.stderr or proc.stdout or "nvidia-smi returned no rows").strip()
        except Exception as exc:
            out["reason"] = _short(exc)
    else:
        out["reason"] = "nvidia-smi not on PATH"

    torch = load_module("torch")
    if torch is not None:
        try:
            if torch.cuda.is_available():
                major, minor = torch.cuda.get_device_capability(0)
                out.update({"detected": True, "source": "torch.cuda",
                            "name": torch.cuda.get_device_name(0),
                            "compute_cap": "%d.%d" % (major, minor), "sm": "sm_%d%d" % (major, minor)})
                return out
            out["reason"] = out["reason"] or "torch is installed but reports no CUDA device"
        except Exception as exc:
            out["reason"] = out["reason"] or _short(exc)
    if not out["reason"]:
        out["reason"] = "no CUDA tooling on this host"
    return out


def detect_vllm():
    """Installed vLLM version, or why it could not be determined."""
    try:
        from importlib import metadata
        return {"present": True, "version": metadata.version("vllm"), "source": "importlib.metadata"}
    except Exception:
        pass
    module = load_module("vllm")
    version = getattr(module, "__version__", "") if module is not None else ""
    if version:
        return {"present": True, "version": version, "source": "vllm.__version__"}
    return {"present": False, "version": "", "source": "", "reason": "vllm not importable on this host"}


def detect_interconnect():
    """Probe the actual GPU interconnect. Never assume it from the model name.

    Public specs for RTX PRO 6000 Blackwell say no NVLink; the operator reports
    NVLink on their box. Both cannot be settled from a datasheet, so this asks the
    driver: `nvidia-smi nvlink --status` for live links, and `nvidia-smi topo -m`
    for the peer matrix (NV# means an NVLink hop; PIX / PXB / PHB / SYS mean PCIe).
    Agent 6's Go probe in `flux arcane provision` asks the same two questions.
    """
    out = {"detected": False, "kind": "unknown", "reason": "", "links": 0, "topo": "", "source": ""}
    exe = shutil.which("nvidia-smi")
    if not exe:
        out["reason"] = "nvidia-smi not on PATH"
        return out
    try:
        proc = subprocess.run([exe, "nvlink", "--status"], capture_output=True, text=True, timeout=10)
        text = (proc.stdout or "") + (proc.stderr or "")
        active = len(re.findall(r"Link\s+\d+:\s+[\d.]+\s*GB/s", text))
        if active:
            out.update({"detected": True, "kind": "nvlink", "links": active,
                        "source": "nvidia-smi nvlink --status"})
        elif "inactive" in text.lower() or "not supported" in text.lower():
            out.update({"detected": True, "kind": "pcie", "source": "nvidia-smi nvlink --status",
                        "reason": text.strip().splitlines()[0] if text.strip() else ""})
    except Exception as exc:
        out["reason"] = _short(exc)

    try:
        proc = subprocess.run([exe, "topo", "-m"], capture_output=True, text=True, timeout=10)
        if proc.returncode == 0 and proc.stdout.strip():
            body = proc.stdout
            out["topo"] = "NV" if re.search(r"\bNV\d+\b", body) else (
                "PIX/PXB/PHB/SYS" if re.search(r"\b(PIX|PXB|PHB|SYS|NODE)\b", body) else "")
            if out["topo"] == "NV" and out["kind"] != "nvlink":
                out.update({"detected": True, "kind": "nvlink", "source": "nvidia-smi topo -m"})
            elif out["topo"] and out["kind"] == "unknown":
                out.update({"detected": True, "kind": "pcie", "source": "nvidia-smi topo -m"})
    except Exception as exc:
        out["reason"] = out["reason"] or _short(exc)
    if not out["detected"] and not out["reason"]:
        out["reason"] = "nvidia-smi answered but neither probe was conclusive"
    return out


def detect_fleet_gpus(profile=None):
    """GPU ordinals this fleet should span.

    Mirrors internal/fleet/fleet.go:DetectGPUs -- FLUX_FLEET_GPUS pins an explicit
    set, FLUX_FLEET_SIZE caps the count, otherwise nvidia-smi enumerates them.
    Falls back to the profile's declared gpu_count so a dry run on a laptop still
    plans the right number of shards.
    """
    out = {"gpus": [], "source": "", "detected": False}
    raw = os.environ.get("FLUX_FLEET_GPUS", "").strip()
    if raw:
        out["gpus"] = [int(f) for f in re.findall(r"\d+", raw)]
        out["source"] = "FLUX_FLEET_GPUS"
        out["detected"] = True
    else:
        exe = shutil.which("nvidia-smi")
        if exe:
            try:
                proc = subprocess.run([exe, "--query-gpu=index", "--format=csv,noheader"],
                                      capture_output=True, text=True, timeout=10)
                if proc.returncode == 0:
                    out["gpus"] = [int(line.strip()) for line in proc.stdout.splitlines() if line.strip().isdigit()]
                    out["source"] = "nvidia-smi"
                    out["detected"] = bool(out["gpus"])
            except Exception:
                pass
    if not out["gpus"] and profile is not None:
        out["gpus"] = list(range(profile.gpu_count))
        out["source"] = "profile gpu_count (declared, not detected)"
    cap = os.environ.get("FLUX_FLEET_SIZE", "").strip()
    if cap.isdigit() and 0 < int(cap) < len(out["gpus"]):
        out["gpus"] = out["gpus"][: int(cap)]
        out["source"] += " capped by FLUX_FLEET_SIZE"
    return out


def version_at_least(found, floor):
    def parts(text):
        return [int(p) for p in re.findall(r"\d+", str(text))[:4]] or [0]
    a, b = parts(found), parts(floor)
    a += [0] * (len(b) - len(a))
    b += [0] * (len(a) - len(b))
    return a >= b


# ---------------------------------------------------------------------------
# Unix-socket client -- the wire format from internal/daemon/daemon.go
# ---------------------------------------------------------------------------


def socket_alive(sock_path, timeout=0.5):
    sock_path = pathlib.Path(sock_path)
    if not sock_path.exists():
        return False
    try:
        conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        conn.settimeout(timeout)
        conn.connect(str(sock_path))
        conn.close()
        return True
    except OSError:
        return False


def socket_request(sock_path, payload, timeout=10.0):
    """One JSON object out, one JSON line back."""
    conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    conn.settimeout(timeout)
    try:
        conn.connect(str(sock_path))
        conn.sendall((json.dumps(payload) + "\n").encode("utf-8"))
        chunks, buf = [], b""
        while b"\n" not in buf:
            chunk = conn.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
            buf = b"".join(chunks)
        line = buf.split(b"\n", 1)[0]
    finally:
        try:
            conn.close()
        except OSError:
            pass
    if not line:
        raise ValueError("worker closed the connection without a response")
    resp = json.loads(line.decode("utf-8"))
    if not isinstance(resp, dict):
        raise ValueError("worker returned a non-object response")
    return resp


def ledger_jobs(ledger_path):
    """The worker's ledger is a full snapshot rewritten on each flush, one JSON object
    per line, so the last row for an id wins."""
    path = pathlib.Path(ledger_path)
    if not path.exists():
        return {}
    jobs = {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and row.get("id"):
                    jobs[str(row["id"])] = row
    except OSError:
        return {}
    return jobs


def fetch_jobs(paths, timeout=5.0):
    if socket_alive(paths.fluxd_sock, timeout=0.5):
        try:
            resp = socket_request(paths.fluxd_sock, {"op": "jobs"}, timeout=timeout)
            if resp.get("ok") and isinstance(resp.get("jobs"), list):
                return ({str(j.get("id")): j for j in resp["jobs"] if isinstance(j, dict) and j.get("id")},
                        "socket")
        except (OSError, ValueError):
            pass
    return ledger_jobs(paths.jobs_ledger), "ledger"


def queue_depth(paths):
    """Active jobs, for the same ``depth < 3`` backpressure perpetual_feeder.py uses."""
    jobs, _source = fetch_jobs(paths)
    return sum(1 for j in jobs.values() if j.get("status") in ("queued", "running"))


# ---------------------------------------------------------------------------
# Traversal arithmetic
#
# Mirrors of worker.py:_atlas_shard_slice and worker.py:_atlas_shard_block, kept
# byte-faithful so --dry-run can report the true per-shard cell count offline without
# importing worker.py (which pulls torch and diffusers at module scope).
# ---------------------------------------------------------------------------


def atlas_shard_slice(order, shard_id, shard_total, block=1):
    if shard_total <= 1:
        return list(order)
    block = max(1, int(block or 1))
    order = list(order)
    out = []
    for start in range(0, len(order), block):
        if (start // block) % shard_total == shard_id:
            out.extend(order[start:start + block])
    return out


def atlas_shard_block(run_total, shard_total, requested):
    if shard_total <= 1:
        return 1
    block = max(1, int(requested or 1))
    return max(1, min(block, run_total // shard_total or 1))


# ---------------------------------------------------------------------------
# Drafts
# ---------------------------------------------------------------------------


def list_drafts(paths):
    out = []
    for path in sorted(glob.glob(str(paths.drafts_dir / "arcane_*.json"))):
        try:
            data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            out.append((pathlib.Path(path), data))
    return out


def draft_alias(stem):
    for alias, name in DRAFT_ALIASES.items():
        if name == stem:
            return alias
    return stem.replace("arcane_", "")


def resolve_draft(paths, spec):
    """Accept a path, a filename, a draft stem, or a short alias."""
    if not spec:
        raise SystemExit("a draft is required: pass --draft <name|path> (see `arcane_pipeline.py drafts`)")
    candidate = pathlib.Path(str(spec)).expanduser()
    if candidate.is_file():
        return candidate, _read_draft(candidate)

    stem = str(spec).strip()
    if stem.endswith(".json"):
        stem = stem[:-5]
    names = [stem]
    if stem in DRAFT_ALIASES:
        names.insert(0, DRAFT_ALIASES[stem])
    if not stem.startswith("arcane_"):
        names.append("arcane_" + stem)
        names.append("arcane_italian_princess_" + stem)
    for name in names:
        path = paths.drafts_dir / ("%s.json" % name)
        if path.is_file():
            return path, _read_draft(path)
    raise SystemExit("unknown draft %r; try one of: %s" % (spec, ", ".join(sorted(DRAFT_ALIASES))))


def _read_draft(path):
    try:
        data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise SystemExit("cannot read draft %s: %s" % (path, _short(exc)))
    except ValueError as exc:
        raise SystemExit("draft %s is not valid JSON: %s" % (path, _short(exc)))
    if not isinstance(data, dict):
        raise SystemExit("draft %s must be a JSON object" % path)
    return data


def validate_draft(draft):
    """Return a list of problems. Empty means the draft is submittable."""
    problems = []
    if str(draft.get("kind") or "") != "latent_sphere_map":
        problems.append("kind is %r, expected 'latent_sphere_map'" % draft.get("kind"))
    if not str(draft.get("id") or "").strip():
        problems.append("missing id")
    if not str(draft.get("prompt") or "").strip():
        problems.append("missing prompt")
    rows = clamp_int(draft.get("n_rows"), 0, 0, 10 ** 9)
    cols = clamp_int(draft.get("n_cols"), 0, 0, 10 ** 9)
    if rows < 1 or cols < 1:
        problems.append("n_rows/n_cols must both be >= 1 (got %s x %s)" % (draft.get("n_rows"), draft.get("n_cols")))
    latent = clamp_int(draft.get("n_latent"), 0, 0, 10 ** 12)
    if latent and rows and cols and latent > rows * cols:
        problems.append("n_latent %d exceeds the %d-cell grid" % (latent, rows * cols))
    size = clamp_int(draft.get("size"), 0, 0, 8192)
    if size and not (128 <= size <= 2048):
        problems.append("size %d is outside the worker's 128..2048 range" % size)
    steps = clamp_int(draft.get("steps"), 0, 0, 10 ** 4)
    if steps and not (1 <= steps <= 120):
        problems.append("steps %d is outside the worker's 1..120 range" % steps)
    return problems


def draft_mode(path, draft):
    """The mode a draft belongs to. An explicit `arcane_mode` key wins."""
    declared = str(draft.get("arcane_mode") or draft.get("mode_family") or "").strip().lower()
    if declared in MODES:
        return declared, "declared in the draft"
    alias = draft_alias(pathlib.Path(path).stem)
    for name, spec in MODES.items():
        if alias in spec["drafts"]:
            return name, "draft roster for %s" % name
    return MODE_LATENT, "default (unclassified)"


def mode_draft_problems(mode, path, draft):
    """Per-mode draft rules. Returns [(severity, message)].

    The same `view_prompts` list is a defect in `character` and a feature in
    `scenes`, because worker.py:1645 clears the cross-frame residual cache every
    time the prompt text changes, and only `character` depends on that cache
    surviving the whole orbit.
    """
    out = []
    spec = MODES[mode]
    views = [str(v).strip() for v in (draft.get("view_prompts") or []) if str(v).strip()]
    alias = draft_alias(pathlib.Path(path).stem)
    policy = spec["view_prompts"]

    if views and policy == "refuse":
        out.append((FAIL,
                    "%d view_prompts steer rotation with TEXT. worker.py:1645 clears the "
                    "atlas-xframe-cache on every prompt change, so this draft flushes the residual "
                    "cache %d times per orbit -- and in %s mode that cache IS the identity "
                    "mechanism. Drive rotation from SO(4) geometry (rates / offsets / "
                    "shell_coupling / seed_lock) instead, or pass --allow-view-prompts to override."
                    % (len(views), len(views), mode)))
    elif views and policy == "allow":
        out.append((WARN, "%d view_prompts flush the residual cache %d times; harmless for %s "
                          "(the cache is pure speedup here) but it costs throughput"
                    % (len(views), len(views), mode)))
    elif views and policy == "expected":
        out.append((OK, "%d view_prompts -- expected in %s; prompt variation is the point" % (len(views), mode)))

    if mode == MODE_CHARACTER:
        lock = clamp_float(draft.get("seed_lock"), 0.0, 0.0, 1.0)
        if lock < 0.30:
            out.append((WARN, "seed_lock=%.2f is loose for an identity orbit; the home latent drifts "
                              "and the character changes across the rotation" % lock))
        if not (draft.get("rates") or draft.get("offsets")):
            out.append((WARN, "no rates/offsets: rotation would come from the traversal alone, "
                              "with nothing shaping the SO(4) path"))
        if alias not in MODES[MODE_CHARACTER]["drafts"]:
            out.append((WARN, "%s is not on the character roster (%s)"
                        % (alias, ", ".join(MODES[MODE_CHARACTER]["drafts"]))))
    if mode == MODE_LATENT and clamp_float(draft.get("seed_lock"), 0.0, 0.0, 1.0) > 0.80:
        out.append((WARN, "seed_lock is very tight for an exploration run; the manifold barely moves"))
    return out


def orbit_coherence(cell_paths, gates_module=None):
    """Identity coherence across a rotation orbit.

    Per-frame Fortiche conformance is necessary but not sufficient for `character`:
    an orbit of eight individually-beautiful frames of eight different women is a
    failure. This reports (a) same-character similarity between adjacent and distant
    frames and (b) whether angular progression is smooth and monotonic.

    Embeddings come from sensory_gates' public API -- this module does not own
    sensory_gates.py or arcane_aesthetic.py and does not touch them. When no
    embedding backend is resident the result says so and carries no numbers;
    it never estimates identity from nothing.
    """
    paths_list = [str(c) for c in cell_paths]
    result = {
        "frames": len(paths_list),
        "available": False,
        "reason": "",
        "backend": "",
        "adjacent_similarity": None,
        "distant_similarity": None,
        "identity_drift": None,
        "progression": None,
        "monotonic": None,
        "jumps": [],
    }
    if len(paths_list) < 3:
        result["reason"] = "an orbit needs at least 3 frames to have a shape"
        return result

    module = gates_module if gates_module is not None else load_module("sensory_gates")
    if module is None:
        result["reason"] = "sensory_gates not importable; no embedding backend"
        return result
    embed = None
    for name in ("embed", "embedding", "embed_image", "features"):
        fn = getattr(module, name, None)
        if callable(fn):
            embed = fn
            result["backend"] = "sensory_gates.%s" % name
            break
    if embed is None:
        result["reason"] = "sensory_gates exposes no embed()/embedding()/embed_image()/features()"
        return result

    vectors = []
    for path in paths_list:
        try:
            vector = embed(path)
        except Exception as exc:
            result["reason"] = "embedding failed on %s: %s" % (os.path.basename(path), _short(exc))
            return result
        vector = _as_vector(vector)
        if vector is None:
            result["reason"] = "sensory_gates returned a non-vector embedding"
            return result
        vectors.append(vector)

    adjacent = [_cosine(vectors[i], vectors[i + 1]) for i in range(len(vectors) - 1)]
    half = len(vectors) // 2
    distant = [_cosine(vectors[i], vectors[(i + half) % len(vectors)]) for i in range(len(vectors))]
    adjacent = [a for a in adjacent if a is not None]
    distant = [d for d in distant if d is not None]
    if not adjacent:
        result["reason"] = "embeddings had no comparable magnitude"
        return result

    mean_adjacent = sum(adjacent) / len(adjacent)
    mean_distant = (sum(distant) / len(distant)) if distant else None
    # A jump is an adjacent step markedly less similar than the orbit's own norm:
    # the frame where identity or pose discontinuously changed.
    spread = max(adjacent) - min(adjacent)
    threshold = mean_adjacent - max(0.05, spread * 0.5)
    jumps = [{"between": [i, i + 1], "similarity": round(adjacent[i], 4)}
             for i in range(len(adjacent)) if adjacent[i] < threshold]

    result.update({
        "available": True,
        "adjacent_similarity": round(mean_adjacent, 4),
        "adjacent_min": round(min(adjacent), 4),
        "distant_similarity": round(mean_distant, 4) if mean_distant is not None else None,
        # How much identity is lost going half-way round. Small is good.
        "identity_drift": round(mean_adjacent - mean_distant, 4) if mean_distant is not None else None,
        "progression": [round(a, 4) for a in adjacent],
        "monotonic": not jumps,
        "jumps": jumps,
    })
    return result


def _as_vector(value):
    if value is None:
        return None
    tolist = getattr(value, "tolist", None)
    if callable(tolist):
        try:
            value = tolist()
        except Exception:
            return None
    if isinstance(value, dict):
        for key in ("embedding", "vector", "features"):
            if key in value:
                return _as_vector(value[key])
        return None
    if isinstance(value, (list, tuple)):
        flat = []
        for item in value:
            if isinstance(item, (list, tuple)):
                flat.extend(item)
            else:
                flat.append(item)
        try:
            return [float(x) for x in flat]
        except (TypeError, ValueError):
            return None
    return None


def _cosine(a, b):
    if not a or not b or len(a) != len(b):
        return None
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0.0 or nb == 0.0:
        return None
    return dot / (na * nb)


def draft_row(path, draft):
    rows = clamp_int(draft.get("n_rows"), 0, 0, 10 ** 9)
    cols = clamp_int(draft.get("n_cols"), 0, 0, 10 ** 9)
    return {
        "alias": draft_alias(path.stem),
        "stem": path.stem,
        "path": str(path),
        "label": str(draft.get("label") or path.stem),
        "cells": clamp_int(draft.get("n_latent"), rows * cols, 0, 10 ** 12),
        "grid": "%dx%d" % (rows, cols),
        "size": clamp_int(draft.get("size"), 0, 0, 8192),
        "steps": clamp_int(draft.get("steps"), 0, 0, 10 ** 4),
        "guidance": clamp_float(draft.get("guidance"), 0.0, 0.0, 100.0),
        "mode": str(draft.get("mode") or "omega"),
        "traversal": str(draft.get("traversal") or "spherical_outward"),
        "seed_lock": clamp_float(draft.get("seed_lock"), 0.0, 0.0, 1.0),
        "coupling": clamp_float(draft.get("shell_coupling"), 1.0, -16.0, 16.0),
        "views": len(draft.get("view_prompts") or []),
        "prompt": str(draft.get("prompt") or ""),
        "problems": validate_draft(draft),
    }


# ---------------------------------------------------------------------------
# Toggle resolution
# ---------------------------------------------------------------------------


def resolve_options(args, paths, profile):
    """CLI flag > environment > continuum > profile default.

    The flags were already pushed into pipeline_paths' own environment contract by
    _env_overrides() before any of this ran, so agent 5's resolver and this function
    read the same knobs and cannot disagree about what was requested.

    Kontext is the ONLY toggle. flux, witness, governor, pixtral and the
    DINOv2/SigLIP gates are mandatory in every profile and have no flags.
    """
    toggles = (paths.continuum or {}).get("toggles") or {}

    kontext = getattr(args, "kontext", None)
    if kontext is None:
        kontext = _env_flag("ARCANE_KONTEXT")
    if kontext is None and "kontext" in toggles:
        kontext = bool(toggles["kontext"])
    if kontext is None:
        kontext = bool((profile.tenant("kontext") or {}).get("enabled", False))

    governor_tenant = profile.tenant("governor") or {}
    governor_remote = getattr(args, "governor_remote", None)
    if governor_remote is None:
        governor_remote = _env_flag("ARCANE_GOVERNOR_REMOTE")
    if governor_remote is None and "governor_remote" in toggles:
        governor_remote = bool(toggles["governor_remote"])
    if governor_remote is None and paths.governor_remote is not None:
        governor_remote = bool(paths.governor_remote)
    if governor_remote is None:
        governor_remote = bool(governor_tenant.get("remote", False))

    governor_url = getattr(args, "governor_url", None) or \
        (paths.continuum or {}).get("governor_base_url") or paths.governor_base_url
    if not governor_url:
        governor_url = (governor_tenant.get("remote_base_url") or GOVERNOR_REMOTE_URL) if governor_remote \
            else (_dig(paths.raw_continuum, "governor", "local_base_url") or GOVERNOR_LOCAL_URL)

    flux_tenant = profile.tenant("flux") or {}
    flux_precision = (getattr(args, "flux_precision", None)
                      or os.environ.get("ARCANE_FLUX_PRECISION", "")
                      or str(flux_tenant.get("precision") or "bf16")).strip().lower()
    available = flux_tenant.get("variants_available") or []
    if available and flux_precision not in available:
        raise SystemExit("--flux-precision %r is not a variant of this profile's flux tenant (%s)"
                         % (flux_precision, ", ".join(sorted(available))))

    kontext_tenant = profile.tenant("kontext") or {}
    kontext_precision = (getattr(args, "kontext_precision", None)
                         or os.environ.get("ARCANE_KONTEXT_PRECISION", "")
                         or str(kontext_tenant.get("precision") or "q4_k_s")).strip().lower()

    crown = getattr(args, "crown_threshold", None)
    if crown is None:
        crown = (paths.continuum or {}).get("masterpiece_threshold")
    if crown is None:
        crown = _dig(paths.raw_continuum, "verdict", "masterpiece_threshold")
    crown = clamp_float(crown, DEFAULT_CROWN_THRESHOLD, 0.0, 100.0)

    mode = getattr(args, "mode", None) or MODE_LATENT
    spec = MODES[mode]
    # A profile that declares its own default layout outranks the mode's suggestion;
    # the mode only picks one when the profile is silent.
    default_layout = str(profile.fact("default_layout", "") or "") or (
        spec["layout"] if getattr(args, "mode", None) else "balanced")

    return {
        "profile": profile.name,
        "mode": mode,
        "layout": (getattr(args, "layout", None) or os.environ.get("ARCANE_LAYOUT", "")
                   or default_layout).strip().lower(),
        "select": (getattr(args, "select", None) or spec.get("select") or "").strip().lower(),
        "character_ref": getattr(args, "character", None) or "none",
        "kontext": bool(kontext),
        "kontext_precision": kontext_precision,
        "kontext_steps": clamp_int(getattr(args, "kontext_steps", None), 28, 1, 120),
        "kontext_guidance": clamp_float(getattr(args, "kontext_guidance", None), 2.5, 0.0, 20.0),
        "governor_remote": bool(governor_remote),
        "governor_url": governor_url,
        "flux_precision": flux_precision,
        "jury": not getattr(args, "no_jury", False),
        "require_jury": bool(getattr(args, "require_jury", False)),
        "crown_threshold": crown,
        "drift_threshold": clamp_float(getattr(args, "drift_threshold", None), DEFAULT_DRIFT_THRESHOLD, 0.0, 100.0),
        "novelty_polarity": spec["novelty_polarity"],
        "cache_role": spec["cache_role"],
    }


# ---------------------------------------------------------------------------
# Payload construction -- the exact shape worker.py:submit_atlas consumes
# ---------------------------------------------------------------------------


def build_payload(paths, draft, draft_path, args, opts):
    draft = json.loads(json.dumps(draft))  # never mutate the file's dict in place

    prompt_override = (getattr(args, "prompt", None) or "").strip()
    if prompt_override:
        # Mirrors cmd/flux/main.go:atlasSphere -- an overridden prompt replaces the
        # per-view bucket list too, or the buckets would silently win.
        draft["prompt"] = prompt_override
        draft["view_prompts"] = [prompt_override]
    if getattr(args, "seed", None):
        draft["seed_a"] = args.seed

    n_rows = clamp_int(draft.get("n_rows"), 16, 1, 1_000_000)
    n_cols = clamp_int(draft.get("n_cols"), 4096, 1, 1_000_000)
    grid_total = n_rows * n_cols
    n_latent = clamp_int(draft.get("n_latent"), grid_total, 1, grid_total)
    if getattr(args, "full_grid", False):
        n_latent = grid_total
        draft["n_latent"] = n_latent

    index_start = clamp_int(getattr(args, "index_start", 0), 0, 0, n_latent)
    index_end = clamp_int(getattr(args, "index_end", 0) or n_latent, n_latent, index_start, n_latent)
    cells = clamp_int(getattr(args, "cells", 0) or 0, 0, 0, n_latent)
    if cells > 0:
        index_end = min(index_end, index_start + cells)

    shard_id, shard_total = parse_shard(getattr(args, "shard", None))

    adapter = (getattr(args, "adapter", None) or DEFAULT_ADAPTER).strip().lower()
    batch_size = clamp_int(getattr(args, "batch_size", 1), 1, 1, 64)
    if adapter in ("atlas-xframe-cache", "xframe-cache") and batch_size > 1:
        # worker.py:_render_atlas raises on this combination; refuse before the wire.
        raise SystemExit("adapter %s renders one cell at a time (cross-frame residuals are sequential); "
                         "--batch-size must be 1, or pass --adapter none" % adapter)

    size = clamp_int(getattr(args, "size", 0) or draft.get("size") or 384, 384, 128, 2048)
    steps = clamp_int(getattr(args, "steps", 0) or draft.get("steps") or 40, 40, 1, 120)
    guidance = clamp_float(getattr(args, "guidance", 0) or draft.get("guidance") or 3.5, 3.5, 0.0, 20.0)
    sample_count = clamp_int(getattr(args, "sample_count", 0) or draft.get("render_count") or 0, 0, 0, n_latent)

    run_total = max(0, index_end - index_start)
    if sample_count > 0:
        run_total = min(run_total, sample_count)
    shard_block = atlas_shard_block(run_total, shard_total, clamp_int(getattr(args, "shard_block", 32), 32, 1, 4096))
    shard_cells = len(atlas_shard_slice(range(run_total), shard_id, shard_total, shard_block))

    job_id = (getattr(args, "id", None) or "").strip() or str(draft.get("id") or "").strip()
    if getattr(args, "fresh", False):
        job_id = "%s_%s" % (job_id, time.strftime("%Y%m%d-%H%M%S"))
    draft["id"] = job_id
    draft["instance_id"] = draft.get("instance_id") or job_id

    cache_threshold = getattr(args, "cache_threshold", None)
    if cache_threshold is None:
        cache_threshold = draft.get("cache_threshold", DEFAULT_CACHE_THRESHOLD)

    payload = {
        "op": "atlas_sphere",
        "id": job_id,
        "draft": draft,
        "prompt": str(draft.get("prompt") or ""),
        "backend": (getattr(args, "backend", None) or "cuda").strip().lower(),
        "size": size,
        "steps": steps,
        "guidance": guidance,
        "seed": str(getattr(args, "seed", None) or draft.get("seed_a") or 7),
        "n_latent": n_latent,
        "index_start": index_start,
        "index_end": index_end,
        "limit": cells,
        "render_count": sample_count,
        "batch_size": batch_size,
        "traversal_order": (getattr(args, "order", None) or draft.get("traversal_order") or "column_serpentine"),
        "sample_mode": (getattr(args, "sample_mode", None) or draft.get("sample_mode") or "contiguous").lower(),
        "study_type": str(draft.get("study_type") or "arcane_cartography"),
        "adapter": adapter,
        "cache_threshold": clamp_float(cache_threshold, DEFAULT_CACHE_THRESHOLD, 0.0, 1.0),
        "cache_downsample": clamp_int(getattr(args, "cache_downsample", None) or draft.get("cache_downsample")
                                      or DEFAULT_CACHE_DOWNSAMPLE, DEFAULT_CACHE_DOWNSAMPLE, 1, 64),
        "cache_warmup": clamp_int(getattr(args, "cache_warmup", None) or draft.get("cache_warmup")
                                  or DEFAULT_CACHE_WARMUP, DEFAULT_CACHE_WARMUP, 0, steps),
        "shard_id": shard_id,
        "shard_total": shard_total,
        "shard_block": shard_block,
    }

    plan = {
        "draft_path": str(draft_path),
        "job_id": job_id,
        "profile": opts["profile"],
        "grid_total": grid_total,
        "n_latent": n_latent,
        "run_total": run_total,
        "shard_cells": shard_cells,
        "sphere_dir": str(paths.sphere_dir(job_id)),
        "worker_manifest": str(paths.sphere_dir(job_id) / "manifest.json"),
        "worker_progress": str(paths.sphere_dir(job_id) /
                               ("progress.shard%d.json" % shard_id if shard_total > 1 else "progress.json")),
        "checkpoint": str(paths.state_path(job_id)),
        "live_run_manifest": str(paths.runs_dir / ("%s.run.json" % job_id)),
        "flux_precision_requested": opts["flux_precision"],
        # worker.py:submit_atlas stamps precision="bf16" on the job record. A q4_k_s
        # request is budgeted and recorded but the resident atlas engine will not honour it.
        "precision_enforced_by_engine": "bf16",
        "kontext": opts["kontext"],
        "jury": opts["jury"],
    }
    return payload, plan


def parse_shard(spec):
    if not spec:
        return 0, 1
    match = re.match(r"^(\d+)\s*[/:]\s*(\d+)$", str(spec).strip())
    if not match:
        raise SystemExit("--shard expects i/n, for example 0/4 (got %r)" % spec)
    shard_id, shard_total = int(match.group(1)), max(1, min(64, int(match.group(2))))
    if shard_id >= shard_total:
        raise SystemExit("--shard id %d is out of range for %d shards" % (shard_id, shard_total))
    return shard_id, shard_total


# ---------------------------------------------------------------------------
# Submission
# ---------------------------------------------------------------------------


def cli_can_express(payload):
    """The flux binary's `atlas sphere` flags cover most of the payload but not all."""
    if payload["shard_total"] > 1:
        return False, "shard_id/shard_total have no CLI flag"
    if payload["batch_size"] > 1:
        return False, "batch_size has no CLI flag"
    if payload["sample_mode"] != "contiguous":
        return False, "sample_mode has no CLI flag"
    return True, ""


# internal/fleet/fleet.go:DefaultShardBlock -- how many consecutive cells a
# worker takes before the next shard's block. Locality inside a block is what
# keeps the cross-frame residual cache warm.
DEFAULT_SHARD_BLOCK = 32


def resolve_fleet(args, profile):
    """Which GPU ordinals this run fans out across.

    --gpus wins, then --shards (first N detected), then the detected fleet, then
    the profile's declared gpu_count. One GPU means the ordinary single-worker path.
    """
    explicit = (getattr(args, "gpus", "") or "").strip()
    if explicit:
        gpus = [int(f) for f in re.findall(r"\d+", explicit)]
        return gpus, "--gpus"
    detected = detect_fleet_gpus(profile)
    gpus = list(detected["gpus"])
    shards = int(getattr(args, "shards", 0) or 0)
    if shards > 0:
        if shards <= len(gpus):
            return gpus[:shards], "--shards over %s" % detected["source"]
        # More shards than GPUs is legitimate on one card but wastes the residual
        # cache, so say so rather than silently over-subscribing.
        return list(range(shards)), "--shards (%d requested, only %d GPU(s) %s)" % (
            shards, len(gpus), detected["source"] or "known")
    return gpus, detected["source"]


def fleet_socket(paths, gpu):
    return paths.home / ".fluxd" / ("flux-gpu%d.sock" % gpu)


def submit_fleet(paths, payload, gpus, shard_block=None, timeout=30.0):
    """Fan one atlas across per-GPU workers as disjoint shards.

    This is the Python mirror of internal/fleet/fleet.go:Pool.SubmitAtlas -- the
    SAME job id goes to every worker with a distinct (shard_id, shard_total), each
    renders an interleaved slice of the traversal via worker.py:_atlas_shard_slice,
    and all of them write into one output directory. Cells are addressed by index
    and skipped when already present, so shards never collide and a re-submit
    resumes rather than duplicating work.
    """
    total = len(gpus)
    block = int(shard_block or os.environ.get("FLUX_SHARD_BLOCK") or DEFAULT_SHARD_BLOCK)
    results = []
    for index, gpu in enumerate(gpus):
        request = dict(payload)
        request["op"] = "atlas_sphere"
        request["shard_id"] = index
        request["shard_total"] = total
        request["shard_block"] = block
        sock = fleet_socket(paths, gpu)
        entry = {"gpu": gpu, "shard_id": index, "shard_total": total, "shard_block": block,
                 "socket": str(sock), "job": None, "error": ""}
        if not socket_alive(sock, timeout=1.0):
            entry["error"] = "no worker on %s" % sock
            results.append(entry)
            continue
        try:
            resp = socket_request(sock, request, timeout=timeout)
        except (OSError, ValueError) as exc:
            entry["error"] = _short(exc)
            results.append(entry)
            continue
        if not resp.get("ok"):
            entry["error"] = resp.get("error", "worker rejected the shard")
        else:
            job = resp.get("job") or {}
            job["worker"] = "flux-gpu%d" % gpu
            job["gpu"] = gpu
            job["already_running"] = bool(resp.get("already"))
            entry["job"] = job
        results.append(entry)
    return results


def fleet_jobs(paths, job_id, gpus):
    """Per-shard job records, read from each worker's own socket or ledger."""
    out = []
    for gpu in gpus:
        sock = fleet_socket(paths, gpu)
        record = None
        if socket_alive(sock, timeout=0.5):
            try:
                resp = socket_request(sock, {"op": "jobs"}, timeout=5.0)
                for row in resp.get("jobs") or []:
                    if isinstance(row, dict) and str(row.get("id")) == job_id:
                        record = row
                        break
            except (OSError, ValueError):
                record = None
        if record is None:
            ledger = paths.home / ".fluxd" / ("flux-gpu%d.jobs.jsonl" % gpu)
            record = ledger_jobs(ledger).get(job_id)
        out.append({"gpu": gpu, "job": record})
    return out


def submit(paths, payload, transport="auto", timeout=30.0):
    """Submit the atlas job. Returns (job_record, transport_used)."""
    expressible, why = cli_can_express(payload)
    have_bin = paths.flux_bin is not None and pathlib.Path(paths.flux_bin).exists()

    chosen = transport
    if transport == "auto":
        chosen = "cli" if (have_bin and expressible) else "socket"
    if chosen == "cli" and not have_bin:
        raise SystemExit("--transport cli needs the flux binary; none found (set FLUX_BIN)")
    if chosen == "cli" and not expressible:
        raise SystemExit("--transport cli cannot express this request: %s; use --transport socket" % why)
    if chosen == "cli":
        return _submit_via_cli(paths, payload, timeout), "cli"
    return _submit_via_socket(paths, payload, timeout), "socket"


def _submit_via_cli(paths, payload, timeout):
    draft_dir = paths.home / ".fluxd" / "arcane"
    draft_dir.mkdir(parents=True, exist_ok=True)
    tmp = draft_dir / ("submit_%s.json" % payload["id"])
    tmp.write_text(json.dumps(payload["draft"], indent=2, sort_keys=True) + "\n", encoding="utf-8")
    cmd = [
        str(paths.flux_bin), "atlas", "sphere",
        "--draft", str(tmp),
        "--id", payload["id"],
        "--backend", payload["backend"],
        "--order", payload["traversal_order"],
        "--adapter", payload["adapter"],
        "--cache-threshold", "%g" % payload["cache_threshold"],
        "--cache-downsample", str(payload["cache_downsample"]),
        "--cache-warmup", str(payload["cache_warmup"]),
        "--index-start", str(payload["index_start"]),
        "--steps", str(payload["steps"]),
        "--size", str(payload["size"]),
        "--guidance", "%g" % payload["guidance"],
    ]
    if payload["limit"]:
        cmd += ["--limit", str(payload["limit"])]
    if payload["index_end"]:
        cmd += ["--index-end", str(payload["index_end"])]
    if payload["render_count"]:
        cmd += ["--sample-count", str(payload["render_count"])]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise SystemExit("flux atlas sphere failed (%d): %s" % (proc.returncode, (proc.stderr or proc.stdout).strip()))
    sys.stdout.write(proc.stdout)
    jobs, _src = fetch_jobs(paths)
    return jobs.get(payload["id"], {"id": payload["id"], "status": "queued", "submitted_via": "cli"})


def _submit_via_socket(paths, payload, timeout):
    if not socket_alive(paths.fluxd_sock, timeout=1.0):
        raise SystemExit("fluxd socket %s is not accepting connections; start the resident worker "
                         "(`flux warm`) or pass --dry-run" % paths.fluxd_sock)
    resp = socket_request(paths.fluxd_sock, payload, timeout=timeout)
    if not resp.get("ok"):
        raise SystemExit("worker rejected the atlas submission: %s" % resp.get("error", "unknown error"))
    job = resp.get("job") or {}
    job["already_running"] = bool(resp.get("already"))
    return job


# ---------------------------------------------------------------------------
# Jury, gates, aesthetic
# ---------------------------------------------------------------------------


def _call_flexible(fn, image_path, context):
    """Call an agent-owned entry point without knowing its exact signature.

    TypeError means the shape did not match and the next one is tried; any other
    exception is a genuine failure and is reported, never folded into a score.
    """
    attempts = (
        (lambda: fn(str(image_path), **context)),
        (lambda: fn(str(image_path), context)),
        (lambda: fn(str(image_path))),
    )
    last = None
    for attempt in attempts:
        try:
            return attempt(), None
        except TypeError as exc:
            last = exc
            continue
        except Exception as exc:
            return None, _short(exc)
    return None, "no compatible signature (%s)" % (_short(last) if last else "unknown")


def jury_evaluate(image_path, context, opts):
    """Run one cell past the jury. Returns a verdict dict; never invents a score."""
    if not opts["jury"]:
        return {"tier": TIER_UNSCORED, "score": None, "reason": "jury disabled (--no-jury)"}
    module = load_module("moj_evaluator")
    if module is None:
        return {"tier": TIER_UNSCORED, "score": None, "reason": "moj_evaluator not importable"}
    fn = getattr(module, "evaluate", None)
    if not callable(fn):
        return {"tier": TIER_UNSCORED, "score": None, "reason": "moj_evaluator.evaluate() missing"}
    raw, error = _call_flexible(fn, image_path, context)
    if error is not None:
        return {"tier": TIER_UNSCORED, "score": None, "reason": "jury call failed: %s" % error}
    return normalize_verdict(raw, opts)


def normalize_verdict(raw, opts):
    """Coerce whatever the jury returned into {score(0-100), tier, epigram, raw}."""
    if raw is None:
        return {"tier": TIER_UNSCORED, "score": None, "reason": "jury returned nothing"}

    score, scale, epigram, detail = None, None, "", {}
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        score = float(raw)
    elif isinstance(raw, dict):
        detail = raw
        scale = raw.get("scale")
        for key in ("score", "overall", "total", "percentile", "verdict_score", "aggregate"):
            if isinstance(raw.get(key), (int, float)) and not isinstance(raw.get(key), bool):
                score = float(raw[key])
                break
        for key in ("epigram", "poetic_epigram", "caption", "note"):
            if isinstance(raw.get(key), str) and raw[key].strip():
                epigram = raw[key].strip()
                break
        if score is None and isinstance(raw.get("tier"), str):
            return {"tier": raw["tier"].strip().lower() or TIER_UNSCORED, "score": None, "epigram": epigram,
                    "raw": detail, "reason": "jury returned a tier without a numeric score"}
    else:
        return {"tier": TIER_UNSCORED, "score": None, "reason": "jury returned %s" % type(raw).__name__}

    if score is None:
        return {"tier": TIER_UNSCORED, "score": None, "raw": detail, "reason": "no numeric score in jury response"}

    # Protocol spec section 5 scores 0-10 (crown at 9.0, drift below 7.0);
    # jury_continuum.toml [verdict] scores 0-100 (masterpiece at 90.0). Normalise to
    # 0-100 and record which reading was applied rather than guessing silently.
    if scale in (10, 10.0, "10"):
        score, applied = score * 10.0, "0-10 declared"
    elif scale in (100, 100.0, "100"):
        applied = "0-100 declared"
    elif 0.0 <= score <= 10.0:
        score, applied = score * 10.0, "0-10 inferred"
    else:
        applied = "0-100 inferred"

    if score >= opts["crown_threshold"]:
        tier = TIER_CROWNED
    elif score >= opts["drift_threshold"]:
        tier = TIER_KEPT
    else:
        tier = TIER_DRIFT
    return {"tier": tier, "score": round(score, 3), "scale": applied, "epigram": epigram, "raw": detail}


def gates_evaluate(image_path, context):
    """DINOv2 + SigLIP micro-sensory gates. Absence is reported, never simulated."""
    module = load_module("sensory_gates")
    if module is None:
        return {"available": False, "reason": "sensory_gates not importable"}
    for name in ("evaluate", "gate", "run"):
        fn = getattr(module, name, None)
        if callable(fn):
            raw, error = _call_flexible(fn, image_path, context)
            if error is not None:
                return {"available": False, "reason": "sensory_gates.%s failed: %s" % (name, error)}
            return {"available": True, "entry": name, "result": raw, "pass": _gate_pass(raw)}
    return {"available": False, "reason": "sensory_gates exposes no evaluate()/gate()/run()"}


def _gate_pass(raw):
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, dict):
        for key in ("pass", "passed", "ok", "accept"):
            if isinstance(raw.get(key), bool):
                return raw[key]
    return True


def aesthetic_evaluate(image_path, context):
    """Fortiche invariants (protocol spec section 3), when agent 3's module is present."""
    module = load_module("arcane_aesthetic")
    if module is None:
        return {"available": False, "reason": "arcane_aesthetic not importable"}
    for name in ("evaluate", "score", "measure"):
        fn = getattr(module, name, None)
        if callable(fn):
            raw, error = _call_flexible(fn, image_path, context)
            if error is not None:
                return {"available": False, "reason": "arcane_aesthetic.%s failed: %s" % (name, error)}
            return {"available": True, "entry": name, "result": raw}
    return {"available": False, "reason": "arcane_aesthetic exposes no evaluate()/score()/measure()"}


# ---------------------------------------------------------------------------
# Governor
# ---------------------------------------------------------------------------


def governor_model(paths, opts):
    profile = get_profile(paths, opts["profile"])
    return str((profile.tenant("governor") or {}).get("model") or "")


def governor_chat(paths, opts, system, user, temperature=0.95, max_tokens=180, timeout=8.0):
    """Ask the governor for text. Returns ("", reason) when unreachable -- never raises."""
    base = (opts.get("governor_url") or "").rstrip("/")
    if not base:
        return "", "no governor base url"
    body = {
        "model": governor_model(paths, opts) or "governor",
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": float(temperature),
        "max_tokens": int(max_tokens),
    }
    headers = {"Content-Type": "application/json"}
    token = _first_env("GOVERNOR_API_KEY", "OPENAI_API_KEY")
    if token:
        headers["Authorization"] = "Bearer %s" % token
    request = urllib.request.Request(base + "/chat/completions",
                                     data=json.dumps(body).encode("utf-8"), headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
        text = data["choices"][0]["message"]["content"].strip()
        return text.replace('"', "").replace("**", "").strip(), None
    except Exception as exc:
        return "", _short(exc)


def governor_reachable(opts, timeout=3.0):
    base = (opts.get("governor_url") or "").rstrip("/")
    if not base:
        return False, "no governor base url"
    try:
        request = urllib.request.Request(base + "/models")
        token = _first_env("GOVERNOR_API_KEY", "OPENAI_API_KEY")
        if token:
            request.add_header("Authorization", "Bearer %s" % token)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return 200 <= response.getcode() < 300, "HTTP %s" % response.getcode()
    except urllib.error.HTTPError as exc:
        # An auth-gated endpoint that answers at all is still reachable.
        return exc.code in (401, 403), "HTTP %s" % exc.code
    except Exception as exc:
        return False, _short(exc)


# ---------------------------------------------------------------------------
# Checkpoint state
# ---------------------------------------------------------------------------

STATE_VERSION = 3


def new_state(payload, plan, opts, draft_path):
    now = time.time()
    return {
        "version": STATE_VERSION,
        "job_id": payload["id"],
        "created": now,
        "updated": now,
        "status": "running",
        "profile": opts["profile"],
        "draft_path": str(draft_path),
        "draft_label": str(payload["draft"].get("label") or ""),
        "payload": payload,
        "plan": plan,
        "options": opts,
        "mode": opts.get("mode"),
        "fleet": {"gpus": [], "shard_total": payload.get("shard_total", 1),
                  "shard_block": payload.get("shard_block", 1), "source": ""},
        "shards": {},
        "cells": {},
        "kontext_jobs": {},
        "counters": {"settled": 0, "scored": 0, "unscored": 0, "kontext": 0},
        "tiers": {tier: 0 for tier in TIER_ORDER},
        "metrics": {},
    }


def load_state(paths, job_id):
    for candidate in (paths.sphere_dir(job_id) / "arcane_pipeline.state.json", paths.fallback_state_path(job_id)):
        if candidate.exists():
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict) and data.get("job_id"):
                data["_path"] = str(candidate)
                return data
    return None


def save_state(paths, state):
    """Checkpoint beside the cells, falling back into the repo when OUT_DIR is unwritable."""
    state["updated"] = time.time()
    body = json.dumps({k: v for k, v in state.items() if k != "_path"}, indent=2, sort_keys=True, default=str) + "\n"
    for target in (paths.sphere_dir(state["job_id"]) / "arcane_pipeline.state.json",
                   paths.fallback_state_path(state["job_id"])):
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(".tmp")
            tmp.write_text(body, encoding="utf-8")
            tmp.replace(target)
            state["_path"] = str(target)
            return target
        except OSError:
            continue
    return None


def recount(state):
    tiers = {tier: 0 for tier in TIER_ORDER}
    scored = 0
    for record in state.get("cells", {}).values():
        tier = record.get("tier") or TIER_UNSCORED
        tiers[tier] = tiers.get(tier, 0) + 1
        if record.get("score") is not None:
            scored += 1
    state["tiers"] = tiers
    state["counters"] = {
        "settled": len(state.get("cells", {})),
        "scored": scored,
        "unscored": len(state.get("cells", {})) - scored,
        "kontext": len(state.get("kontext_jobs", {})),
    }
    return state


# ---------------------------------------------------------------------------
# Measured metrics -- read from the worker, never from the spec
# ---------------------------------------------------------------------------


def measured_metrics(paths, job_id, job=None, jobs=None):
    """What the run actually did, aggregated across every shard.

    A fleet writes one progress.shard{N}.json per GPU into the same sphere
    directory. Cells and cache checks SUM across shards; the sphere-wide total is
    `full_total`, which every shard reports identically. Nothing here is taken
    from the protocol spec -- an absent number stays None and prints as "not
    reported by the worker".
    """
    metrics = {
        "source": [], "cells_done": None, "cells_total": None, "cache_checks": None, "cache_hits": None,
        "cache_hit_rate": None, "seconds_per_cell": None, "last_cell_seconds": None,
        "cells_per_hour": None, "eta_seconds": None, "elapsed_seconds": None, "shards": [],
    }
    if jobs is None and job is None:
        jobs, source = fetch_jobs(paths)
        job = jobs.get(job_id)
        if job:
            metrics["source"].append(source)
    elif job:
        metrics["source"].append("job record")

    if job:
        metrics["cells_done"] = job.get("atlas_done")
        metrics["cells_total"] = job.get("atlas_total")
        if job.get("cache_checks"):
            metrics["cache_checks"] = int(job["cache_checks"])
            metrics["cache_hits"] = int(job.get("cache_hits") or 0)
            metrics["cache_hit_rate"] = metrics["cache_hits"] / float(metrics["cache_checks"])
        if job.get("last_cell_seconds"):
            metrics["last_cell_seconds"] = float(job["last_cell_seconds"])
        if job.get("cells_per_hour"):
            metrics["cells_per_hour"] = float(job["cells_per_hour"])
            metrics["seconds_per_cell"] = 3600.0 / float(job["cells_per_hour"])
        if isinstance(job.get("eta_seconds"), (int, float)):
            metrics["eta_seconds"] = float(job["eta_seconds"])

    sphere = paths.sphere_dir(job_id)
    if sphere.exists():
        shards = []
        for progress_path in sorted(sphere.glob("progress*.json")):
            try:
                progress = json.loads(progress_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            metrics["source"].append(progress_path.name)
            shards.append(progress)

        if shards:
            done = sum(int(sh.get("current") or 0) for sh in shards)
            # full_total is the whole sphere and is identical on every shard, so
            # take the max rather than summing it; `total` is this shard's slice.
            full = max([int(sh.get("full_total") or 0) for sh in shards] or [0])
            sliced = sum(int(sh.get("total") or 0) for sh in shards)
            checks = sum(int(sh.get("cache_checks") or 0) for sh in shards)
            hits = sum(int(sh.get("cache_hits") or 0) for sh in shards)
            per_hour = sum(float(sh.get("cells_per_hour") or 0.0) for sh in shards)
            last = [float(sh.get("last_cell_seconds") or 0.0) for sh in shards
                    if sh.get("last_cell_seconds")]
            elapsed = max([float(sh.get("elapsed_seconds") or 0.0) for sh in shards] or [0.0])

            metrics["cells_done"] = done
            metrics["cells_total"] = full or sliced or metrics["cells_total"]
            if checks:
                metrics["cache_checks"] = checks
                metrics["cache_hits"] = hits
                metrics["cache_hit_rate"] = hits / float(checks)
            if per_hour > 0:
                # Combined fleet rate: shards render concurrently, so throughput adds
                # and the effective seconds-per-cell for the sphere is the reciprocal.
                metrics["cells_per_hour"] = per_hour
                metrics["seconds_per_cell"] = 3600.0 / per_hour
                remaining = max(0, (metrics["cells_total"] or 0) - done)
                metrics["eta_seconds"] = remaining / (per_hour / 3600.0)
            if last:
                metrics["last_cell_seconds"] = sum(last) / len(last)
            if elapsed:
                metrics["elapsed_seconds"] = elapsed
            metrics["shards"] = [{
                "shard_id": sh.get("shard_id", 0),
                "shard_total": sh.get("shard_total", 1),
                "current": sh.get("current"),
                "total": sh.get("total"),
                "cells_per_hour": sh.get("cells_per_hour"),
                "seconds_per_cell": (3600.0 / float(sh["cells_per_hour"]))
                                    if sh.get("cells_per_hour") else None,
                "cache_hit_rate": (int(sh.get("cache_hits") or 0) / float(sh["cache_checks"]))
                                  if sh.get("cache_checks") else None,
                "last_cell_seconds": sh.get("last_cell_seconds"),
                "current_index": sh.get("current_index"),
            } for sh in sorted(shards, key=lambda x: int(x.get("shard_id") or 0))]

        manifest_path = sphere / "manifest.json"
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                manifest = None
            if isinstance(manifest, dict):
                metrics["source"].append("manifest.json")
                stats = manifest.get("cache_stats") or manifest.get("first_block_cache")
                if isinstance(stats, dict) and stats.get("checks") and not metrics["cache_checks"]:
                    metrics["cache_checks"] = int(stats["checks"])
                    metrics["cache_hits"] = int(stats.get("hits") or 0)
                    metrics["cache_hit_rate"] = metrics["cache_hits"] / float(metrics["cache_checks"])
                if manifest.get("rendered") is not None:
                    metrics["cells_rendered_total"] = int(manifest["rendered"])

    metrics["source"] = sorted(set(metrics["source"]))
    return metrics


def _fmt_duration(seconds):
    seconds = int(max(0.0, float(seconds)))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return "%dd%02dh" % (days, hours)
    if hours:
        return "%dh%02dm" % (hours, minutes)
    if minutes:
        return "%dm%02ds" % (minutes, secs)
    return "%ds" % secs


def fmt_metric(value, unit="", precision=2, missing="not reported by the worker"):
    if value is None:
        return missing
    if unit == "%":
        return "%.1f%%" % (value * 100.0)
    return ("%%.%df%s" % (precision, unit)) % value


def print_metrics(logger, metrics):
    detail = ("" if metrics.get("cache_checks") is None
              else "  (%d hits / %d residual checks)" % (metrics["cache_hits"], metrics["cache_checks"]))
    logger.kv("measured cache hit rate", fmt_metric(metrics.get("cache_hit_rate"), "%") + detail)
    logger.kv("measured s/cell", fmt_metric(metrics.get("seconds_per_cell"), "s"))
    logger.kv("last cell", fmt_metric(metrics.get("last_cell_seconds"), "s"))
    logger.kv("cells/hour", fmt_metric(metrics.get("cells_per_hour"), "", 1))
    logger.kv("metric sources", ", ".join(metrics.get("source") or []) or "none")
    shards = metrics.get("shards") or []
    if len(shards) > 1:
        logger.table(["shard", "cells", "of", "s/cell", "cache hit", "last cell"],
                     [[sh["shard_id"], sh.get("current"), sh.get("total"),
                       fmt_metric(sh.get("seconds_per_cell"), "s", missing="-"),
                       fmt_metric(sh.get("cache_hit_rate"), "%", missing="-"),
                       fmt_metric(sh.get("last_cell_seconds"), "s", missing="-")]
                      for sh in shards])
        logger.kv("fleet", "%d shards; the s/cell above is the COMBINED sphere rate "
                           "(shards render concurrently, so throughput adds)" % len(shards))
    logger.kv("spec claim (UNVERIFIED)",
              "%.1f%% hit rate, %.2f s/cell -- protocol spec section 2.2"
              % (SPEC_CLAIM_HIT_RATE * 100.0, SPEC_CLAIM_SECONDS_PER_CELL))


# ---------------------------------------------------------------------------
# Subcommand: drafts
# ---------------------------------------------------------------------------


def cmd_drafts(args):
    paths = Paths.resolve()
    logger = log()
    entries = list_drafts(paths)
    if args.json:
        logger.line(json.dumps([draft_row(p, d) for p, d in entries], indent=2, sort_keys=True))
        return 0

    logger.header("arcane drafts", "latent-sphere studies bound to the Arcane world forge")
    logger.kv("dir", str(paths.drafts_dir))
    logger.kv("engine default", "adapter=%s cache_threshold=%.2f (protocol spec section 2.2)"
              % (DEFAULT_ADAPTER, DEFAULT_CACHE_THRESHOLD))
    logger.kv("modes", " | ".join("%s: %s" % (name, spec["objective"]) for name, spec in MODES.items()))
    logger.kv("views verdict", "refuse = view_prompts flush the residual cache that character mode "
                               "depends on (worker.py:1645); allow = tolerated; expected = a feature")
    logger.line("")
    rows = []
    for path, draft in entries:
        row = draft_row(path, draft)
        family, _why = draft_mode(path, draft)
        verdict = MODES[family]["view_prompts"] if row["views"] else "-"
        rows.append([row["alias"], family, "{:,}".format(row["cells"]), row["grid"], row["size"],
                     row["steps"], "%.1f" % row["guidance"], row["mode"], "%.2f" % row["seed_lock"],
                     "%.2f" % row["coupling"], row["views"] or "-", verdict,
                     "ok" if not row["problems"] else "INVALID"])
    logger.table(["alias", "family", "cells", "grid", "size", "steps", "guid", "path", "lock",
                  "coupl", "views", "views verdict", "valid"], rows)
    logger.line("")
    for path, draft in entries:
        row = draft_row(path, draft)
        logger.line("  %s" % row["alias"])
        logger.line("    file    %s" % path.name)
        logger.line("    label   %s" % row["label"])
        logger.line("    prompt  %s" % _wrap(row["prompt"], 66, "            "))
        family, why = draft_mode(path, draft)
        logger.line("    mode    %s (%s) -- %s" % (family, why, MODES[family]["objective"]))
        for problem in row["problems"]:
            logger.line("    PROBLEM %s" % problem)
        for severity, message in mode_draft_problems(family, path, draft):
            if severity != OK:
                logger.line("    %-7s %s" % (severity, _wrap(message, 66, "            ")))
        logger.line("")
    logger.line("  %d arcane drafts" % len(entries))
    logger.line("")
    return 0 if entries else 1


# ---------------------------------------------------------------------------
# Subcommand: preflight
# ---------------------------------------------------------------------------


def cmd_preflight(args):
    if getattr(args, "json", False):
        # Sibling modules (sensory_gates) print human banners to stdout when they
        # load degraded. Buffer everything the probes emit so --json stays parseable.
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code, payload = _preflight_report(args)
        sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n")
        return code
    return _preflight_report(args)[0]


def _preflight_report(args):
    paths = Paths.resolve()
    profile_name = resolve_profile_name(paths, getattr(args, "profile", None))
    profile = get_profile(paths, profile_name)
    opts = resolve_options(args, paths, profile)
    profile_source = profile.source
    logger = log()
    checks = []

    def check(stage, name, status, detail):
        checks.append({"stage": stage, "name": name, "status": status, "detail": detail})

    # -- stage: host --------------------------------------------------------
    check("host", "python", OK if sys.version_info >= (3, 8) else FAIL,
          "%d.%d.%d at %s" % (sys.version_info[0], sys.version_info[1], sys.version_info[2], sys.executable))
    check("host", "numpy", OK if module_present("numpy") else WARN,
          "present" if module_present("numpy") else "absent (optional here; the worker needs it)")
    check("host", "Pillow", OK if module_present("PIL") else WARN,
          "present" if module_present("PIL") else "absent (optional here; the worker needs it)")
    check("host", "logging", OK if logger.backend == "arcane_log" else WARN,
          "arcane_log" if logger.backend == "arcane_log" else "arcane_log unavailable; plain renderer in use")
    check("host", "paths", OK if paths.source == "pipeline_paths" else WARN,
          "%s%s" % (paths.source, "" if not paths.notes else " -- " + "; ".join(paths.notes)))
    check("host", "continuum", OK if paths.continuum else WARN, paths.continuum_source)

    # -- stage: silicon -----------------------------------------------------
    gpu = detect_gpu()
    expected_sm = profile.sm
    if not gpu["detected"]:
        check("silicon", "compute capability", UNAVAIL,
              "cannot detect -- %s. Profile %s expects %s; nothing here confirms or denies it."
              % (gpu["reason"], profile_name, expected_sm or "an unspecified sm"))
    else:
        matches = (gpu["sm"] == expected_sm)
        check("silicon", "compute capability", OK if matches else FAIL,
              "%s reports %s (%s)%s" % (gpu["source"], gpu["sm"] or "?", gpu["name"],
                                        "" if matches else "; profile %s expects %s -- sm_120 kernels are NOT "
                                        "sm_100 kernels and will crash or fail to compile"
                                        % (profile_name, expected_sm)))
        if gpu.get("memory_gib"):
            declared = profile.vram_per_gpu_gib
            close = abs(gpu["memory_gib"] - declared) <= max(4.0, declared * 0.05)
            check("silicon", "card VRAM", OK if close else WARN,
                  "%.2f GiB detected, profile declares %.1f GiB" % (gpu["memory_gib"], declared))

    vllm = detect_vllm()
    floor = str(profile.fact("vllm_min_version", "") or "")
    if not vllm["present"]:
        check("silicon", "vLLM", UNAVAIL,
              "cannot detect -- %s. Profile %s requires >= %s for its judge tenants."
              % (vllm.get("reason", "not importable"), profile_name, floor or "an unspecified version"))
    elif floor and not version_at_least(vllm["version"], floor):
        check("silicon", "vLLM", FAIL, "%s installed, profile requires >= %s" % (vllm["version"], floor))
    else:
        check("silicon", "vLLM", OK, "%s (floor %s)" % (vllm["version"], floor or "none declared"))

    dense_ok = bool(profile.fact("native_nvfp4_dense", True))
    moe_ok = bool(profile.fact("native_nvfp4_moe", True))
    sparse = [t["key"] for t in roster(profile, opts) if t["enabled"] and not t["dense"]]
    if sparse and not moe_ok:
        check("silicon", "MoE kernels", FAIL,
              "tenants %s are not marked dense and %s has native_nvfp4_moe=false -- the FlashInfer/CUTLASS "
              "MoE paths are gated behind is_device_capability_family(100) and crash on this card"
              % (", ".join(sparse), profile_name))
    elif sparse:
        check("silicon", "MoE kernels", OK, "%s are MoE; %s declares native NVFP4 MoE support"
              % (", ".join(sparse), profile_name))
    else:
        check("silicon", "MoE kernels", OK,
              "every enabled tenant is dense%s" % ("" if moe_ok else " -- required, %s has no working NVFP4 MoE path"
                                                   % profile_name))
    check("silicon", "NVFP4 dense GEMM", OK if dense_ok else WARN,
          "%s declares native_nvfp4_dense=%s" % (profile_name, dense_ok))

    # -- interconnect and fleet --------------------------------------------
    link = detect_interconnect()
    declared = profile.interconnect
    if not link["detected"]:
        check("silicon", "interconnect", UNAVAIL,
              "cannot detect -- %s. Profile declares %r; unverified, and never assumed from the "
              "card model (public specs say no NVLink on this SKU, the operator reports NVLink)."
              % (link["reason"], declared))
    else:
        agree = declared == "undeclared" or declared.lower().startswith(link["kind"])
        check("silicon", "interconnect", OK if agree else WARN,
              "detected %s%s via %s; profile declares %r%s"
              % (link["kind"], " (%d live links)" % link["links"] if link["links"] else "",
                 link["source"], declared, "" if agree else " -- MISMATCH, trust the probe"))

    tp = [(t["key"], t.get("tensor_parallel")) for t in roster(profile, opts)
          if t["enabled"] and int(t.get("tensor_parallel") or 1) > 1]
    if tp:
        # TP over PCIe is impractical: Gen5 x16 is ~64 GB/s bidirectional against
        # NVLink's ~900. Over NVLink it is viable but still buys latency, not
        # capacity -- every model on this roster fits a single card.
        viable = profile.tensor_parallel_viable
        if viable is False or (link["detected"] and link["kind"] == "pcie"):
            check("silicon", "tensor parallel", FAIL,
                  "tenants %s request TP>1 but the interconnect is PCIe; use shard/data parallelism"
                  % ", ".join(k for k, _v in tp))
        else:
            check("silicon", "tensor parallel", WARN,
                  "tenants %s request TP>1; viable over NVLink but no tenant needs it for capacity"
                  % ", ".join(k for k, _v in tp))
    else:
        check("silicon", "tensor parallel", OK, "every tenant is tensor_parallel=1 (fits one card)")

    fleet = detect_fleet_gpus(profile)
    if not fleet["detected"]:
        check("silicon", "fleet", UNAVAIL,
              "cannot detect GPUs -- planning %d shard(s) from %s"
              % (len(fleet["gpus"]) or 1, fleet["source"] or "nothing"))
    else:
        check("silicon", "fleet", OK if len(fleet["gpus"]) >= profile.gpu_count else WARN,
              "%d GPU(s) %s via %s; profile declares %d"
              % (len(fleet["gpus"]), fleet["gpus"], fleet["source"], profile.gpu_count))
    if len(fleet["gpus"]) > 1:
        live = [g for g in fleet["gpus"]
                if socket_alive(paths.home / ".fluxd" / ("flux-gpu%d.sock" % g), timeout=0.5)]
        check("silicon", "fleet workers", OK if len(live) == len(fleet["gpus"]) else FAIL,
              "%d/%d per-GPU workers answering on .fluxd/flux-gpu{N}.sock%s"
              % (len(live), len(fleet["gpus"]), "" if live else " -- run `flux warm` per GPU"))

    wheel = bool(profile.fact("prebuilt_wheel_available", False))
    check("silicon", "prebuilt vLLM wheel", OK if wheel else WARN,
          "available in the R2 artifact bank" if wheel else
          "NO wheel for %s in the R2 bank (it ships sm100 and sm80 only); vLLM >= %s must be built for %s first"
          % (expected_sm or profile_name, floor or "0.13.0", expected_sm or "this card"))

    # -- stage: engine ------------------------------------------------------
    if paths.flux_bin and pathlib.Path(paths.flux_bin).exists():
        executable = os.access(str(paths.flux_bin), os.X_OK)
        check("engine", "flux binary", OK if executable else FAIL,
              "%s%s" % (paths.flux_bin, "" if executable else " (not executable)"))
    else:
        check("engine", "flux binary", FAIL, "not found (looked at ./flux, $FLUX_BIN, PATH)")

    if socket_alive(paths.fluxd_sock, timeout=1.0):
        try:
            pong = socket_request(paths.fluxd_sock, {"op": "ping"}, timeout=5.0)
            check("engine", "fluxd socket", OK, "%s (device=%s backend=%s loaded=%s)"
                  % (paths.fluxd_sock, pong.get("device", "?"), pong.get("backend", "?"), pong.get("loaded")))
        except (OSError, ValueError) as exc:
            check("engine", "fluxd socket", FAIL,
                  "%s connected but did not answer ping: %s" % (paths.fluxd_sock, _short(exc)))
    else:
        check("engine", "fluxd socket", FAIL,
              "%s not accepting connections (no resident worker)" % paths.fluxd_sock)

    model_dir, model_ok = _resolve_model_dir()
    if opts["flux_precision"] == "bf16":
        check("engine", "FLUX.1-dev weights", OK if model_ok else FAIL,
              "%s%s" % (model_dir or "unresolved",
                        "" if model_ok else " (missing model_index.json / transformer / vae)"))
        check("engine", "generator precision", OK,
              "bf16 -- %s (hard pin: docs/BF16_NATIVE_PRECISION_SPEC.md)"
              % ((profile.tenant("flux") or {}).get("model") or "black-forest-labs/FLUX.1-dev"))
    else:
        q4 = tenant_view(profile, "flux", "q4_k_s") or {}
        check("engine", "FLUX.1-dev weights", WARN,
              "flux precision %s requests %s; the BF16 dir is %s"
              % (opts["flux_precision"], q4.get("model", "the q4 GGUF build"), model_dir or "unresolved"))
        check("engine", "generator precision", WARN,
              "%s -- %s. And note worker.py:submit_atlas stamps precision=bf16 on every atlas job "
              "record, so the resident engine will NOT honour this; it is budgeted, not enforced."
              % (opts["flux_precision"], GENERATOR_Q4_WARNING))

    # -- stage: jury --------------------------------------------------------
    if not opts["jury"]:
        check("jury", "moj_evaluator", SKIP, "--no-jury: every cell will be recorded unscored")
    elif module_present("moj_evaluator"):
        module = load_module("moj_evaluator")
        if module is None:
            check("jury", "moj_evaluator", WARN, "module found but import failed")
        elif callable(getattr(module, "evaluate", None)):
            check("jury", "moj_evaluator", OK, "moj_evaluator.evaluate() available")
        else:
            check("jury", "moj_evaluator", WARN, "imports but exposes no evaluate()")
    else:
        check("jury", "moj_evaluator", WARN, "not importable; every cell would be marked unscored")

    witness = tenant_view(profile, "witness") or {}
    check("jury", "visual witness", OK, "%s (%s, %.2f GiB, mandatory)"
          % (witness.get("model", "?"), witness.get("precision", "?"), witness.get("gib", 0.0)))
    pixtral = tenant_view(profile, "pixtral") or {}
    check("jury", "palette critic", OK, "%s (%s, %.2f GiB, mandatory -- not a toggle)"
          % (pixtral.get("model", "?"), pixtral.get("precision", "?"), pixtral.get("gib", 0.0)))

    gates_tenant = tenant_view(profile, "gates") or {}
    gates_module = load_module("sensory_gates") if module_present("sensory_gates") else None
    if gates_module is None:
        # DINOv2 + SigLIP are mandatory in every profile. On production hardware a
        # gate that will not load is a hard failure; on a host with no CUDA at all it
        # is simply undetectable, and --allow-offline says so.
        check("gates", "sensory_gates", FAIL,
              "not importable -- DINOv2-Giant + SigLIP are mandatory in every profile (%s, %.2f GiB)"
              % (gates_tenant.get("model", "?"), gates_tenant.get("gib", 0.0)))
    else:
        warm = getattr(gates_module, "warm", None)
        if callable(warm):
            try:
                warm(require_full=True)
                check("gates", "sensory_gates", OK, "warm(require_full=True) succeeded")
            except TypeError:
                check("gates", "sensory_gates", WARN, "sensory_gates.warm() does not accept require_full")
            except Exception as exc:
                check("gates", "sensory_gates", FAIL, "warm(require_full=True) failed: %s" % _short(exc))
        else:
            check("gates", "sensory_gates", WARN, "importable but exposes no warm(); load state unverified")
    check("gates", "arcane_aesthetic", OK if module_present("arcane_aesthetic") else WARN,
          "importable" if module_present("arcane_aesthetic")
          else "not importable; the Fortiche invariants go unmeasured")

    # -- stage: governor ----------------------------------------------------
    reachable, detail = governor_reachable(opts, timeout=3.0)
    mode = "remote" if opts["governor_remote"] else "local"
    check("governor", "endpoint (%s)" % mode, OK if reachable else WARN,
          "%s -- %s%s" % (opts["governor_url"], "reachable" if reachable else "unreachable: %s" % detail,
                          "" if reachable else "; prompt mutation falls back to the local Fortiche vocabulary"))
    check("governor", "model", OK, "%s (%s)" % (governor_model(paths, opts) or "?", mode))

    # -- stage: roster hygiene ---------------------------------------------
    retired = set(str(m).lower() for m in (profile.retired_models
                                           or _dig(paths.raw_continuum, "retired", "models") or []))
    active = [t for t in roster(profile, opts) if t["enabled"]]
    offenders = [t["model"] for t in active if str(t["model"]).lower() in retired]
    check("roster", "retired models", OK if not offenders else FAIL,
          "none of the %d retired ids are in the active roster" % len(retired) if not offenders
          else "RETIRED MODEL IN USE: %s" % ", ".join(offenders))
    check("roster", "mandatory tenants", OK if all(
        any(t["key"] == k and t["enabled"] for t in active) for k in MANDATORY_TENANTS) else FAIL,
        "flux, witness, governor, pixtral and gates all present" if all(
            any(t["key"] == k and t["enabled"] for t in active) for k in MANDATORY_TENANTS)
        else "missing: %s" % ", ".join(k for k in MANDATORY_TENANTS
                                       if not any(t["key"] == k and t["enabled"] for t in active)))

    # -- stage: drafts ------------------------------------------------------
    entries = list_drafts(paths)
    check("drafts", "arcane drafts", OK if entries else FAIL, "%d found in %s" % (len(entries), paths.drafts_dir))
    if args.draft:
        draft_path, draft = resolve_draft(paths, args.draft)
        problems = validate_draft(draft)
        check("drafts", "draft %s" % draft_path.stem, OK if not problems else FAIL,
              str(draft_path) if not problems else "; ".join(problems))

    writable = _dir_writable(paths.out_dir)
    check("drafts", "output dir", OK if writable else WARN,
          "%s%s" % (paths.out_dir, "" if writable else " (not writable here; checkpoints fall back to .fluxd/arcane)"))

    # -- stage: surfaces ----------------------------------------------------
    status, detail = surfaces_check(paths, profile_name, timeout=args.surfaces_timeout)
    check("surfaces", "provision_surfaces.py", status, detail)

    # -- stage: vram --------------------------------------------------------
    budget = budget_for(paths, profile, opts)
    for entry in budget_per_gpu(budget):
        check("vram", "gpu %s" % entry.get("gpu", entry.get("index", "?")),
              OK if entry.get("fits", True) else FAIL,
              "%.2f / %.2f GiB usable%s" % (budget_allocated(entry), budget_usable(entry),
                                            "" if entry.get("fits", True) else " -- OVERCOMMIT"))
    check("vram", "budget", OK if budget["fits"] else FAIL,
          "%.2f / %.2f GiB usable per GPU on %s (headroom %.2f GiB)"
          % (budget_allocated(budget), budget_usable(budget),
             profile.gpu or profile_name, budget["headroom_gib"]))

    # -- render -------------------------------------------------------------
    report = {
        "profile": profile_name, "profile_source": profile_source,
        "checks": checks, "paths": paths.as_dict(), "options": opts,
        "gpu": gpu, "vllm": vllm, "interconnect": link, "fleet": fleet,
        "vram": budget, "roster": active,
    }
    if args.json:
        pass
    else:
        logger.header("arcane preflight", "%s -- %s" % (profile_name, profile.description()))
        logger.kv("profile", "%s (%s)" % (profile_name, profile_source))
        logger.kv("target", "%s, %s, %.1f GiB x %d GPU" % (profile.gpu or "?", profile.sm or "?",
                                                            profile.vram_per_gpu_gib, profile.gpu_count))
        logger.kv("host", "%s / %s" % (sys.platform, sys.executable))
        logger.kv("home", str(paths.home))
        logger.kv("out", str(paths.out_dir))
        logger.kv("socket", str(paths.fluxd_sock))
        logger.kv("toggles", "kontext=%s(%s)  governor=%s  flux=%s  jury=%s  pixtral+gates=mandatory"
                  % ("on" if opts["kontext"] else "off", opts["kontext_precision"],
                     "remote" if opts["governor_remote"] else "local", opts["flux_precision"],
                     "on" if opts["jury"] else "off"))
        logger.line("")
        for stage in ("host", "silicon", "engine", "jury", "gates", "governor", "roster", "drafts",
                      "surfaces", "vram"):
            rows = [[c["name"], c["status"], c["detail"]] for c in checks if c["stage"] == stage]
            if not rows:
                continue
            logger.rule(stage)
            logger.table(["check", "status", "detail"], rows)
            logger.line("")
        logger.rule("model roster")
        logger.roster(active)
        logger.line("")
        logger.rule("VRAM allocation")
        logger.kv("source", budget.get("source_note") or budget.get("source"))
        logger.kv("layout", opts["layout"])
        logger.vram(budget)
        per_gpu = budget_per_gpu(budget)
        if per_gpu:
            logger.line("")
            logger.kv("per-GPU", "`fits` is per-GPU, never aggregate -- a 4x96 GiB fleet that "
                                 "overflows one card does not fit")
            logger.table(["gpu", "role", "allocated", "usable", "fits", "tenants"],
                         [[g.get("gpu", g.get("index", "?")), g.get("role", g.get("layout", "")),
                           "%.2f" % budget_allocated(g), "%.2f" % budget_usable(g),
                           "yes" if g.get("fits", True) else "NO",
                           _tenant_names(g.get("tenants"))]
                          for g in per_gpu])
        elif profile.gpu_count > 1:
            logger.warn("profile declares %d GPUs but the budget carries no per-GPU accounting; "
                        "the figures above are for one card only" % profile.gpu_count)
        logger.line("")

    counts = {}
    for entry in checks:
        counts[entry["status"]] = counts.get(entry["status"], 0) + 1
    failures = counts.get(FAIL, 0)
    if args.allow_offline:
        offline = ("flux binary", "fluxd socket", "FLUX.1-dev weights", "compute capability",
                   "vLLM", "sensory_gates", "MoE kernels")
        failures = sum(1 for c in checks if c["status"] == FAIL and c["name"] not in offline)

    if not args.json:
        logger.kv("preflight", "%d ok, %d warn, %d unavail, %d skip, %d fail"
                  % (counts.get(OK, 0), counts.get(WARN, 0), counts.get(UNAVAIL, 0),
                     counts.get(SKIP, 0), counts.get(FAIL, 0)))
        if args.allow_offline and counts.get(FAIL, 0) != failures:
            logger.kv("--allow-offline", "GPU/daemon failures downgraded; exit status reflects only the rest")
        logger.line("")
    logger.event("preflight", profile=profile_name, ok=counts.get(OK, 0), warn=counts.get(WARN, 0),
                 fail=counts.get(FAIL, 0), fits=budget["fits"])
    report["summary"] = {"ok": counts.get(OK, 0), "warn": counts.get(WARN, 0),
                         "unavail": counts.get(UNAVAIL, 0), "skip": counts.get(SKIP, 0),
                         "fail": counts.get(FAIL, 0), "blocking": failures}
    return (1 if failures else 0), report


def surfaces_check(paths, profile_name, timeout=30.0):
    """Fold agent 8's studio-surface provisioner into the status matrix.

    A missing provisioner is reported UNAVAIL. It is never reported as a pass.
    """
    script = paths.home / "provision_surfaces.py"
    if not script.exists():
        return UNAVAIL, "provision_surfaces.py not present; studio surfaces unverified"
    cmd = [sys.executable, str(script), "--check", "--json", "--profile", profile_name]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return WARN, "provision_surfaces.py --check timed out after %.0fs" % timeout
    except Exception as exc:
        return WARN, "could not run provision_surfaces.py --check: %s" % _short(exc)
    summary = ""
    try:
        data = json.loads(proc.stdout or "{}")
        if isinstance(data, dict):
            for key in ("summary", "message", "status"):
                if isinstance(data.get(key), str):
                    summary = data[key]
                    break
            if not summary and isinstance(data.get("surfaces"), (list, dict)):
                summary = "%d surfaces reported" % len(data["surfaces"])
    except ValueError:
        summary = (proc.stdout or "").strip().splitlines()[-1] if proc.stdout.strip() else ""
    if proc.returncode == 0:
        return OK, summary or "provision_surfaces.py --check passed"
    return FAIL, "provision_surfaces.py --check exit %d: %s" % (
        proc.returncode, (summary or proc.stderr or proc.stdout or "").strip()[:200])


def _resolve_model_dir():
    fp = load_module("flux_paths")
    model_dir = None
    if fp is not None and hasattr(fp, "default_model_dir"):
        try:
            model_dir = fp.default_model_dir()
        except Exception:
            model_dir = None
    if not model_dir:
        model_dir = _first_env("MODEL_DIR", "FLUX_MODEL_DIR")
    if not model_dir:
        return None, False
    root = pathlib.Path(model_dir).expanduser()
    return str(root), all((root / name).exists() for name in ("model_index.json", "transformer", "vae"))


def _dir_writable(path):
    probe = pathlib.Path(path)
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return os.access(str(probe), os.W_OK)


# ---------------------------------------------------------------------------
# Subcommand: run
# ---------------------------------------------------------------------------


def apply_mode_defaults(args):
    """Per-mode defaults for the knobs the operator has not pinned.

    Resolution and cell count are undecided by the operator, so they are flags with
    a documented per-mode default rather than a hardcoded constant.
    """
    spec = MODES[args.mode]
    if not getattr(args, "size", 0):
        args.size = spec["size"]
    if not getattr(args, "cells", 0) and spec["cells"]:
        args.cells = spec["cells"]
    if getattr(args, "adapter", None) in (None, ""):
        args.adapter = spec["adapter"]
    if getattr(args, "cache_threshold", None) is None:
        args.cache_threshold = spec["cache_threshold"]
    if getattr(args, "select", None) in (None, ""):
        args.select = spec.get("select") or None
    return args


def cmd_run(args):
    paths = Paths.resolve()
    logger = log()
    draft_path, draft = resolve_draft(paths, args.draft)

    # `run` is the alias: it dispatches on the draft's declared mode. The three
    # mode subcommands set args.mode themselves and skip this.
    mode_reason = "explicit"
    if not getattr(args, "mode", None):
        args.mode, mode_reason = draft_mode(draft_path, draft)
        os.environ.setdefault("ARCANE_MODE", args.mode)
    apply_mode_defaults(args)

    profile_name = resolve_profile_name(paths, getattr(args, "profile", None))
    profile = get_profile(paths, profile_name)
    opts = resolve_options(args, paths, profile)

    if opts["require_jury"] and not opts["jury"]:
        raise SystemExit("--require-jury and --no-jury are contradictory")

    problems = validate_draft(draft)
    if problems:
        raise SystemExit("draft %s is not submittable: %s" % (draft_path, "; ".join(problems)))

    blocking = []
    for severity, message in mode_draft_problems(opts["mode"], draft_path, draft):
        if severity == FAIL and not getattr(args, "allow_view_prompts", False):
            blocking.append(message)
        elif severity == FAIL:
            logger.warn("--allow-view-prompts: %s" % message)
        elif severity == WARN:
            logger.warn(message)
        else:
            logger.kv("mode note", message)
    if blocking:
        raise SystemExit("%s mode refuses this draft:\n  - %s" % (opts["mode"], "\n  - ".join(blocking)))

    # Resolve the fan-out BEFORE the payload so the shard fields in it are the ones
    # that will actually go over the wire -- a dry run that printed shard 0/1 while
    # the run submitted 0/4 would be a lie about the thing it exists to show.
    fleet_gpus, fleet_source = resolve_fleet(args, profile)
    if len(fleet_gpus) > 1 and not getattr(args, "shard", None):
        args.shard = "0/%d" % len(fleet_gpus)

    payload, plan = build_payload(paths, draft, draft_path, args, opts)
    plan["fleet_gpus"] = fleet_gpus
    plan["fleet_source"] = fleet_source
    plan["mode"] = opts["mode"]
    plan["mode_source"] = mode_reason
    plan["objective"] = MODES[opts["mode"]]["objective"]
    plan["novelty_polarity"] = opts["novelty_polarity"]
    plan["cache_role"] = opts["cache_role"]

    resumed = None
    if args.resume:
        resumed = load_state(paths, payload["id"])
        if resumed is None:
            logger.warn("no checkpoint for %s; starting fresh" % payload["id"])
        else:
            # Byte-identical resubmission: the checkpointed payload is the truth, so a
            # resumed run cannot drift from the geometry already on disk.
            payload = resumed["payload"]
            plan = resumed.get("plan", plan)
            logger.kv("resume", "%s from %s (%d cells already settled)"
                      % (payload["id"], resumed.get("_path"), len(resumed.get("cells", {}))))

    budget = budget_for(paths, profile, opts)

    if args.dry_run:
        return _print_dry_run(paths, payload, plan, opts, budget, draft_path, resumed, profile,
                              fleet_gpus, fleet_source)

    if not budget["fits"]:
        logger.error("REFUSED: %s" % budget["overcommit_reason"])
        logger.vram(budget)
        return 2

    state = resumed or new_state(payload, plan, opts, draft_path)
    state["status"] = "running"
    # Shard-aware resume: the fan-out recorded in the checkpoint is reused verbatim,
    # so a resumed run re-partitions the sphere identically. The per-CELL record is
    # deliberately shard-agnostic -- a cell PNG is judged exactly once no matter which
    # GPU drew it, and every shard skips cells already on disk.
    if resumed and resumed.get("fleet", {}).get("gpus"):
        fleet_gpus = list(resumed["fleet"]["gpus"])
        fleet_source = "checkpoint"
    recount(state)
    save_state(paths, state)

    logger.header("arcane %s" % opts["mode"], payload["draft"].get("label", ""))
    logger.kv("objective", plan["objective"])
    logger.kv("job", payload["id"])
    logger.kv("profile", "%s (layout %s)" % (profile_name, opts["layout"]))
    logger.kv("mode", "%s (%s)" % (opts["mode"], plan["mode_source"]))
    logger.kv("novelty gate", opts["novelty_polarity"])
    logger.kv("residual cache", opts["cache_role"])

    if len(fleet_gpus) > 1:
        results = submit_fleet(paths, payload, fleet_gpus, shard_block=payload.get("shard_block"),
                               timeout=args.timeout)
        landed = [r for r in results if r["job"]]
        if not landed:
            logger.error("no shard landed: %s" % "; ".join(r["error"] for r in results))
            return 5
        transport = "fleet (%d shards over GPUs %s, via %s)" % (
            len(landed), ",".join(str(r["gpu"]) for r in landed), fleet_source)
        job = landed[0]["job"]
        state["fleet"] = {"gpus": fleet_gpus, "shard_total": len(fleet_gpus),
                          "shard_block": payload.get("shard_block"), "source": fleet_source}
        state["shards"] = {str(r["shard_id"]): {"gpu": r["gpu"], "socket": r["socket"],
                                                "status": (r["job"] or {}).get("status", "error"),
                                                "error": r["error"]} for r in results}
        logger.table(["shard", "gpu", "socket", "status"],
                     [[r["shard_id"], r["gpu"], os.path.basename(r["socket"]),
                       (r["job"] or {}).get("status") or ("ERROR: " + r["error"])] for r in results])
    else:
        job, transport = submit(paths, payload, transport=args.transport, timeout=args.timeout)
        state["fleet"] = {"gpus": fleet_gpus or [], "shard_total": payload["shard_total"],
                          "shard_block": payload.get("shard_block"), "source": fleet_source}
        state["shards"] = {str(payload["shard_id"]): {"gpu": (fleet_gpus or [None])[0],
                                                      "socket": str(paths.fluxd_sock),
                                                      "status": job.get("status", "?"), "error": ""}}

    state["job"] = {k: job.get(k) for k in ("id", "status", "backend", "output", "atlas_total", "atlas_full_total")}
    state["transport"] = transport
    save_state(paths, state)

    logger.kv("transport", transport)
    logger.kv("output", job.get("output") or plan["sphere_dir"])
    logger.kv("cells", "%d of %d (shard %d/%d, block %d)"
              % (plan["shard_cells"], plan["n_latent"], payload["shard_id"],
                 max(payload["shard_total"], len(fleet_gpus) or 1), payload["shard_block"]))
    logger.kv("adapter", "%s (cache_threshold=%.2f)" % (payload["adapter"], payload["cache_threshold"]))
    logger.kv("kontext", "on" if opts["kontext"] else "off")
    logger.line("")
    logger.event("run_start", job_id=payload["id"], profile=profile_name, mode=opts["mode"],
                 transport=transport, gpus=fleet_gpus, cells=plan["shard_cells"],
                 kontext=opts["kontext"], jury=opts["jury"])

    return _follow(paths, state, opts, args)


def _print_dry_run(paths, payload, plan, opts, budget, draft_path, resumed, profile,
                   fleet_gpus=(), fleet_source=""):
    logger = log()
    profile_name, profile_source = profile.name, profile.source
    logger.header("arcane %s -- DRY RUN" % opts["mode"], "nothing submitted, nothing written")
    logger.kv("mode", "%s (%s)" % (opts["mode"], plan.get("mode_source", "?")))
    logger.kv("objective", plan.get("objective", ""))
    logger.kv("novelty gate", opts["novelty_polarity"])
    logger.kv("residual cache", opts["cache_role"])
    if opts.get("select"):
        logger.kv("orbit selection", opts["select"])
    if opts["mode"] == MODE_SCENES:
        logger.kv("character", opts.get("character_ref") or "none")
    logger.kv("draft", str(draft_path))
    logger.kv("job id", payload["id"])
    logger.kv("profile", "%s (%s) -- %s, %s" % (profile_name, profile_source,
                                                profile.gpu or "?", profile.sm or "?"))
    expressible, why = cli_can_express(payload)
    have_bin = paths.flux_bin is not None and pathlib.Path(paths.flux_bin).exists()
    route = "cli" if (have_bin and expressible) else "socket"
    logger.kv("transport", "%s%s" % (route, "" if expressible else "  (%s)" % why))
    logger.kv("wire", "%s <- one newline-delimited JSON object (internal/daemon/daemon.go)" % paths.fluxd_sock)
    logger.kv("handler", "worker.py:submit_atlas -> worker.py:_render_atlas")
    logger.line("")
    logger.rule("payload (op=atlas_sphere)")
    logger.line(json.dumps(payload, indent=2, sort_keys=True))
    logger.line("")
    logger.rule("derived plan")
    logger.table(["field", "value"], [
        ["grid_total", "{:,}".format(plan["grid_total"])],
        ["n_latent", "{:,}".format(plan["n_latent"])],
        ["index window", "[%d, %d)" % (payload["index_start"], payload["index_end"])],
        ["run_total (all shards)", "{:,}".format(plan["run_total"])],
        ["this shard renders", "{:,}".format(plan["shard_cells"])],
        ["shard", "%d/%d block=%d" % (payload["shard_id"], payload["shard_total"], payload["shard_block"])],
        ["sphere dir", plan["sphere_dir"]],
        ["worker manifest", plan["worker_manifest"]],
        ["worker progress", plan["worker_progress"]],
        ["arcane checkpoint", plan["checkpoint"]],
        ["live run manifest", plan["live_run_manifest"]],
        ["generator", (tenant_view(profile, "flux", opts["flux_precision"]) or {}).get("model", "?")],
        ["flux precision", "%s requested / %s enforced by worker.py"
         % (plan["flux_precision_requested"], plan["precision_enforced_by_engine"])],
        ["jury", "on -- moj_evaluator.evaluate()" if opts["jury"] else "off -- cells recorded unscored"],
        ["kontext", ("on -- %s" % (tenant_view(profile, "kontext", opts["kontext_precision"]) or {}).get("model", "?"))
         if opts["kontext"] else "off (0 GiB)"],
        ["resume", "checkpoint found" if resumed else "no checkpoint"],
        ["fleet", "%s (%s)" % (fleet_gpus or "single worker", fleet_source)],
        ["fan-out", "same job id to each worker with a distinct (shard_id, shard_total); "
                    "mirrors internal/fleet/fleet.go:Pool.SubmitAtlas"],
    ])
    if len(fleet_gpus) > 1:
        logger.line("")
        logger.rule("per-shard submissions (the payload above, with these fields replaced)")
        logger.table(["gpu", "socket", "shard_id", "shard_total", "shard_block", "cells"],
                     [[gpu, ".fluxd/flux-gpu%d.sock" % gpu, i, len(fleet_gpus),
                       payload["shard_block"],
                       len(atlas_shard_slice(range(plan["run_total"]), i, len(fleet_gpus),
                                             payload["shard_block"]))]
                      for i, gpu in enumerate(fleet_gpus)])
    logger.line("")
    logger.rule("VRAM")
    logger.kv("source", budget.get("source_note") or budget.get("source"))
    logger.kv("layout", opts["layout"])
    logger.vram(budget)
    for note in budget.get("notes") or []:
        logger.kv("note", note)
    per_gpu = budget_per_gpu(budget)
    if per_gpu:
        logger.table(["gpu", "role", "allocated", "usable", "fits", "tenants"],
                     [[g.get("gpu", g.get("index", "?")), g.get("role", g.get("layout", "")),
                       "%.2f" % budget_allocated(g), "%.2f" % budget_usable(g),
                       "yes" if g.get("fits", True) else "NO", _tenant_names(g.get("tenants"))]
                      for g in per_gpu])
    if not budget["fits"]:
        # A real run refuses here. The dry run still prints the payload, but says
        # plainly that this configuration would be turned away, and which tenant to shed.
        logger.error("WOULD BE REFUSED: %s" % budget.get("overcommit_reason", "overcommits the card"))
    logger.line("")
    logger.rule("performance")
    logger.kv("measured", "nothing yet -- this is a dry run; `arcane_pipeline.py status` reports what "
                          "the worker actually did once cells land")
    logger.kv("spec claim (UNVERIFIED)", "%.1f%% residual hit rate, %.2f s/cell -- protocol spec section 2.2"
              % (SPEC_CLAIM_HIT_RATE * 100.0, SPEC_CLAIM_SECONDS_PER_CELL))
    logger.line("")
    return 0


def _follow(paths, state, opts, args):
    """Poll the job, run settled cells through gates + jury, route by tier, publish."""
    logger = log()
    job_id = state["job_id"]
    sphere = paths.sphere_dir(job_id)
    deadline = time.time() + args.max_seconds if args.max_seconds else None
    last_save = 0.0
    idle_polls = 0

    while True:
        jobs, source = fetch_jobs(paths)
        job = jobs.get(job_id)
        status = (job or {}).get("status", "unknown")

        settled = _settle_cells(paths, state, opts, sphere, args)
        metrics = measured_metrics(paths, job_id, job)
        state["metrics"] = metrics
        recount(state)

        if settled or time.time() - last_save > 15.0:
            save_state(paths, state)
            write_run_manifest(paths, state, opts)
            last_save = time.time()
            logger.event("checkpoint", job_id=job_id, settled=state["counters"]["settled"],
                         crowned=state["tiers"].get(TIER_CROWNED, 0),
                         unscored=state["counters"]["unscored"],
                         cache_hit_rate=metrics.get("cache_hit_rate"),
                         seconds_per_cell=metrics.get("seconds_per_cell"))

        # One unified bar for the whole fleet: total cells across every shard, the
        # combined rate, and one ETA -- not four separate bars racing each other.
        shards = metrics.get("shards") or []
        if len(shards) > 1:
            state.setdefault("shards", {})
            for sh in shards:
                entry = state["shards"].setdefault(str(sh["shard_id"]), {})
                entry.update({"current": sh.get("current"), "total": sh.get("total"),
                              "seconds_per_cell": sh.get("seconds_per_cell"),
                              "cache_hit_rate": sh.get("cache_hit_rate"),
                              "current_index": sh.get("current_index"), "last_seen": time.time()})
            per_shard = " | ".join(
                "s%d %s/%s%s" % (sh["shard_id"], sh.get("current"), sh.get("total"),
                                 "" if sh.get("seconds_per_cell") is None
                                 else " %.2fs" % sh["seconds_per_cell"])
                for sh in shards)
        else:
            per_shard = ""

        done = metrics.get("cells_done") or 0
        total = metrics.get("cells_total") or state.get("plan", {}).get("shard_cells") or 0
        detail = "settled=%d crowned=%d unscored=%d" % (
            state["counters"]["settled"], state["tiers"].get(TIER_CROWNED, 0),
            state["counters"]["unscored"])
        if metrics.get("eta_seconds"):
            detail += " eta=%s" % _fmt_duration(metrics["eta_seconds"])
        if per_shard:
            detail += "  [%s]" % per_shard
        logger.progress(done, total, label="%s status=%s" % (state.get("options", {}).get("mode", ""), status),
                        detail=detail, rate=metrics.get("seconds_per_cell"),
                        cache_hit=metrics.get("cache_hit_rate"))

        if status in ("done", "error", "cancelled"):
            state["status"] = "complete" if status == "done" else status
            break
        if status == "unknown":
            idle_polls += 1
            if idle_polls >= 3 and source == "ledger":
                logger.warn("job %s is not in the %s; detaching" % (job_id, source))
                state["status"] = "detached"
                break
        else:
            idle_polls = 0
        if deadline and time.time() > deadline:
            state["status"] = "detached"
            logger.warn("--max-seconds reached; the atlas keeps rendering. Resume with --resume.")
            break
        time.sleep(max(1.0, args.poll_interval))

    _settle_cells(paths, state, opts, sphere, args)
    state["metrics"] = measured_metrics(paths, job_id)
    recount(state)
    if opts.get("mode") == MODE_CHARACTER:
        # Reported per ORBIT, not per frame: eight individually beautiful frames of
        # eight different women is a failed turntable, and no per-frame metric sees it.
        cells = sorted(state.get("cells", {}).items())
        coherence = orbit_coherence([record.get("path") for _key, record in cells
                                     if record.get("path")])
        state["orbit_coherence"] = coherence
        logger.rule("orbit coherence")
        if coherence.get("available"):
            logger.kv("adjacent similarity", "%.4f (min %.4f)"
                      % (coherence["adjacent_similarity"], coherence["adjacent_min"]))
            logger.kv("half-orbit similarity", fmt_metric(coherence.get("distant_similarity"), "", 4))
            logger.kv("identity drift", fmt_metric(coherence.get("identity_drift"), "", 4))
            logger.kv("progression", "smooth and monotonic" if coherence["monotonic"]
                      else "%d discontinuity(ies) at %s"
                           % (len(coherence["jumps"]),
                              ", ".join(str(j["between"]) for j in coherence["jumps"])))
            logger.kv("backend", coherence["backend"])
        else:
            logger.warn("orbit coherence not measured: %s" % coherence.get("reason", "unknown"))
        logger.event("orbit_coherence", job_id=job_id, **{
            k: v for k, v in coherence.items() if k not in ("progression", "jumps")})
    save_state(paths, state)
    manifest_path = write_run_manifest(paths, state, opts)

    logger.progress_done("%d cells settled, %d crowned, %d unscored"
                         % (state["counters"]["settled"], state["tiers"].get(TIER_CROWNED, 0),
                            state["counters"]["unscored"]))
    logger.line("")
    logger.rule("run complete")
    logger.kv("status", state["status"])
    print_metrics(logger, state["metrics"])
    logger.kv("tiers", "  ".join("%s=%d" % (t, state["tiers"].get(t, 0)) for t in TIER_ORDER))
    if manifest_path:
        logger.kv("live run manifest", str(manifest_path))
    logger.kv("checkpoint", state.get("_path"))
    logger.line("")
    logger.event("run_complete", job_id=job_id, status=state["status"], **state["counters"])

    if opts["require_jury"] and state["counters"]["unscored"]:
        logger.error("--require-jury: %d cells landed unscored" % state["counters"]["unscored"])
        return 3
    return 0 if state["status"] in ("complete", "detached") else 4


def _settle_cells(paths, state, opts, sphere, args):
    """Process cell PNGs that have appeared since the last pass. Returns the count."""
    if not sphere.exists():
        return 0
    logger = log()
    processed = 0
    known = state.setdefault("cells", {})
    for cell_path in sorted(sphere.glob("cell_*.png")):
        key = cell_path.stem.split("_")[-1]
        if key in known:
            continue
        if not _file_settled(cell_path):
            continue
        record = _judge_cell(paths, state, opts, cell_path, key)
        known[key] = record
        processed += 1
        logger.event("cell", job_id=state["job_id"], cell=key, tier=record.get("tier"),
                     score=record.get("score"), reason=record.get("reason", ""))
        if record.get("tier") == TIER_CROWNED:
            logger.verdict({"cell": key, "tier": record["tier"], "score": record.get("score"),
                            "epigram": record.get("epigram", ""), "path": str(cell_path)})
            _promote(paths, state, cell_path, key, record)
            if opts["kontext"]:
                _refine_crowned(paths, state, opts, cell_path, key, record)
        if args.max_cells and len(known) >= args.max_cells:
            break
    return processed


def _file_settled(path, quiet_seconds=0.75):
    """A PNG the worker is still writing must not be judged."""
    try:
        stat = path.stat()
    except OSError:
        return False
    return stat.st_size > 0 and (time.time() - stat.st_mtime) >= quiet_seconds


def _judge_cell(paths, state, opts, cell_path, key):
    logger = log()
    payload = state["payload"]
    context = {
        "job_id": state["job_id"],
        "cell_index": int(key) if key.isdigit() else key,
        "prompt": payload.get("prompt", ""),
        "subject": payload["draft"].get("subject", ""),
        "study": "arcane_latent_cartography",
        "adapter": payload.get("adapter"),
    }
    gates = gates_evaluate(cell_path, context)
    logger.gates(gates)
    aesthetic = aesthetic_evaluate(cell_path, context)
    logger.fortiche(aesthetic)
    if gates.get("available") and gates.get("pass") is False:
        return {"path": str(cell_path), "tier": TIER_DRIFT, "score": None,
                "reason": "rejected by the sensory gates before the jury",
                "gates": gates, "aesthetic": aesthetic, "ts": time.time()}
    verdict = jury_evaluate(cell_path, context, opts)
    record = {
        "path": str(cell_path),
        "tier": verdict.get("tier", TIER_UNSCORED),
        "score": verdict.get("score"),
        "scale": verdict.get("scale"),
        "epigram": verdict.get("epigram", ""),
        "reason": verdict.get("reason", ""),
        "gates": gates,
        "aesthetic": aesthetic,
        "ts": time.time(),
    }
    if record["tier"] == TIER_UNSCORED and opts["require_jury"]:
        logger.warn("--require-jury: cell %s unscored (%s)" % (key, record["reason"]))
    return record


def _promote(paths, state, cell_path, key, record):
    """Route a crowned cell into the surface vault and the crowned genome."""
    target_dir = paths.surface_dir / "crowned" / state["job_id"]
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / cell_path.name
        if not target.exists():
            shutil.copy2(str(cell_path), str(target))
        record["crowned_path"] = str(target)
    except OSError as exc:
        record["crowned_path"] = ""
        record["promote_error"] = _short(exc)

    entry = {
        "ts": record["ts"], "job_id": state["job_id"], "cell": key, "score": record.get("score"),
        "epigram": record.get("epigram", ""), "prompt": state["payload"].get("prompt", ""),
        "draft": state.get("draft_path", ""), "path": record.get("crowned_path") or str(cell_path),
    }
    try:
        paths.genome_path.parent.mkdir(parents=True, exist_ok=True)
        with paths.genome_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
    except OSError:
        pass


def _refine_crowned(paths, state, opts, cell_path, key, record):
    """Optional Kontext refinement pass. Only reached when the one toggle is on."""
    instruction = (
        "Refine this Arcane Fortiche frame: deepen the visible oil impasto and dry-brush breaks, "
        "sharpen the angular facial planes, and strengthen the graphic rim light. Preserve character "
        "identity, pose, framing and palette exactly."
    )
    request = {
        "op": "submit_img2img",
        "model_family": "kontext",
        "image": str(cell_path),
        "prompt": instruction,
        "steps": opts["kontext_steps"],
        "guidance": opts["kontext_guidance"],
        "strength": 0.35,
        "filename": "arcane_kontext_%s_%s.png" % (state["job_id"], key),
    }
    if not socket_alive(paths.fluxd_sock, timeout=1.0):
        record["kontext"] = {"submitted": False, "reason": "fluxd socket unavailable"}
        return
    try:
        resp = socket_request(paths.fluxd_sock, request, timeout=30.0)
    except (OSError, ValueError) as exc:
        record["kontext"] = {"submitted": False, "reason": _short(exc)}
        return
    if not resp.get("ok"):
        record["kontext"] = {"submitted": False, "reason": resp.get("error", "rejected")}
        return
    job_id = (resp.get("job") or {}).get("id", "")
    record["kontext"] = {"submitted": True, "job_id": job_id, "request": request}
    state.setdefault("kontext_jobs", {})[key] = job_id


# ---------------------------------------------------------------------------
# Live run manifest for the /arcane surface
#
# This is the LIVE RUN artifact: per-cell verdicts, progress and measured metrics.
# provision_surfaces.py writes the STATIC surface manifest (drafts, profile, roster,
# crowned gallery). Two files, two names, no ambiguity:
#     <OUT_DIR>/arcane/runs/<job_id>.run.json   kind=arcane_live_run   (this module)
#     <OUT_DIR>/arcane/runs/index.json          kind=arcane_run_index  (this module)
# ---------------------------------------------------------------------------


def write_run_manifest(paths, state, opts):
    payload = state["payload"]
    draft = payload.get("draft", {})
    cells = state.get("cells", {})
    crowned = sorted(k for k, v in cells.items() if v.get("tier") == TIER_CROWNED)
    profile = get_profile(paths, opts["profile"])
    manifest = {
        "kind": "arcane_live_run",
        "version": 3,
        "generated_by": "arcane_pipeline.py %s" % __version__,
        "companion_static_manifest": "written by provision_surfaces.py; this file is the live run state only",
        "job_id": state["job_id"],
        "status": state.get("status"),
        "updated": time.time(),
        "profile": {
            "name": opts["profile"],
            "gpu": profile.gpu,
            "sm": profile.sm,
            "vram_gib": profile.vram_gib,
            "gpu_count": profile.gpu_count,
            "interconnect": profile.interconnect,
        },
        "draft": {
            "path": state.get("draft_path"), "id": draft.get("id"), "label": draft.get("label"),
            "subject": draft.get("subject"), "prompt": payload.get("prompt"),
            "mode": draft.get("mode"), "traversal": draft.get("traversal"),
        },
        "grid": {
            "n_rows": draft.get("n_rows"), "n_cols": draft.get("n_cols"), "n_latent": payload.get("n_latent"),
            "index_start": payload.get("index_start"), "index_end": payload.get("index_end"),
            "shard_id": payload.get("shard_id"), "shard_total": payload.get("shard_total"),
        },
        "engine": {
            "generator": (tenant_view(profile, "flux", opts["flux_precision"]) or {}).get("model", ""),
            "flux_precision_requested": opts["flux_precision"],
            "precision_enforced_by_engine": "bf16",
            "adapter": payload.get("adapter"), "cache_threshold": payload.get("cache_threshold"),
            "cache_downsample": payload.get("cache_downsample"), "cache_warmup": payload.get("cache_warmup"),
            "size": payload.get("size"), "steps": payload.get("steps"), "guidance": payload.get("guidance"),
        },
        "jury": {
            "enabled": opts["jury"],
            "witness": (tenant_view(profile, "witness") or {}).get("model", ""),
            "critic": (tenant_view(profile, "pixtral") or {}).get("model", ""),
            "gates": (tenant_view(profile, "gates") or {}).get("model", ""),
            "crown_threshold": opts["crown_threshold"], "drift_threshold": opts["drift_threshold"],
        },
        "kontext": {
            "enabled": opts["kontext"],
            "model": (tenant_view(profile, "kontext", opts["kontext_precision"]) or {}).get("model", "")
            if opts["kontext"] else None,
            "refined": len(state.get("kontext_jobs", {})),
        },
        "governor": {
            "remote": opts["governor_remote"], "base_url": opts.get("governor_url"),
            "model": (tenant_view(profile, "governor") or {}).get("model", ""),
        },
        # Measured, not claimed. Nulls mean the worker published nothing.
        "metrics": state.get("metrics", {}),
        "spec_claims_unverified": {
            "cache_hit_rate": SPEC_CLAIM_HIT_RATE,
            "seconds_per_cell": SPEC_CLAIM_SECONDS_PER_CELL,
            "source": "docs/ARCANE_LATENT_CARTOGRAPHY_PROTOCOL_SPEC.md section 2.2",
        },
        "mode": {
            "name": opts.get("mode"),
            "objective": MODES.get(opts.get("mode"), {}).get("objective", ""),
            "novelty_polarity": opts.get("novelty_polarity"),
            "cache_role": opts.get("cache_role"),
            "select": opts.get("select") or None,
            "character_ref": opts.get("character_ref"),
        },
        "fleet": state.get("fleet", {}),
        "shards": state.get("shards", {}),
        # Only populated for `character`: per-orbit identity coherence, or the
        # honest reason it could not be measured.
        "orbit_coherence": state.get("orbit_coherence", {}),
        "tiers": state.get("tiers", {}),
        "counters": state.get("counters", {}),
        "crowned": crowned,
        "cells": [
            {"cell": key, "path": record.get("path"), "tier": record.get("tier"), "score": record.get("score"),
             "epigram": record.get("epigram", ""), "crowned_path": record.get("crowned_path", "")}
            for key, record in sorted(cells.items())
        ],
        "endpoints": SURFACE_ENDPOINTS,
    }
    target = paths.runs_dir / ("%s.run.json" % state["job_id"])
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
        tmp.replace(target)
    except OSError:
        return None
    _update_run_index(paths, manifest)
    return target


def _update_run_index(paths, manifest):
    index_path = paths.runs_dir / "index.json"
    index = {"kind": "arcane_run_index", "runs": []}
    if index_path.exists():
        try:
            loaded = json.loads(index_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and isinstance(loaded.get("runs"), list):
                index = loaded
        except (OSError, ValueError):
            pass
    runs = [r for r in index["runs"] if isinstance(r, dict) and r.get("job_id") != manifest["job_id"]]
    runs.append({
        "job_id": manifest["job_id"], "label": manifest["draft"].get("label"), "status": manifest.get("status"),
        "profile": manifest["profile"].get("name"), "updated": manifest.get("updated"),
        "cells": manifest.get("counters", {}).get("settled", 0), "crowned": len(manifest.get("crowned", [])),
        "manifest": "%s.run.json" % manifest["job_id"],
    })
    runs.sort(key=lambda r: -(r.get("updated") or 0))
    index["runs"] = runs
    index["updated"] = time.time()
    index["kind"] = "arcane_run_index"
    try:
        index_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = index_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(index, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
        tmp.replace(index_path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Subcommand: status
# ---------------------------------------------------------------------------


def cmd_status(args):
    paths = Paths.resolve()
    logger = log()
    job_id = args.job
    if not job_id and args.draft:
        _path, draft = resolve_draft(paths, args.draft)
        job_id = str(draft.get("id") or "")
    if not job_id:
        job_id = _latest_arcane_job(paths)
    if not job_id:
        logger.warn("no arcane atlas found under %s" % paths.atlas_dir)
        logger.line("  submit one with: arcane_pipeline.py run --draft <name>")
        return 1

    jobs, source = fetch_jobs(paths)
    job = jobs.get(job_id)
    metrics = measured_metrics(paths, job_id, job)
    state = load_state(paths, job_id)
    if state:
        recount(state)

    if args.json:
        logger.line(json.dumps({
            "job_id": job_id, "job": job, "job_source": source, "metrics": metrics,
            "tiers": (state or {}).get("tiers", {}), "counters": (state or {}).get("counters", {}),
            "spec_claims_unverified": {"cache_hit_rate": SPEC_CLAIM_HIT_RATE,
                                       "seconds_per_cell": SPEC_CLAIM_SECONDS_PER_CELL},
        }, indent=2, sort_keys=True, default=str))
        return 0

    logger.header("arcane status", job_id)
    logger.kv("record", source if job else "no job record (the worker never saw this id, or the ledger is gone)")
    if job:
        logger.kv("status", "%s / %s" % (job.get("status", "?"), job.get("phase", "?")))
        logger.kv("backend", job.get("backend", "?"))
        logger.kv("adapter", "%s (cache_threshold=%s)" % (job.get("adapter", "?"), job.get("cache_threshold", "?")))
        logger.kv("output", job.get("output", "?"))
    logger.kv("sphere", str(paths.sphere_dir(job_id)))
    logger.line("")

    done = metrics.get("cells_done") or 0
    total = metrics.get("cells_total") or 0
    on_disk = len(list(paths.sphere_dir(job_id).glob("cell_*.png"))) if paths.sphere_dir(job_id).exists() else 0
    logger.progress(done, total, label="cells", detail="%d PNG on disk" % on_disk,
                    rate=metrics.get("seconds_per_cell"))
    logger.line("")
    print_metrics(logger, metrics)
    logger.line("")
    if state:
        logger.rule("tier histogram")
        logger.table(["tier", "cells"], [[tier, state["tiers"].get(tier, 0)] for tier in TIER_ORDER])
        logger.line("")
        logger.kv("jury", "%d scored, %d unscored" % (state["counters"]["scored"], state["counters"]["unscored"]))
        logger.kv("kontext", "%d refinement jobs submitted" % state["counters"]["kontext"])
        logger.kv("checkpoint", state.get("_path"))
    else:
        logger.warn("no arcane checkpoint for this job; the tier histogram is unavailable "
                    "(a run started outside arcane_pipeline.py leaves no jury record)")
    logger.line("")
    return 0


def _latest_arcane_job(paths):
    if not paths.atlas_dir.exists():
        return ""
    best, best_mtime = "", -1.0
    for entry in paths.atlas_dir.glob("*arcane*.sphere"):
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            continue
        if mtime > best_mtime:
            best, best_mtime = entry.name[: -len(".sphere")], mtime
    return best


# ---------------------------------------------------------------------------
# Subcommand: perpetual
# ---------------------------------------------------------------------------


def cmd_perpetual(args):
    paths = Paths.resolve()
    profile_name = resolve_profile_name(paths, getattr(args, "profile", None))
    profile = get_profile(paths, profile_name)
    opts = resolve_options(args, paths, profile)
    logger = log()

    pool = _perpetual_pool(paths, args.drafts)
    if opts.get("mode"):
        # Only feed drafts this mode will actually accept, so the loop cannot spend
        # the card on work its own validator would refuse.
        filtered = []
        for path, draft in pool:
            blocking = [m for sev, m in mode_draft_problems(opts["mode"], path, draft) if sev == FAIL]
            if blocking and not args.allow_view_prompts:
                logger.warn("%s excluded from the %s pool: %s"
                            % (draft_alias(path.stem), opts["mode"], blocking[0].split(".")[0]))
                continue
            filtered.append((path, draft))
        pool = filtered
    if not pool:
        raise SystemExit("no arcane drafts to feed on; check %s" % paths.drafts_dir)

    stop_file = pathlib.Path(args.stop_file or (paths.home / ".fluxd" / "ARCANE_STOP")).expanduser()
    budget = budget_for(paths, profile, opts)
    if not budget["fits"] and not args.dry_run:
        logger.error("REFUSED: %s" % budget["overcommit_reason"])
        logger.vram(budget)
        return 2

    logger.header("arcane perpetual", "keep the card fed with Arcane work, forever")
    logger.kv("profile", profile_name)
    logger.kv("mode", "%s -- %s" % (opts["mode"], MODES[opts["mode"]]["objective"]))
    logger.kv("novelty gate", opts["novelty_polarity"])
    if opts["mode"] == MODE_CHARACTER:
        logger.kv("anti-collapse", "DISABLED. perpetual_feeder.py fires an 'Orthogonal Paradigm "
                                   "Jump' on is_mode_collapsed(); inside a character orbit that "
                                   "signal means SUCCESS, so acting on it would destroy the "
                                   "turntable. This loop inverts it.")
    logger.kv("drafts", ", ".join(draft_alias(p.stem) for p, _d in pool))
    logger.kv("sortie", "%d cells per submission" % args.sortie)
    logger.kv("backpressure", "submit while active jobs < %d (same rule as perpetual_feeder.py)" % args.depth)
    logger.kv("governor", "%s (%s)" % (opts["governor_url"], "remote" if opts["governor_remote"] else "local"))
    logger.kv("genome", str(paths.genome_path))
    logger.kv("stop file", str(stop_file))
    logger.kv("kontext", "on" if opts["kontext"] else "off")
    logger.line("")

    rng = random.Random(args.rng_seed)
    seen_prompts = []
    iteration = 0
    while True:
        if args.max_iterations and iteration >= args.max_iterations:
            logger.kv("done", "--max-iterations reached")
            return 0
        if stop_file.exists():
            logger.warn("stop file present; idling")
            if args.dry_run or args.once:
                return 0
            time.sleep(5.0)
            continue

        depth = 0 if args.dry_run else queue_depth(paths)
        if depth >= args.depth:
            if args.once or args.dry_run:
                logger.kv("backpressure", "queue depth %d >= %d; nothing to do" % (depth, args.depth))
                return 0
            time.sleep(2.0)
            continue

        iteration += 1
        draft_path, draft = (pool[(iteration - 1) % len(pool)] if args.round_robin else rng.choice(pool))
        prompt, prompt_source = _mutate_prompt(paths, opts, draft, seen_prompts, rng)
        seen_prompts.append(prompt)
        del seen_prompts[:-150]

        payload, plan = build_payload(paths, draft, draft_path, _sortie_args(args, draft_path, prompt), opts)
        logger.rule("sortie %d -- %s" % (iteration, draft_alias(draft_path.stem)))
        logger.kv("job", payload["id"])
        logger.kv("prompt source", prompt_source)
        logger.line("  %s" % _wrap(prompt, 70, "  "))

        if args.dry_run:
            expressible, _why = cli_can_express(payload)
            logger.kv("would submit", "%d cells via %s to %s"
                      % (plan["shard_cells"], "cli" if (paths.flux_bin and expressible) else "socket",
                         paths.fluxd_sock))
            logger.kv("payload keys", ", ".join(sorted(payload)))
            logger.line("")
            if args.once or iteration >= (args.max_iterations or 3):
                logger.kv("dry run", "nothing was submitted and nothing was written")
                logger.line("")
                return 0
            continue

        try:
            job, transport = submit(paths, payload, transport=args.transport, timeout=args.timeout)
        except SystemExit as exc:
            logger.error("submission failed: %s" % exc)
            time.sleep(5.0)
            continue
        state = new_state(payload, plan, opts, draft_path)
        state["job"] = {"id": job.get("id"), "status": job.get("status")}
        state["transport"] = transport
        state["perpetual"] = {"iteration": iteration, "prompt_source": prompt_source}
        save_state(paths, state)
        logger.kv("submitted", "%s via %s" % (job.get("status", "?"), transport))
        logger.event("perpetual_sortie", job_id=payload["id"], iteration=iteration,
                     draft=draft_alias(draft_path.stem), prompt_source=prompt_source,
                     cells=plan["shard_cells"])
        logger.line("")

        if args.once:
            return 0
        time.sleep(max(0.5, args.settle))


def _perpetual_pool(paths, spec):
    if not spec:
        return list_drafts(paths)
    return [resolve_draft(paths, name) for name in
            [s.strip() for s in str(spec).split(",") if s.strip()]]


def _sortie_args(args, draft_path, prompt):
    """A tiny argparse-shaped object so perpetual reuses build_payload verbatim."""
    return argparse.Namespace(
        draft=str(draft_path), prompt=prompt, seed=args.seed, cells=args.sortie,
        index_start=args.index_start, index_end=0, sample_count=0, full_grid=False,
        shard=args.shard, shard_block=32, adapter=args.adapter,
        cache_threshold=args.cache_threshold, cache_downsample=args.cache_downsample,
        cache_warmup=args.cache_warmup, batch_size=1, size=args.size, steps=args.steps,
        guidance=args.guidance, backend=args.backend, order=args.order, sample_mode=args.sample_mode,
        id="", fresh=True,
    )


def _mutate_prompt(paths, opts, draft, seen, rng):
    """Mode-correct prompt handling.

    In `character` the prompt must NOT change: worker.py:1645 clears the
    cross-frame residual cache whenever the prompt text differs from the last
    cell, and that cache is the mechanism holding identity constant across the
    orbit. Varying the prompt here would be the anti-collapse machinery destroying
    a SUCCESSFUL turntable -- which is exactly what perpetual_feeder.py's
    is_mode_collapsed() "Orthogonal Paradigm Jump" would do if it were driving
    this loop. It is not; this loop is, and it declines.
    """
    if opts.get("mode") == MODE_CHARACTER:
        return str(draft.get("prompt") or ""), (
            "held constant (character mode: a prompt change flushes the residual cache "
            "that holds identity; novelty polarity is inverted inside an orbit)")
    return _mutate_prompt_varying(paths, opts, draft, seen, rng)


def _mutate_prompt_varying(paths, opts, draft, seen, rng):
    """Governor-mutated prompt, seeded by the crowned-frame genome.

    The Governor may be unreachable; when he is, the local Fortiche invariant vocabulary
    from protocol spec section 3 does the work instead, and says so. Nothing is
    fabricated as having come from him.
    """
    base = str(draft.get("prompt") or "")
    genome = _sample_genome(paths, rng)
    seed_text = genome or base

    system = (
        "You are the Governor of the Influx Vision Arcane World Forge. Rewrite ONE prompt for the "
        "FLUX.1-dev latent cartographer. It must stay inside the Arcane Fortiche aesthetic: visible oil "
        "impasto and dry-brush breaks, sharp angular facial planes, dual-source lighting with a graphic "
        "rim light, Zaun chemtech emerald or Piltover hextech cyan, and absolutely no smooth plastic CGI "
        "skin. Preserve the character identity. Change exactly one axis: costume, backdrop, light, or "
        "palette. Return only the prompt text."
    )
    user = "Mutate this Arcane prompt along one axis: %s" % seed_text[:600]
    text, error = governor_chat(paths, opts, system, user, temperature=rng.uniform(0.85, 1.05))
    if text and len(text) > 40 and text not in seen:
        return text, "governor%s" % (" (seeded from the crowned genome)" if genome else "")

    axes = [k for k in FORTICHE_INVARIANTS if k != "anti_plastic"]
    rng.shuffle(axes)
    additions = [rng.choice(FORTICHE_INVARIANTS[axis]) for axis in axes[:2]]
    additions.append(rng.choice(FORTICHE_INVARIANTS["anti_plastic"]))
    local = ", ".join([seed_text.rstrip(" ,.")] + additions)
    why = "local Fortiche vocabulary"
    if error:
        why += " (governor unreachable: %s)" % error
    return local, why


def _sample_genome(paths, rng, window=40):
    path = paths.genome_path
    if not path.exists():
        return ""
    try:
        lines = [l.strip() for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    except OSError:
        return ""
    for line in reversed(lines[-window:]):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get("prompt") and rng.random() < 0.5:
            return str(entry["prompt"])
    return ""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _add_common_args(parser):
    parser.add_argument("--profile", default=None,
                        help="hardware profile, e.g. rtx-pro-6000 | rtx-pro-6000-x4 | b200 | b300 "
                             "(default: $ARCANE_PROFILE, else the continuum default)")
    parser.add_argument("--layout", default=None,
                        help="multi-GPU placement: balanced (generators + one judge card, default) | "
                             "dense (every card generates) | tp (tensor-parallel judge stack). "
                             "Read from pipeline_paths.vram_budget(); never hardcoded here.")


def _add_toggle_args(parser):
    # Kontext is the ONLY toggle. flux, witness, governor, pixtral and the gates are
    # mandatory in every profile and deliberately have no flags.
    parser.add_argument("--kontext", dest="kontext", action="store_true", default=None,
                        help="enable the Kontext refinement pass on crowned frames (default off)")
    parser.add_argument("--no-kontext", dest="kontext", action="store_false",
                        help="force the Kontext pass off, overriding ARCANE_KONTEXT and the profile")
    parser.add_argument("--kontext-precision", default=None, help="Kontext variant to budget for (profile default)")
    parser.add_argument("--kontext-steps", type=int, default=28)
    parser.add_argument("--kontext-guidance", type=float, default=2.5)
    parser.add_argument("--flux-precision", "--precision", dest="flux_precision", default=None,
                        help="generator precision (default bf16; q4_k_s is a low-VRAM escape hatch that "
                             "costs impasto detail)")
    parser.add_argument("--governor-remote", dest="governor_remote", action="store_true", default=None,
                        help="governor is off-card at its remote base url (0 GiB resident)")
    parser.add_argument("--governor-local", dest="governor_remote", action="store_false",
                        help="governor is resident on the card")
    parser.add_argument("--governor-url", default=None)
    parser.add_argument("--no-jury", action="store_true",
                        help="skip scoring; every cell lands unscored (the tenants stay resident)")
    parser.add_argument("--require-jury", action="store_true", help="fail the run if any cell lands unscored")
    parser.add_argument("--crown-threshold", type=float, default=None)
    parser.add_argument("--drift-threshold", type=float, default=None)


def _add_run_args(parser, mode=None):
    spec = MODES.get(mode) if mode else None
    parser.add_argument("--draft", required=True,
                        help="draft name, alias or path%s"
                             % ("; %s roster: %s" % (mode, ", ".join(spec["drafts"])) if spec else ""))
    if not mode:
        parser.add_argument("--mode", choices=sorted(MODES), default=None,
                            help="force the mode instead of taking it from the draft")
    parser.add_argument("--id", default="")
    parser.add_argument("--fresh", action="store_true",
                        help="timestamp the job id instead of reusing the draft id")
    parser.add_argument("--resume", action="store_true",
                        help="pick up from the checkpoint; settled cells are never re-rendered or re-judged")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the exact payload and exit; touches nothing")
    parser.add_argument("--poll-interval", type=float, default=5.0)
    parser.add_argument("--max-seconds", type=float, default=0.0,
                        help="detach after this long; 0 follows to the end")
    parser.add_argument("--max-cells", type=int, default=0,
                        help="stop judging after this many cells; 0 is unlimited")
    parser.add_argument("--allow-view-prompts", action="store_true",
                        help="override a mode that refuses text-steered rotation "
                             "(character mode: view_prompts flush the residual cache that holds identity)")
    parser.add_argument("--shards", type=int, default=0,
                        help="fan the atlas across N GPU workers as disjoint cell shards "
                             "(0 = one shard per detected GPU)")
    parser.add_argument("--gpus", default="",
                        help="explicit GPU ordinals for the fan-out, e.g. 0,1,2")
    if mode == MODE_CHARACTER:
        parser.add_argument("--select", choices=("dense", "discrete"), default=None,
                            help="dense (default): render one smooth single-prompt orbit so the "
                                 "residual cache never flushes, then select canonical front/three-quarter/"
                                 "side/back frames from it -- more cells, but identity holds. "
                                 "discrete: render only the canonical angles -- fewer cells, but each "
                                 "prompt change clears the cache that was holding identity constant. "
                                 "Undecided by the operator; this is the seam, not a verdict.")
    if mode == MODE_SCENES:
        parser.add_argument("--character", default=None,
                            help="none (default): a standalone backdrop library. "
                                 "<orbit-id>: carry identity in from a character orbit. "
                                 "Undecided by the operator; the seam exists, the machinery for the "
                                 "unchosen path deliberately does not.")


def _add_atlas_args(parser):
    parser.add_argument("--cells", type=int, default=0, help="cap cells rendered; 0 runs the whole draft")
    parser.add_argument("--shard", default=None, help="i/n -- render only this shard's slice of the traversal")
    parser.add_argument("--shard-block", type=int, default=32)
    parser.add_argument("--size", type=int, default=0)
    parser.add_argument("--steps", type=int, default=0)
    parser.add_argument("--guidance", type=float, default=0.0)
    parser.add_argument("--seed", default=None)
    parser.add_argument("--prompt", default=None)
    parser.add_argument("--index-start", type=int, default=0)
    parser.add_argument("--index-end", type=int, default=0)
    parser.add_argument("--sample-count", type=int, default=0)
    parser.add_argument("--full-grid", action="store_true")
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--adapter", default=DEFAULT_ADAPTER)
    parser.add_argument("--cache-threshold", type=float, default=None)
    parser.add_argument("--cache-downsample", type=int, default=None)
    parser.add_argument("--cache-warmup", type=int, default=None)
    parser.add_argument("--order", default=None)
    parser.add_argument("--sample-mode", default=None)
    parser.add_argument("--backend", default="cuda")
    parser.add_argument("--transport", choices=("auto", "cli", "socket"), default="auto")
    parser.add_argument("--timeout", type=float, default=30.0)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="arcane_pipeline.py",
        description="Arcane latent-cartography pipeline: draft -> atlas -> jury -> promote -> publish",
    )
    parser.add_argument("--version", action="version", version="arcane_pipeline %s" % __version__)
    sub = parser.add_subparsers(dest="command")

    p_drafts = sub.add_parser("drafts", help="list the arcane latent-sphere drafts")
    _add_common_args(p_drafts)
    p_drafts.add_argument("--json", action="store_true")
    p_drafts.set_defaults(func=cmd_drafts)

    p_pre = sub.add_parser("preflight", help="verify the whole chain before committing the card")
    _add_common_args(p_pre)
    p_pre.add_argument("--draft", default=None)
    p_pre.add_argument("--mode", choices=sorted(MODES), default=None,
                       help="preflight for one mode's defaults; omit to check the profile itself")
    p_pre.add_argument("--json", action="store_true")
    p_pre.add_argument("--allow-offline", action="store_true",
                       help="downgrade GPU/daemon failures so the rest of the matrix still gates cleanly")
    p_pre.add_argument("--surfaces-timeout", type=float, default=60.0)
    _add_toggle_args(p_pre)
    p_pre.set_defaults(func=cmd_preflight)

    for name in (MODE_CHARACTER, MODE_LATENT, MODE_SCENES, "run"):
        spec = MODES.get(name)
        if spec is None:
            helptext = ("submit an arcane atlas and follow it through the jury; the mode is taken "
                        "from the draft unless --mode says otherwise")
        else:
            helptext = "%s -- %s" % (name, spec["objective"])
        p_mode = sub.add_parser(name, help=helptext)
        _add_common_args(p_mode)
        _add_run_args(p_mode, name if spec else None)
        _add_atlas_args(p_mode)
        _add_toggle_args(p_mode)
        p_mode.set_defaults(func=cmd_run, mode=(name if spec else None))


    p_status = sub.add_parser("status", help="live progress of an arcane atlas")
    _add_common_args(p_status)
    p_status.add_argument("--job", default="")
    p_status.add_argument("--draft", default="")
    p_status.add_argument("--json", action="store_true")
    p_status.set_defaults(func=cmd_status)

    p_perp = sub.add_parser("perpetual", help="keep the card fed with arcane work, forever")
    _add_common_args(p_perp)
    p_perp.add_argument("--mode", choices=sorted(MODES), default=MODE_LATENT,
                        help="which objective the loop feeds (default latent). character holds the "
                             "prompt constant and inverts the novelty gate; scenes varies it freely.")
    p_perp.add_argument("--allow-view-prompts", action="store_true")
    p_perp.add_argument("--drafts", default="", help="comma-separated draft names; default is all arcane drafts")
    p_perp.add_argument("--sortie", type=int, default=64, help="cells per submission (default 64)")
    p_perp.add_argument("--depth", type=int, default=3, help="submit while active jobs < this (default 3)")
    p_perp.add_argument("--settle", type=float, default=1.0)
    p_perp.add_argument("--stop-file", default="")
    p_perp.add_argument("--round-robin", action="store_true", help="cycle drafts in order instead of sampling")
    p_perp.add_argument("--rng-seed", type=int, default=None,
                        help="seed the draft/vocabulary sampler (distinct from the latent --seed)")
    p_perp.add_argument("--once", action="store_true")
    p_perp.add_argument("--max-iterations", type=int, default=0)
    p_perp.add_argument("--dry-run", action="store_true")
    _add_atlas_args(p_perp)
    _add_toggle_args(p_perp)
    p_perp.set_defaults(func=cmd_perpetual)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    # Push the flags into pipeline_paths' environment contract BEFORE anything
    # imports or calls it, so agent 5's resolver and this module agree.
    _env_overrides(args)
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        log().line("")
        log().warn("interrupted; the checkpoint is on disk, resume with --resume")
        return 130


if __name__ == "__main__":
    sys.exit(main())
