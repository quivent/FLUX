#!/usr/bin/env python3
"""Sovereign FLUX · Mixture of Judges (MoJ) real vision-language jury.

This module replaces the string-hash placeholder that used to live in
``jury_evaluator.score_frame()``.  Every number that reaches ``audit.jsonl`` or
``jury.sqlite3`` now originates from one of three places:

  1. An actual HTTP round trip to an OpenAI-compatible vLLM endpoint that was
     handed the rendered PNG as a base64 ``image_url`` content part,
  2. ``uniqueness_tracker`` -- real pixel work over a 128-d handcrafted
     fingerprint, or
  3. ``sensory_gates`` -- real DINOv2/SigLIP triage measurement.

Nothing else is allowed to produce a score.  **The cardinal rule of this file is
that a judge which cannot be reached, times out, or returns unparseable output
NEVER contributes a fabricated number.**  It is marked ``degraded``, it is
excluded from the weighted composite, and the composite is renormalised over the
judges that actually answered.  If no judge survives, the receipt carries
``tier == "unscored"`` and ``composite is None``.  There is no fallback score.

The jury (three distinct models, three distinct rubrics)
-------------------------------------------------------
``unsloth/Qwen3.8-27B-NVFP4``      served-model-name ``visual-witness``  :8001
    STRUCTURAL INSPECTOR.  Anatomy, geometry, line integrity, defect scan.
    Also files a non-scored ``scene_inventory`` -- a factual list of what is
    actually depicted -- which is what lets the text-only synthesist audit
    prompt adherence without hallucinating.

``RedHatAI/pixtral-12b-quantized.w4a16``  served ``pixtral-critic``      :8002
    PALETTE / MEDIUM CRITIC.  Lighting, palette cohesion, medium authenticity,
    impasto.  This is the aesthetic lens, and it is the judge that gets
    ``arcane_aesthetic.FORTICHE_RUBRIC`` spliced into its system prompt on
    Arcane-lineage jobs.

``nvidia/Gemma-4-31B-IT-NVFP4``    served-model-name ``governor``   :8000/remote
    SEMANTIC AUDITOR & SYNTHESIST.  Prompt adherence, scorecard synthesis, and
    a poetic epigram on crowned frames.  Runs in a second phase over the two
    visual judges' sworn testimony.  It may be local (NVFP4 fits the 96 GiB
    Blackwell card) or remote at ``https://governor.influx.vision/v1`` -- the
    base URL comes from ``pipeline_paths``, never from a hardcode here, and the
    code behaves identically either way, including when it is unreachable.

Model ids are resolved from ``pipeline_paths.load_continuum()`` tenants; the ids
above are only the fallback when that module or file is unavailable.

Legacy receipt compatibility
----------------------------
``internal/jury/jury.go``, the ``/jury`` surface and the SQLite schema key off
four judge seats (``pixtral``/``qwen``/``decoder``/``governor``) and four score
names (``harmony``/``structure``/``feature_decoder``/``semantic_fidelity``).
Those are preserved exactly; what changed is who fills them:

    role       model            weight seats      score names
    ---------  ---------------  ----------------  --------------------------
    structure  visual-witness   qwen + decoder    structure, feature_decoder
    aesthetic  pixtral-critic   pixtral           harmony
    synthesis  governor         governor          semantic_fidelity

The ``decoder`` seat was retired in continuum 3.0.0 ("the witness tenant answers
for it"), so the witness carries its weight and its score name is an attributed
alias -- ``receipt["feature_decoder_source"]`` records that -- rather than a
second, invented number.

Environment
-----------
Importable with no network, no GPU, no ``/root``, and no numpy/Pillow/torch.
Every optional dependency and every sibling pipeline module is imported
defensively; all I/O happens inside functions and is timeout-bounded.

CLI
---
    python3 moj_evaluator.py                 offline self-test (default)
    python3 moj_evaluator.py --serve         run the jury daemon loop
    python3 moj_evaluator.py --probe         report judge liveness
    python3 moj_evaluator.py --score IMG --prompt "..."   one-shot real pass
"""
from __future__ import annotations

import argparse
import base64
import glob
import io
import json
import os
import re
import sqlite3
import struct
import sys
import threading
import time
import urllib.error
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

EVALUATOR_NAME = "moj_evaluator"
EVALUATOR_VERSION = "3.1.0"

# --------------------------------------------------------------------------
# Optional dependencies.  None of these may be required at import time: the dev
# machine is macOS with no CUDA, no vLLM, and a bare system python.
# --------------------------------------------------------------------------
try:  # numpy is used transitively by uniqueness_tracker / sensory_gates
    import numpy as _np
except Exception:  # pragma: no cover - environment dependent
    _np = None

try:
    from PIL import Image as _PILImage
except Exception:  # pragma: no cover - environment dependent
    _PILImage = None

try:
    import uniqueness_tracker as _uniqueness_tracker
except Exception:  # pragma: no cover - needs numpy + Pillow
    _uniqueness_tracker = None

try:  # agent 5 owns this; degrade to inline roster defaults without it
    import pipeline_paths as _pipeline_paths
except Exception:
    _pipeline_paths = None

try:  # fast (<10ms) pre-jury triage
    import sensory_gates as _sensory_gates
except Exception:
    _sensory_gates = None

try:  # Arcane conformance + FORTICHE_RUBRIC
    import arcane_aesthetic as _arcane_aesthetic
except Exception:
    _arcane_aesthetic = None

try:  # pre-existing path helper in this repo
    import flux_paths as _flux_paths
except Exception:
    _flux_paths = None


# --------------------------------------------------------------------------
# Logging.  ``arcane_log`` (agent 7) is the house renderer; this module must
# stay importable and runnable without it, so everything goes through a proxy
# that falls back to plain prints method by method.
# --------------------------------------------------------------------------
try:
    from arcane_log import get_logger as _get_logger
except Exception:  # pragma: no cover - written concurrently
    _get_logger = None


class _FallbackLogger(object):
    """Plain-print stand-in for ``arcane_log`` with the same call surface."""

    _WIDTH = 78

    def __init__(self, name: str = "jury") -> None:
        self.name = name
        self.quiet = False

    # -- primitives ------------------------------------------------------
    def _emit(self, level: str, msg: str) -> None:
        if self.quiet and level == "INFO":
            return
        try:
            print("[%s %s] %s" % (self.name.upper(), level, msg), flush=True)
        except Exception:
            pass

    def info(self, msg: str, **_kw: Any) -> None:
        self._emit("INFO", msg)

    def warn(self, msg: str, **_kw: Any) -> None:
        self._emit("WARN", msg)

    warning = warn

    def error(self, msg: str, **_kw: Any) -> None:
        self._emit("ERROR", msg)

    def debug(self, msg: str, **_kw: Any) -> None:
        if not self.quiet:
            self._emit("DEBUG", msg)

    # -- domain renderers ------------------------------------------------
    def degraded(self, what: str, why: str = "", **_kw: Any) -> None:
        self._emit("DEGRADED", "%s -- %s" % (what, why) if why else str(what))

    def gates(self, result: Any, **_kw: Any) -> None:
        if not isinstance(result, dict):
            return
        verdict = "PASS" if result.get("passed") else "REJECT"
        self._emit(
            "GATES",
            "%s  aesthetic=%s novelty=%s adherence=%s palette_delta=%s "
            "backend=%s %s"
            % (
                verdict,
                result.get("aesthetic"),
                result.get("novelty"),
                result.get("adherence"),
                result.get("palette_delta"),
                result.get("backend"),
                "(degraded)" if result.get("degraded") else "",
            ),
        )
        for reason in (result.get("reasons") or [])[:4]:
            self._emit("GATES", "  · %s" % reason)

    def fortiche(self, conformance: Any, **_kw: Any) -> None:
        if not isinstance(conformance, dict):
            return
        self._emit(
            "FORTICHE",
            json.dumps(conformance, sort_keys=True, default=str)[: self._WIDTH * 3],
        )

    def verdict(self, receipt: Any, **_kw: Any) -> None:
        if not isinstance(receipt, dict):
            return
        line = "-" * self._WIDTH
        tier = str(receipt.get("tier") or "?")
        job_id = receipt.get("job_id")
        self._emit("VERDICT", line)
        if tier == "unscored":
            self._emit(
                "VERDICT",
                "job %s  UNSCORED -- no judge survived; NO score was invented"
                % job_id,
            )
            if receipt.get("unscored_reason"):
                self._emit("VERDICT", "  reason: %s" % receipt["unscored_reason"])
        else:
            badge = {
                "masterpiece": "MASTERPIECE",
                "spectacle": "SPECTACLE",
            }.get(tier, tier.upper())
            pct = receipt.get("percentile_rank")
            self._emit(
                "VERDICT",
                "job %s  %s  curved=%s  raw=%s  percentile=%s"
                % (
                    job_id,
                    badge,
                    receipt.get("curved_score"),
                    receipt.get("raw_composite"),
                    pct,
                ),
            )
        prompt = str(receipt.get("prompt") or "")
        if prompt:
            self._emit("VERDICT", "  prompt: %s" % prompt[:100])
        for judge in receipt.get("judges") or []:
            if not isinstance(judge, dict):
                continue
            if judge.get("degraded"):
                self._emit(
                    "VERDICT",
                    "  %-10s %-16s DEGRADED  %s"
                    % (judge.get("role"), judge.get("model"), (judge.get("error") or "")[:60]),
                )
            else:
                self._emit(
                    "VERDICT",
                    "  %-10s %-16s %6s  %s"
                    % (
                        judge.get("role"),
                        judge.get("model"),
                        judge.get("score"),
                        (judge.get("critique") or "")[:60],
                    ),
                )
        uniq = receipt.get("uniqueness") or {}
        if isinstance(uniq, dict):
            self._emit(
                "VERDICT",
                "  novelty: %s (%s)" % (uniq.get("score"), uniq.get("category")),
            )
        if receipt.get("epigram"):
            self._emit("VERDICT", "  epigram: %s" % receipt["epigram"])
        self._emit("VERDICT", line)

    def event(self, kind: str, **fields: Any) -> None:
        try:
            record = {"ts": time.time(), "kind": kind, "logger": self.name}
            record.update(fields)
            print(json.dumps(record, default=str), flush=True)
        except Exception:
            pass


class _LogProxy(object):
    """Dispatch to ``arcane_log`` when present, else to the fallback.

    ``arcane_log`` is being written concurrently, so every call is attempted
    against the real logger and silently re-routed to the fallback if the
    method is missing or raises.  Logging must never be able to kill a jury
    pass.
    """

    def __init__(self, name: str = "jury") -> None:
        self.name = name
        self._fallback = _FallbackLogger(name)
        self._real = None
        if _get_logger is not None:
            try:
                self._real = _get_logger(name)
            except Exception:
                self._real = None

    @property
    def backend(self) -> str:
        return "arcane_log" if self._real is not None else "fallback"

    def set_quiet(self, quiet: bool) -> None:
        self._fallback.quiet = bool(quiet)
        for attr in ("quiet", "set_quiet"):
            target = getattr(self._real, attr, None)
            if callable(target):
                try:
                    target(bool(quiet))
                    return
                except Exception:
                    pass
            elif target is not None:
                try:
                    setattr(self._real, "quiet", bool(quiet))
                    return
                except Exception:
                    pass

    def _dispatch(self, method: str, *args: Any, **kwargs: Any) -> None:
        real = getattr(self._real, method, None)
        if callable(real):
            try:
                real(*args, **kwargs)
                return
            except Exception:
                pass
        fallback = getattr(self._fallback, method, None)
        if callable(fallback):
            try:
                fallback(*args, **kwargs)
            except Exception:
                pass

    def info(self, msg: str, **kw: Any) -> None:
        self._dispatch("info", msg, **kw)

    def warn(self, msg: str, **kw: Any) -> None:
        self._dispatch("warn", msg, **kw)

    def error(self, msg: str, **kw: Any) -> None:
        self._dispatch("error", msg, **kw)

    def degraded(self, what: str, why: str = "", **kw: Any) -> None:
        self._dispatch("degraded", what, why, **kw)

    def verdict(self, receipt: Dict[str, Any], **kw: Any) -> None:
        self._dispatch("verdict", receipt, **kw)

    def gates(self, result: Any, **kw: Any) -> None:
        self._dispatch("gates", result, **kw)

    def fortiche(self, conformance: Any, **kw: Any) -> None:
        self._dispatch("fortiche", conformance, **kw)

    def event(self, kind: str, **fields: Any) -> None:
        self._dispatch("event", kind, **fields)


LOG = _LogProxy("jury")


def get_log() -> _LogProxy:
    """The module logger, so ``jury_evaluator`` shares one sink."""
    return LOG


# --------------------------------------------------------------------------
# Roster
# --------------------------------------------------------------------------

#: Model ids that are stale or out of scope.  If one of these shows up in a
#: continuum file we refuse it rather than silently serving a banned model.
BANNED_MODEL_IDS = frozenset(
    m.lower()
    for m in (
        "Qwen/Qwen2.5-VL-7B-Instruct",
        "Qwen/Qwen3-VL-8B-Instruct",
        "google/gemma-4-12b-it",
        "black-forest-labs/FLUX.1-schnell",
    )
)

VISUAL_WITNESS = "visual-witness"
PIXTRAL_CRITIC = "pixtral-critic"
GOVERNOR = "governor"

#: Fallback roster, used only when ``pipeline_paths.load_continuum()`` cannot
#: supply the tenants.  Ports match ``jury_continuum.toml`` 3.0.0's port map.
DEFAULT_ENDPOINTS: Dict[str, Dict[str, Any]] = {
    VISUAL_WITNESS: {
        "base_url": "http://127.0.0.1:8001/v1",
        "model": "visual-witness",
        "hf_model": "unsloth/Qwen3.8-27B-NVFP4",
        "vision": True,
        "enabled": True,
        "remote": False,
    },
    PIXTRAL_CRITIC: {
        "base_url": "http://127.0.0.1:8002/v1",
        "model": "pixtral-critic",
        "hf_model": "RedHatAI/pixtral-12b-quantized.w4a16",
        "vision": True,
        "enabled": True,
        "remote": False,
    },
    GOVERNOR: {
        # Local when the local tenant is up, remote otherwise. Resolved from
        # pipeline_paths.GOVERNOR_BASE_URL first -- never hardcoded here.
        "base_url": "https://governor.influx.vision/v1",
        "model": "governor",
        "hf_model": "nvidia/Gemma-4-31B-IT-NVFP4",
        # The governor tenant is provisioned without multimodal slots, so it
        # adjudicates over testimony rather than pixels. Flip to True (or set
        # `vision = true` on the continuum tenant) if that ever changes.
        "vision": False,
        "enabled": True,
        "remote": True,
    },
}

#: Continuum tenant name -> served-model-name, for the three vLLM judges.
TENANT_TO_SERVED = {
    "witness": VISUAL_WITNESS,
    "visual_witness": VISUAL_WITNESS,
    "pixtral": PIXTRAL_CRITIC,
    "governor": GOVERNOR,
}


class JudgeSpec(NamedTuple):
    """Descriptor for one judge role."""

    role: str
    title: str
    endpoint: str  # key into the endpoint table
    legacy_key: str  # primary weights/strictness seat (jury.go compatible)
    extra_legacy_keys: Tuple[str, ...]  # additional weight seats this judge carries
    score_key: str  # primary key inside receipt["jury_scores"]
    alias_score_keys: Tuple[str, ...]  # attributed aliases of the same number
    phase: int  # 1 = visual wave (parallel), 2 = synthesis wave
    default_weight: float
    default_gamma: float
    subscores: Tuple[str, ...]
    evidence_keys: Tuple[str, ...]  # non-scored observations forwarded to phase 2
    enabled_by_default: bool


JUDGE_STRUCTURE = JudgeSpec(
    role="structure",
    title="Structural Inspector",
    endpoint=VISUAL_WITNESS,
    legacy_key="qwen",
    # continuum 3.0.0 retired the standalone decoder tenant: "the witness
    # tenant answers for it", so the witness carries the decoder seat's weight.
    extra_legacy_keys=("decoder",),
    score_key="structure",
    alias_score_keys=("feature_decoder",),
    phase=1,
    default_weight=0.35,
    default_gamma=1.2,
    subscores=(
        "anatomy",
        "geometry",
        "edge_integrity",
        "artifact_freedom",
        "focus_coherence",
    ),
    evidence_keys=("scene_inventory", "worst_defect"),
    enabled_by_default=True,
)

JUDGE_AESTHETIC = JudgeSpec(
    role="aesthetic",
    title="Palette & Medium Critic",
    endpoint=PIXTRAL_CRITIC,
    legacy_key="pixtral",
    extra_legacy_keys=(),
    score_key="harmony",
    alias_score_keys=(),
    phase=1,
    default_weight=0.35,
    default_gamma=2.0,
    subscores=(
        "palette_cohesion",
        "lighting_authenticity",
        "medium_authenticity",
        "tonal_range",
        "atmosphere",
    ),
    evidence_keys=("observed_palette", "observed_medium", "observed_light"),
    enabled_by_default=True,
)

JUDGE_SYNTHESIS = JudgeSpec(
    role="synthesis",
    title="Governor · Semantic Auditor & Synthesist",
    endpoint=GOVERNOR,
    legacy_key="governor",
    extra_legacy_keys=(),
    score_key="semantic_fidelity",
    alias_score_keys=(),
    phase=2,
    default_weight=0.30,
    default_gamma=2.2,
    subscores=(
        "subject_fidelity",
        "attribute_binding",
        "scene_completeness",
        "evidence_consistency",
        "exhibition_readiness",
    ),
    evidence_keys=("epigram", "missing_elements"),
    enabled_by_default=True,
)

#: The jury roster, in dispatch order.
JUDGES: Tuple[JudgeSpec, ...] = (JUDGE_STRUCTURE, JUDGE_AESTHETIC, JUDGE_SYNTHESIS)

SPEC_BY_ROLE: Dict[str, JudgeSpec] = {s.role: s for s in JUDGES}

#: Legacy score names that must always be present in receipt["jury_scores"].
LEGACY_SCORE_KEYS = ("harmony", "structure", "feature_decoder", "semantic_fidelity")

#: Legacy weight/strictness seats jury.go writes.
LEGACY_SEATS = ("pixtral", "qwen", "decoder", "governor")


# --------------------------------------------------------------------------
# Role prompts.  Three models, three genuinely different rubrics.
# --------------------------------------------------------------------------

_JSON_CONTRACT = (
    "Return ONE JSON object and nothing else. No preamble, no markdown fence, no "
    "trailing commentary. Every score is an integer or one-decimal float on a "
    "0-100 scale where 100 is the theoretical maximum, not 'the best AI image you "
    "have seen'. If you cannot see the image, or the image is blank or corrupt, "
    'set every score to null and put the reason in "critique" -- do NOT guess a '
    "number. A missing score is recoverable; an invented one poisons the whole "
    "archive."
)

_CALIBRATION = (
    "Calibration anchors (absolute, museum-grade, NOT graded on a curve against "
    "other generative output):\n"
    "  96-100  no fault findable at 100% zoom by a hostile specialist.\n"
    "  86-95   one trivial fault a specialist would have to hunt for.\n"
    "  70-85   competent; a careful viewer finds a real fault within ten seconds.\n"
    "  45-69   an obvious fault that a casual viewer notices immediately.\n"
    "  20-44   structurally or tonally broken in a way that cannot be cropped out.\n"
    "  0-19    failed render.\n"
    "Most competent output lands in the 70-85 band. Scores above 90 must be "
    "earned and must be justified by the critique. Never award a score you "
    "cannot point at concrete evidence for."
)

SYSTEM_PROMPT_STRUCTURE = """You are the Structural Inspector of a museum-grade image jury.

You care about exactly one thing: whether the depicted world is physically and
geometrically coherent. Beauty, palette, mood and prompt adherence belong to
OTHER judges and must not move your score.

Inspect in this order, looking for a reason to deduct:
 1. ANATOMY -- count fingers, hands, limbs, eyes, ears, teeth, and the legs of
    animals and furniture. Check joint direction, limb attachment, symmetry of
    paired features, and whether any body part merges into another object.
 2. GEOMETRY -- vanishing points, horizon consistency, whether parallel
    architecture stays parallel, whether reflections and cast shadows agree
    with the implied light position, whether object scale holds across depth.
 3. EDGE INTEGRITY -- contour breaks, melted or doubled outlines, boundaries
    that dissolve into the background, hair/fur/fabric terminating in mush.
 4. ARTIFACT FREEDOM -- diffusion smear, repeated texture tiling, chromatic
    fringing, checkerboard or grid residue, garbled glyphs, seams, duplicated
    limbs or objects at the frame edge.
 5. FOCUS COHERENCE -- whether the depth of field is optically consistent, or
    whether things are sharp and soft where no lens could make them so.

You also file the jury's factual record. In "scene_inventory", list plainly and
literally what is actually depicted -- subjects, their count, their materials,
the setting, the time of day. Do not interpret and do not flatter: a later
judge who cannot see the image will audit the brief against your inventory, so
an inaccurate inventory is worse than a harsh score.

{calibration}

{contract}
Schema:
{{"anatomy": <0-100>, "geometry": <0-100>, "edge_integrity": <0-100>,
  "artifact_freedom": <0-100>, "focus_coherence": <0-100>, "overall": <0-100>,
  "scene_inventory": ["<literal thing depicted>", "..."],
  "worst_defect": "<the single worst defect and where it is in the frame, or
  'none findable'>",
  "critique": "<one sentence naming that worst defect, or stating that none was
  findable>"}}"""

SYSTEM_PROMPT_AESTHETIC = """You are the Palette & Medium Critic of a museum-grade image jury.

You care about exactly one thing: whether this image is convincing AS AN OBJECT
IN A MEDIUM. Anatomy, geometry and prompt adherence belong to OTHER judges and
must not move your score.

Interrogate in this order:
 1. PALETTE COHESION -- is there a governing colour logic, or an unmotivated
    accumulation of hues? Penalise the generative defaults: oversaturated
    teal/orange complementaries, unmotivated magenta rim light, the purple-and-
    cyan gradient that stands in for atmosphere. Reward restraint, a deliberate
    limited palette, and considered neutrals.
 2. LIGHTING AUTHENTICITY -- can you name the light sources and their colour
    temperature? Do key, fill and bounce behave like real light in a real
    volume? Penalise the ubiquitous sourceless ambient glow, and highlights
    that sit on top of the form instead of wrapping it.
 3. MEDIUM AUTHENTICITY -- if it claims oil, does it carry loaded impasto,
    visible brush direction and edge quality that changes with pressure? If
    ink, real dry-brush, bleed and reserved white? If photography, real lens
    character, grain structure and falloff? Penalise the plastic
    digital-airbrush surface that belongs to no medium at all.
 4. TONAL RANGE -- is there a true black and a true white with a structured
    midtone ladder, or is everything crushed into mid-grey haze or blown out?
 5. ATMOSPHERE -- does aerial perspective, haze density and particulate light
    build actual depth, or is it a flat sticker over a gradient?

Also record what you observe, plainly, in "observed_palette",
"observed_medium" and "observed_light". A later judge who cannot see the image
will reason from these, so describe rather than praise.

{calibration}

{contract}
Schema:
{{"palette_cohesion": <0-100>, "lighting_authenticity": <0-100>,
  "medium_authenticity": <0-100>, "tonal_range": <0-100>, "atmosphere": <0-100>,
  "overall": <0-100>,
  "observed_palette": "<the actual dominant hues and their relationship>",
  "observed_medium": "<the medium the surface actually reads as>",
  "observed_light": "<the light sources you can name and their temperature>",
  "critique": "<one sentence naming the single decision that most cheapens the
  image, or the single decision that most elevates it>"}}"""

SYSTEM_PROMPT_SYNTHESIS = """You are the Governor, presiding over a museum-grade image jury.

YOU CANNOT SEE THE IMAGE. You are given the commissioning prompt and the sworn
testimony of the visual judges who did see it -- the Structural Inspector's
factual scene inventory and defect findings, and the Palette & Medium Critic's
observations -- plus mechanical evidence from the perceptual uniqueness tracker
and the sensory gates.

Absolute constraint: you must NOT invent visual observations. You may only
reason over the evidence you were handed. If the evidence is thin, say so and
score conservatively; if it is contradictory, say which judge you find more
credible and why. Fabricating an observation is the one unforgivable act in
this chamber.

Adjudicate:
 1. SUBJECT_FIDELITY -- enumerate every noun the prompt requires, then check it
    against the Structural Inspector's scene_inventory. A required subject that
    does not appear in the inventory is a hard failure, not a deduction.
 2. ATTRIBUTE_BINDING -- every adjective, material, colour and count in the
    brief must be attached to the RIGHT noun in the reported evidence. The
    classic failure is attribute leakage: the brief asks for brass gears and
    lapis inlay; the testimony describes brass inlay and lapis gears. Hunt for
    it specifically.
 3. SCENE_COMPLETENESS -- required setting, time of day, weather, framing, lens
    language and any stated art-historical reference must be honoured, not just
    the headline subject.
 4. EVIDENCE_CONSISTENCY -- do the judges' subscores and prose agree with each
    other and with their own observations? A judge whose critique describes a
    serious defect while scoring 92 is not credible; weigh accordingly.
 5. EXHIBITION_READINESS -- on this testimony, would you hang this at size in a
    room where the work is looked at closely?

Then synthesise. "overall" is the scorecard for delivery-against-brief, and it
is the jury's semantic verdict.

If and only if the work is genuinely exhibition-grade on this evidence, write
one line of "epigram": a single spare poetic sentence fit to caption a crowned
frame. Otherwise set "epigram" to null. Do not write an epigram for competent
work; the epigram is a crown, not a courtesy.

{calibration}

{contract}
Schema:
{{"subject_fidelity": <0-100>, "attribute_binding": <0-100>,
  "scene_completeness": <0-100>, "evidence_consistency": <0-100>,
  "exhibition_readiness": <0-100>, "overall": <0-100>,
  "missing_elements": ["<prompt element absent from the testimony>", "..."],
  "epigram": "<one spare poetic line, or null>",
  "critique": "<one sentence stating your verdict and the decisive piece of
  testimony behind it>"}}"""

SYSTEM_PROMPTS = {
    "structure": SYSTEM_PROMPT_STRUCTURE,
    "aesthetic": SYSTEM_PROMPT_AESTHETIC,
    "synthesis": SYSTEM_PROMPT_SYNTHESIS,
}

#: Only the aesthetic lens is graded against the house style.
FORTICHE_ROLES = ("aesthetic",)


def fortiche_rubric() -> str:
    """``arcane_aesthetic.FORTICHE_RUBRIC`` if that module is available."""
    if _arcane_aesthetic is None:
        return ""
    rubric = getattr(_arcane_aesthetic, "FORTICHE_RUBRIC", None)
    if isinstance(rubric, str):
        return rubric
    if isinstance(rubric, (list, tuple)):
        return "\n".join(str(x) for x in rubric)
    if isinstance(rubric, dict):
        return "\n".join("%s: %s" % (k, v) for k, v in rubric.items())
    return ""


def system_prompt_for(role: str, arcane: bool = False) -> str:
    """Render the system prompt for ``role``.

    On an Arcane-lineage job the Palette & Medium Critic gets
    ``FORTICHE_RUBRIC`` spliced in, so the aesthetic lens grades against the
    house style rather than generic good taste.
    """
    template = SYSTEM_PROMPTS.get(role, SYSTEM_PROMPT_SYNTHESIS)
    text = template.format(calibration=_CALIBRATION, contract=_JSON_CONTRACT)
    if arcane and role in FORTICHE_ROLES:
        rubric = fortiche_rubric()
        if rubric:
            text += (
                "\n\nHOUSE RUBRIC (FORTICHE) -- this render is an Arcane-lineage "
                "job and is additionally bound by the following. Where the house "
                "rubric and generic good taste disagree, the house rubric wins:\n"
                + rubric.strip()
            )
    return text


# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------

_PATH_LOCK = threading.Lock()
_OUTPUT_DIR_CACHE: Optional[str] = None


def _env(*names: str) -> str:
    for name in names:
        value = os.environ.get(name, "")
        if value:
            return value
    return ""


def _pipeline_attr(*names: str) -> Any:
    """Read an attribute (or zero-arg callable) off ``pipeline_paths``."""
    if _pipeline_paths is None:
        return None
    for name in names:
        value = getattr(_pipeline_paths, name, None)
        if value is None:
            continue
        if callable(value):
            try:
                value = value()
            except Exception:
                continue
        if value:
            return value
    return None


def _dir_usable(path: str) -> bool:
    if not path:
        return False
    try:
        return os.path.isdir(path)
    except Exception:
        return False


def output_dir(refresh: bool = False) -> str:
    """Resolve the FLUX output directory without creating or writing anything.

    Order: explicit env -> ``pipeline_paths`` -> continuum ``verdict.audit_log``
    -> the production ``/root/Models/flux-output`` -> ``flux_paths`` -> ``~``.
    """
    global _OUTPUT_DIR_CACHE
    with _PATH_LOCK:
        if _OUTPUT_DIR_CACHE and not refresh:
            return _OUTPUT_DIR_CACHE

    candidates: List[str] = []
    env_dir = _env("MOJ_OUTPUT_DIR", "FLUX_OUTPUT_DIR", "OUT_DIR")
    if env_dir:
        candidates.append(env_dir)

    from_pp = _pipeline_attr("output_dir", "OUTPUT_DIR", "out_dir", "OUT_DIR")
    if from_pp:
        candidates.append(str(from_pp))

    audit_log = _dig(load_continuum() or {}, ("verdict", "audit_log"))
    if isinstance(audit_log, str) and os.path.isabs(audit_log):
        candidates.append(os.path.dirname(audit_log))

    candidates.append("/root/Models/flux-output")

    if _flux_paths is not None:
        try:
            candidates.append(str(_flux_paths.default_out_dir()))
        except Exception:
            pass

    candidates.append(os.path.join(os.path.expanduser("~"), "Models", "flux-output"))

    chosen = ""
    for cand in candidates:
        if _dir_usable(cand):
            chosen = cand
            break
    if not chosen:
        # Nothing exists yet: prefer the first explicit candidate so the writer
        # (jury_evaluator) can mkdir it.
        chosen = next((c for c in candidates if c), "/root/Models/flux-output")

    with _PATH_LOCK:
        _OUTPUT_DIR_CACHE = chosen
    return chosen


def sqlite_path() -> str:
    from_pp = _pipeline_attr("jury_sqlite", "JURY_SQLITE", "sqlite_db", "SQLITE_DB")
    if from_pp:
        return str(from_pp)
    return os.path.join(output_dir(), "jury.sqlite3")


def jobs_ledger_path() -> str:
    """Resolve the fluxd jobs ledger the daemon tails."""
    env_ledger = _env("MOJ_JOBS_LEDGER", "FLUX_JOBS_LEDGER")
    if env_ledger:
        return env_ledger
    from_pp = _pipeline_attr("jobs_ledger", "JOBS_LEDGER", "jobs_ledger_path")
    if from_pp:
        return str(from_pp)
    fluxd = _pipeline_attr("FLUXD_DIR", "fluxd_dir")
    if fluxd:
        candidate = os.path.join(str(fluxd), "flux-gpu0.jobs.jsonl")
        if os.path.exists(candidate) or os.path.isdir(str(fluxd)):
            return candidate
    prod = "/root/CLIs/flux/.fluxd/flux-gpu0.jobs.jsonl"
    if os.path.exists(prod):
        return prod
    here = os.path.dirname(os.path.abspath(__file__))
    local = os.path.join(here, ".fluxd", "flux-gpu0.jobs.jsonl")
    if os.path.exists(local):
        return local
    return prod


def _align_uniqueness_db() -> None:
    """Point ``uniqueness_tracker`` at the resolved output dir.

    ``uniqueness_tracker`` hardcodes ``/root/Models/flux-output/jury.sqlite3``
    and is owned by another module, so we rebind the constant at runtime rather
    than editing it.  Off the production node this is what keeps the fingerprint
    ring buffer working instead of silently returning its 75.0 placeholder.
    """
    if _uniqueness_tracker is None:
        return
    try:
        target = sqlite_path()
        if getattr(_uniqueness_tracker, "SQLITE_DB", None) != target:
            _uniqueness_tracker.SQLITE_DB = target
    except Exception:
        pass


# --------------------------------------------------------------------------
# Continuum / runtime configuration
# --------------------------------------------------------------------------

_CONT_LOCK = threading.Lock()
_CONT_CACHE: Tuple[float, Optional[Dict[str, Any]]] = (0.0, None)
_CONT_TTL = 30.0


def _dig(obj: Any, path: Tuple[str, ...]) -> Any:
    cur = obj
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def load_continuum(refresh: bool = False) -> Optional[Dict[str, Any]]:
    """``pipeline_paths.load_continuum()`` if available, else ``None``.

    Agent 5 owns ``pipeline_paths``; that function resolves the active profile
    and expands each tenant's ``variants.<precision>`` table up onto the tenant,
    so what we receive already carries a concrete ``model`` per tenant.  This
    never raises and never blocks.
    """
    global _CONT_CACHE
    with _CONT_LOCK:
        stamp, cached = _CONT_CACHE
        if cached is not None and not refresh and (time.time() - stamp) < _CONT_TTL:
            return cached

    data: Optional[Dict[str, Any]] = None
    if _pipeline_paths is not None:
        fn = getattr(_pipeline_paths, "load_continuum", None)
        if callable(fn):
            try:
                raw = fn()
                if isinstance(raw, dict):
                    data = raw
                elif raw is not None and hasattr(raw, "__dict__"):
                    data = dict(vars(raw))
            except Exception as exc:
                LOG.warn("pipeline_paths.load_continuum() failed: %s" % _short_exc(exc))

    with _CONT_LOCK:
        _CONT_CACHE = (time.time(), data if data is not None else {})
    return data


def _tenant_tables(cont: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Every plausible tenant table in a continuum blob, resolved or raw."""
    tables: List[Dict[str, Any]] = []
    if not isinstance(cont, dict):
        return tables

    for key in ("tenants", "endpoints", "judges"):
        table = cont.get(key)
        if isinstance(table, dict):
            tables.append(table)

    # Unresolved file layout: profiles.<name>.tenants
    profiles = cont.get("profiles")
    if isinstance(profiles, dict):
        active = (
            cont.get("active_profile")
            or _dig(cont, ("continuum", "default_profile"))
            or cont.get("profile")
        )
        names = [active] if active in profiles else list(profiles.keys())[:1]
        for name in names:
            tenants = _dig(profiles, (str(name), "tenants"))
            if isinstance(tenants, dict):
                tables.append(tenants)
    return tables


def _resolve_tenant_model(tenant: Dict[str, Any]) -> str:
    """The concrete model id for a tenant, expanding ``variants`` if needed."""
    model = tenant.get("model") or tenant.get("hf_model")
    if model:
        return str(model)
    variants = tenant.get("variants")
    precision = str(tenant.get("precision") or "").strip()
    if isinstance(variants, dict):
        chosen = variants.get(precision) if precision else None
        if not isinstance(chosen, dict) and variants:
            chosen = next(
                (v for v in variants.values() if isinstance(v, dict)), None
            )
        if isinstance(chosen, dict) and chosen.get("model"):
            return str(chosen["model"])
    return ""


def _endpoints_from_continuum(cont: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Extract endpoint overrides from a continuum blob, shape-agnostically.

    Only the three served names in the fixed roster are honoured, and any banned
    model id causes the entry to be dropped in favour of the built-in default.
    """
    found: Dict[str, Dict[str, Any]] = {}

    for table in _tenant_tables(cont):
        for name, tenant in table.items():
            if not isinstance(tenant, dict):
                continue
            served = str(
                tenant.get("served_name")
                or tenant.get("served_model_name")
                or TENANT_TO_SERVED.get(str(name).lower())
                or ""
            ).strip()
            if served not in DEFAULT_ENDPOINTS:
                continue
            if str(tenant.get("kind") or "vllm").lower() not in ("vllm", "openai", "http"):
                continue

            model_id = _resolve_tenant_model(tenant)
            if model_id and model_id.lower() in BANNED_MODEL_IDS:
                LOG.warn(
                    "continuum tenant %r declares banned model %r; ignoring the "
                    "tenant and using the built-in roster default" % (name, model_id)
                )
                continue

            entry: Dict[str, Any] = {"model": served}
            if model_id:
                entry["hf_model"] = model_id

            remote = bool(tenant.get("remote"))
            base_url = (
                tenant.get("base_url") or tenant.get("url") or tenant.get("endpoint")
            )
            if remote and tenant.get("remote_base_url"):
                base_url = tenant["remote_base_url"]
            if base_url:
                entry["base_url"] = _normalise_base_url(str(base_url))
            elif tenant.get("port"):
                host = str(tenant.get("host") or "127.0.0.1")
                scheme = str(tenant.get("scheme") or "http")
                entry["base_url"] = "%s://%s:%s/v1" % (scheme, host, tenant["port"])
            entry["remote"] = remote

            if "enabled" in tenant:
                entry["enabled"] = bool(tenant["enabled"])
            if "api_key" in tenant:
                entry["api_key"] = str(tenant.get("api_key") or "")

            vision = _tenant_vision(tenant)
            if vision is not None:
                entry["vision"] = vision

            found[served] = entry

    # pipeline_paths.load_continuum() flattens the resolved governor URL to a
    # top-level key; that is the authoritative local-or-remote decision.
    flat_gov = cont.get("governor_base_url") if isinstance(cont, dict) else None
    if isinstance(flat_gov, str) and flat_gov.strip():
        entry = found.setdefault(GOVERNOR, {"model": "governor"})
        entry["base_url"] = _normalise_base_url(flat_gov)

    # Unresolved file layout: a top-level [governor] table with
    # remote_base_url / local_base_url / served_name.
    gov = cont.get("governor") if isinstance(cont.get("governor"), dict) else None
    if gov:
        entry = found.setdefault(GOVERNOR, {"model": "governor"})
        remote = bool(gov.get("remote", entry.get("remote")))
        url = gov.get("remote_base_url") if remote else gov.get("local_base_url")
        url = url or gov.get("base_url")
        if url and "base_url" not in entry:
            entry["base_url"] = _normalise_base_url(str(url))
        if gov.get("served_name"):
            entry["model"] = str(gov["served_name"])
    return found


def _tenant_vision(tenant: Dict[str, Any]) -> Optional[bool]:
    """Whether a tenant accepts image content parts, if it says so."""
    if "vision" in tenant:
        return bool(tenant["vision"])
    if "multimodal" in tenant:
        return bool(tenant["multimodal"])
    limits = tenant.get("limit_mm_per_prompt")
    if isinstance(limits, dict) and "image" in limits:
        num = _num(limits.get("image"))
        return bool(num and num > 0)
    return None


def _normalise_base_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if url.endswith("/chat/completions"):
        url = url[: -len("/chat/completions")]
    if not url.endswith("/v1"):
        url = url + "/v1"
    return url


#: Continuum verdict-weight names -> legacy jury.go seats.
_WEIGHT_ALIAS = {
    "harmony": "pixtral",
    "aesthetic": "pixtral",
    "pixtral": "pixtral",
    "structure": "qwen",
    "structural": "qwen",
    "witness": "qwen",
    "qwen": "qwen",
    "feature_decoder": "decoder",
    "decoder": "decoder",
    "semantic_fidelity": "governor",
    "semantic": "governor",
    "synthesis": "governor",
    "governor": "governor",
}


def _seat_table(raw: Any) -> Dict[str, float]:
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, float] = {}
    for key, value in raw.items():
        seat = _WEIGHT_ALIAS.get(str(key).strip().lower())
        num = _num(value)
        if seat and num is not None and num >= 0:
            out[seat] = float(num)
    return out


DEFAULT_TIMEOUTS = {
    "probe": 2.0,  # cheap liveness GET /v1/models
    "judge": 25.0,  # per-judge socket timeout
    "total": 30.0,  # wall clock for one frame
    "phase1_fraction": 0.70,  # share of the budget the visual wave may consume
    "gates": 10.0,  # bounded wait on sensory_gates (first call warms models)
}

DEFAULT_TIERS = {
    "masterpiece_percentile": 98.0,
    "spectacle_percentile": 90.0,
    "banal_percentile": 40.0,
}


def _truthy(value: str) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _base_runtime() -> Dict[str, Any]:
    """Built-in roster defaults, overlaid with continuum then environment."""
    endpoints: Dict[str, Dict[str, Any]] = {
        key: dict(value) for key, value in DEFAULT_ENDPOINTS.items()
    }

    cont = load_continuum() or {}
    for served, override in _endpoints_from_continuum(cont).items():
        endpoints.setdefault(served, {}).update(override)

    # pipeline_paths.GOVERNOR_BASE_URL is authoritative for the governor: it
    # resolves to localhost when the local tenant is enabled, else to the
    # remote service. Never hardcode either location here.
    gov_url = _pipeline_attr("GOVERNOR_BASE_URL", "governor_base_url")
    if gov_url:
        endpoints[GOVERNOR]["base_url"] = _normalise_base_url(str(gov_url))

    # Environment always wins.
    env_map = {
        VISUAL_WITNESS: ("MOJ_VISUAL_WITNESS_URL", "VISUAL_WITNESS_URL"),
        PIXTRAL_CRITIC: ("MOJ_PIXTRAL_URL", "PIXTRAL_CRITIC_URL"),
        GOVERNOR: ("MOJ_GOVERNOR_URL", "GOVERNOR_BASE_URL"),
    }
    for served, names in env_map.items():
        url = _env(*names)
        if url:
            endpoints[served]["base_url"] = _normalise_base_url(url)

    enable_map = {
        VISUAL_WITNESS: "MOJ_VISUAL_WITNESS_ENABLED",
        PIXTRAL_CRITIC: "MOJ_PIXTRAL_ENABLED",
        GOVERNOR: "MOJ_GOVERNOR_ENABLED",
    }
    for served, name in enable_map.items():
        raw = os.environ.get(name)
        if raw is not None and raw != "":
            endpoints[served]["enabled"] = _truthy(raw)

    if _truthy(_env("ARCANE_GOVERNOR_REMOTE")) and not _env("MOJ_GOVERNOR_URL"):
        remote = _dig(cont, ("governor", "remote_base_url")) or DEFAULT_ENDPOINTS[
            GOVERNOR
        ]["base_url"]
        endpoints[GOVERNOR]["base_url"] = _normalise_base_url(str(remote))
        endpoints[GOVERNOR]["remote"] = True

    shared_key = _env("MOJ_API_KEY", "OPENAI_API_KEY")
    if shared_key:
        for entry in endpoints.values():
            entry.setdefault("api_key", shared_key)
    gov_key = _env("GOVERNOR_API_KEY", "MOJ_GOVERNOR_API_KEY")
    if gov_key:
        endpoints[GOVERNOR]["api_key"] = gov_key

    # Final safety net: never call a banned model, whatever the config said.
    for served, entry in endpoints.items():
        hf = str(entry.get("hf_model") or "").lower()
        if hf and hf in BANNED_MODEL_IDS:
            LOG.error(
                "endpoint %r resolved to banned model %r; disabling it"
                % (served, entry.get("hf_model"))
            )
            entry["enabled"] = False

    timeouts = dict(DEFAULT_TIMEOUTS)
    cont_timeouts = _dig(cont, ("verdict", "timeouts")) or _dig(cont, ("jury", "timeouts"))
    if isinstance(cont_timeouts, dict):
        for key, value in cont_timeouts.items():
            num = _num(value)
            if key in timeouts and num is not None and num > 0:
                timeouts[key] = float(num)
    for key, name in (
        ("probe", "MOJ_PROBE_TIMEOUT_S"),
        ("judge", "MOJ_JUDGE_TIMEOUT_S"),
        ("total", "MOJ_TOTAL_BUDGET_S"),
        ("gates", "MOJ_GATES_TIMEOUT_S"),
    ):
        num = _num(_env(name))
        if num is not None and num > 0:
            timeouts[key] = float(num)

    weights = {seat: 0.0 for seat in LEGACY_SEATS}
    for spec in JUDGES:
        weights[spec.legacy_key] = spec.default_weight
    weights.setdefault("decoder", 0.15)
    weights.update(_seat_table(_dig(cont, ("verdict", "weights"))))
    weights.update(_seat_table(_dig(cont, ("verdict", "jurors", "weights"))))

    strictness = {seat: 1.5 for seat in LEGACY_SEATS}
    for spec in JUDGES:
        strictness[spec.legacy_key] = spec.default_gamma
    strictness.update(_seat_table(_dig(cont, ("verdict", "jurors", "strictness"))))

    tiers = dict(DEFAULT_TIERS)
    cont_tiers = _dig(cont, ("verdict", "tiers"))
    if isinstance(cont_tiers, dict):
        for key in tiers:
            num = _num(cont_tiers.get(key))
            if num is not None:
                tiers[key] = float(num)

    jurors = _dig(cont, ("verdict", "jurors")) or {}
    mode = str(jurors.get("mode") or "parallel")
    order = (
        list(jurors["order"])
        if isinstance(jurors.get("order"), (list, tuple)) and jurors["order"]
        else list(LEGACY_SEATS)
    )
    adversarial = bool(jurors.get("adversarial_mode", True))

    min_judges = _num(_env("MOJ_MIN_JUDGES"))

    return {
        "_moj_runtime": True,
        "endpoints": endpoints,
        "weights": weights,
        "strictness": strictness,
        "mode": mode,
        "order": order,
        "adversarial_mode": adversarial,
        "timeouts": timeouts,
        "tiers": tiers,
        "min_judges": int(min_judges) if min_judges is not None else 1,
        "temperature": 0.15,
        "top_p": 0.9,
        "max_tokens": 640,
        "image_max_side": int(_num(_env("MOJ_IMAGE_MAX_SIDE")) or 1024),
        "image_format": (_env("MOJ_IMAGE_FORMAT") or "png").lower(),
        "image_max_bytes": int(_num(_env("MOJ_IMAGE_MAX_BYTES")) or 12 * 1024 * 1024),
        "probe_ttl": float(_num(_env("MOJ_PROBE_TTL_S")) or 20.0),
        "uniqueness_influence": not _truthy(_env("MOJ_DISABLE_UNIQUENESS")),
        "gate_triage": _truthy(_env("MOJ_GATE_TRIAGE")),
        "text_from_gates": _truthy(_env("MOJ_TEXT_FROM_GATES")),
        "output_dir": output_dir(),
        "json_retry_without_response_format": True,
    }


def load_runtime_config(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Merge a jury_config-shaped dict onto the roster runtime configuration.

    ``cfg`` is whatever ``jury_evaluator.load_active_config()`` produced -- the
    ``jury_config`` row the Go server writes (mode/order/weights/strictness/
    adversarial_mode).  Passing an already-merged runtime config is a no-op.
    """
    if isinstance(cfg, dict) and cfg.get("_moj_runtime"):
        return cfg

    runtime = _base_runtime()
    if not isinstance(cfg, dict):
        return runtime

    if cfg.get("mode"):
        runtime["mode"] = str(cfg["mode"])
    if isinstance(cfg.get("order"), (list, tuple)) and cfg["order"]:
        runtime["order"] = list(cfg["order"])
    if "adversarial_mode" in cfg:
        runtime["adversarial_mode"] = bool(cfg["adversarial_mode"])

    for key in ("weights", "strictness"):
        table = cfg.get(key)
        if isinstance(table, dict):
            for seat, value in table.items():
                num = _num(value)
                if num is not None:
                    runtime[key][str(seat)] = float(num)

    if isinstance(cfg.get("endpoints"), dict):
        for served, override in cfg["endpoints"].items():
            if served in runtime["endpoints"] and isinstance(override, dict):
                runtime["endpoints"][served].update(override)

    if isinstance(cfg.get("timeouts"), dict):
        for key, value in cfg["timeouts"].items():
            num = _num(value)
            if key in runtime["timeouts"] and num is not None and num > 0:
                runtime["timeouts"][key] = float(num)

    for key in ("min_judges", "temperature", "top_p", "max_tokens", "image_max_side"):
        if key in cfg:
            num = _num(cfg[key])
            if num is not None:
                runtime[key] = type(runtime[key])(num)

    for flag in ("uniqueness_influence", "gate_triage", "text_from_gates"):
        if flag in cfg:
            runtime[flag] = bool(cfg[flag])

    return runtime


def endpoint_for(spec: JudgeSpec, runtime: Dict[str, Any]) -> Dict[str, Any]:
    table = runtime.get("endpoints") or {}
    entry = table.get(spec.endpoint)
    if not isinstance(entry, dict):
        entry = dict(DEFAULT_ENDPOINTS.get(spec.endpoint, {}))
    return entry


def judge_enabled(spec: JudgeSpec, runtime: Dict[str, Any]) -> bool:
    entry = endpoint_for(spec, runtime)
    if "enabled" in entry:
        return bool(entry["enabled"])
    return bool(spec.enabled_by_default)


def judge_weight(spec: JudgeSpec, runtime: Dict[str, Any]) -> float:
    """Total configured weight for a judge, including any seats it absorbed."""
    weights = runtime.get("weights") or {}
    total = _num(weights.get(spec.legacy_key))
    total = spec.default_weight if total is None else float(total)
    for seat in spec.extra_legacy_keys:
        extra = _num(weights.get(seat))
        if extra is not None:
            total += float(extra)
    return max(0.0, total)


def judge_gamma(spec: JudgeSpec, runtime: Dict[str, Any]) -> float:
    gamma = _num((runtime.get("strictness") or {}).get(spec.legacy_key))
    return float(gamma) if gamma is not None and gamma > 0 else spec.default_gamma


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

_AVAIL_LOCK = threading.Lock()
_AVAIL_CACHE: Dict[Tuple[str, str], Tuple[float, bool]] = {}


def _headers(entry: Dict[str, Any]) -> Dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "%s/%s" % (EVALUATOR_NAME, EVALUATOR_VERSION),
    }
    api_key = entry.get("api_key")
    if api_key:
        headers["Authorization"] = "Bearer %s" % api_key
    return headers


def _http_get(url: str, headers: Dict[str, str], timeout: float) -> Tuple[int, bytes]:
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return int(getattr(resp, "status", 200) or 200), resp.read()


def _http_post_json(
    url: str, payload: Dict[str, Any], headers: Dict[str, str], timeout: float
) -> Dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    try:
        decoded = json.loads(raw.decode("utf-8", "replace"))
    except Exception as exc:
        raise ValueError("endpoint returned non-JSON body: %r" % (raw[:200],)) from exc
    if not isinstance(decoded, dict):
        raise ValueError("endpoint returned a non-object JSON body")
    return decoded


def _probe(base_url: str, headers: Dict[str, str], timeout: float) -> bool:
    """Cheap liveness check: ``GET {base_url}/models``.  Never raises."""
    try:
        status, raw = _http_get(base_url.rstrip("/") + "/models", headers, timeout)
    except Exception:
        return False
    if status < 200 or status >= 300:
        return False
    try:
        decoded = json.loads(raw.decode("utf-8", "replace"))
        return isinstance(decoded, dict) and "data" in decoded
    except Exception:
        # A 2xx from /v1/models with an odd body is still a live server.
        return True


def judge_available(role: str, cfg: Optional[Dict[str, Any]] = None) -> bool:
    """Memoised liveness probe for one judge role.

    Cheap (``GET /v1/models``, ~2s timeout), TTL-cached per endpoint.  Returns
    ``False`` rather than raising for every failure mode.
    """
    spec = SPEC_BY_ROLE.get(role)
    if spec is None:
        return False
    runtime = load_runtime_config(cfg)
    if not judge_enabled(spec, runtime):
        return False

    entry = endpoint_for(spec, runtime)
    base_url = str(entry.get("base_url") or "")
    if not base_url:
        return False

    ttl = float(runtime.get("probe_ttl") or 20.0)
    key = (spec.endpoint, base_url)
    now = time.time()
    with _AVAIL_LOCK:
        cached = _AVAIL_CACHE.get(key)
        if cached is not None and (now - cached[0]) < ttl:
            return cached[1]

    timeout = float((runtime.get("timeouts") or {}).get("probe") or 2.0)
    alive = _probe(base_url, _headers(entry), timeout)

    with _AVAIL_LOCK:
        _AVAIL_CACHE[key] = (time.time(), alive)
    return alive


def _clear_probe_cache() -> None:
    with _AVAIL_LOCK:
        _AVAIL_CACHE.clear()


def _chat_completion(
    entry: Dict[str, Any],
    messages: List[Dict[str, Any]],
    runtime: Dict[str, Any],
    timeout: float,
) -> str:
    """POST ``/v1/chat/completions`` and return the assistant message content."""
    url = str(entry.get("base_url") or "").rstrip("/") + "/chat/completions"
    payload: Dict[str, Any] = {
        "model": entry.get("model") or "visual-witness",
        "messages": messages,
        "temperature": float(runtime.get("temperature", 0.15)),
        "top_p": float(runtime.get("top_p", 0.9)),
        "max_tokens": int(runtime.get("max_tokens", 640)),
        "stream": False,
        "response_format": {"type": "json_object"},
    }
    headers = _headers(entry)

    try:
        decoded = _http_post_json(url, payload, headers, timeout)
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        # Some builds reject guided-JSON decoding; retry once in free-text mode
        # and lean on the tolerant parser instead.
        if exc.code in (400, 404, 415, 422, 501) and runtime.get(
            "json_retry_without_response_format", True
        ):
            payload.pop("response_format", None)
            decoded = _http_post_json(url, payload, headers, timeout)
        else:
            raise RuntimeError("HTTP %s from %s: %s" % (exc.code, url, body)) from exc

    choices = decoded.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("no choices in completion response")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = (message or {}).get("content") if isinstance(message, dict) else None
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(str(part.get("text") or ""))
            elif isinstance(part, str):
                parts.append(part)
        content = "\n".join(parts)
    if not isinstance(content, str) or not content.strip():
        raise ValueError("completion contained no assistant text")
    return content


# --------------------------------------------------------------------------
# Image encoding
# --------------------------------------------------------------------------


def _mime_for(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(ext, "image/png")


def encode_image_data_uri(
    path: str, max_side: int = 1024, fmt: str = "png", max_bytes: int = 12 * 1024 * 1024
) -> Tuple[Optional[str], Optional[str]]:
    """Read a render and return ``(data_uri, error)``.

    Downscales with Pillow when the render exceeds ``max_side``; falls back to
    shipping the file bytes verbatim when Pillow is unavailable.
    """
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except Exception as exc:
        return None, "cannot read render: %s" % _short_exc(exc)
    if not raw:
        return None, "render file is empty"

    mime = _mime_for(path)
    if _PILImage is not None:
        try:
            with _PILImage.open(io.BytesIO(raw)) as img:
                width, height = img.size
                if max_side > 0 and max(width, height) > max_side:
                    scale = float(max_side) / float(max(width, height))
                    size = (max(1, int(width * scale)), max(1, int(height * scale)))
                    resample = getattr(_PILImage, "LANCZOS", 1)
                    shrunk = img.convert("RGB").resize(size, resample)
                    buf = io.BytesIO()
                    if fmt in ("jpg", "jpeg"):
                        shrunk.save(buf, "JPEG", quality=90, optimize=True)
                        mime = "image/jpeg"
                    else:
                        shrunk.save(buf, "PNG", optimize=True)
                        mime = "image/png"
                    raw = buf.getvalue()
        except Exception as exc:
            LOG.warn("Pillow re-encode failed, shipping original bytes: %s" % _short_exc(exc))

    if len(raw) > max_bytes:
        return None, "encoded render is %d bytes, over the %d byte ceiling" % (
            len(raw),
            max_bytes,
        )
    return "data:%s;base64,%s" % (mime, base64.b64encode(raw).decode("ascii")), None


# --------------------------------------------------------------------------
# Tolerant JSON scorecard parsing
# --------------------------------------------------------------------------

_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.S)
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _balanced_objects(text: str) -> List[str]:
    """Every top-level ``{...}`` span in ``text``, string/escape aware."""
    found: List[str] = []
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    found.append(text[start : index + 1])
                    start = -1
    return found


def extract_json(text: str) -> Optional[Dict[str, Any]]:
    """Pull a JSON object out of model output that may be fenced or prose-wrapped."""
    if not text:
        return None
    stripped = text.strip()

    candidates: List[str] = [stripped]
    candidates.extend(m.group(1).strip() for m in _FENCE_RE.finditer(stripped))
    blobs = _balanced_objects(stripped)
    blobs.sort(key=len, reverse=True)
    candidates.extend(blobs)

    for candidate in candidates:
        if not candidate:
            continue
        for variant in (candidate, _TRAILING_COMMA_RE.sub(r"\1", candidate)):
            try:
                decoded = json.loads(variant)
            except Exception:
                continue
            if isinstance(decoded, dict):
                return decoded
    return None


def _num(value: Any) -> Optional[float]:
    """Coerce ints, floats, ``"82"``, ``"82/100"``, ``{"score": 82}`` to a float."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        num = float(value)
        if num != num or num in (float("inf"), float("-inf")):
            return None
        return num
    if isinstance(value, dict):
        for key in ("score", "value", "rating", "overall"):
            if key in value:
                return _num(value[key])
        return None
    if isinstance(value, str):
        match = _NUM_RE.search(value)
        if match:
            try:
                return float(match.group(0))
            except Exception:
                return None
    return None


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


_OVERALL_KEYS = (
    "overall",
    "overall_score",
    "score",
    "total",
    "composite",
    "final_score",
    "final",
    "verdict_score",
)
_CRITIQUE_KEYS = (
    "critique",
    "one_sentence_critique",
    "comment",
    "summary",
    "verdict",
    "rationale",
    "justification",
    "reason",
    "notes",
)


def coerce_scorecard(
    obj: Dict[str, Any], spec: JudgeSpec
) -> Tuple[Optional[float], Dict[str, float], str, Dict[str, Any], Optional[str]]:
    """Normalise a model scorecard.

    Returns ``(score, subscores, critique, observations, error)``.  ``error`` is
    non-None when the scorecard carried no usable number -- which degrades the
    judge rather than substituting anything.
    """
    if not isinstance(obj, dict):
        return None, {}, "", {}, "scorecard was not a JSON object"

    lower = {str(k).strip().lower(): v for k, v in obj.items()}
    nested = None
    for key in ("subscores", "scores", "dimensions", "criteria"):
        if isinstance(lower.get(key), dict):
            nested = {str(k).strip().lower(): v for k, v in lower[key].items()}
            break

    # Explicit scale declaration (e.g. a model that insists on 0-10).
    scale = _num(lower.get("scale")) or _num(lower.get("max_score"))
    factor = 1.0
    if scale and scale > 0 and abs(scale - 100.0) > 0.5:
        factor = 100.0 / float(scale)

    subscores: Dict[str, float] = {}
    for name in spec.subscores:
        value = lower.get(name)
        if value is None and nested is not None:
            value = nested.get(name)
        num = _num(value)
        if num is not None:
            subscores[name] = round(_clamp(num * factor), 1)

    if nested:
        for name, value in nested.items():
            if name in subscores or name in _OVERALL_KEYS or name in _CRITIQUE_KEYS:
                continue
            num = _num(value)
            if num is not None:
                subscores[name] = round(_clamp(num * factor), 1)

    overall = None
    for key in _OVERALL_KEYS:
        if key in lower:
            overall = _num(lower[key])
            if overall is not None:
                overall = _clamp(overall * factor)
                break
    if overall is None and subscores:
        overall = _clamp(sum(subscores.values()) / float(len(subscores)))

    critique = ""
    for key in _CRITIQUE_KEYS:
        value = lower.get(key)
        if isinstance(value, str) and value.strip():
            critique = value.strip()
            break
        if isinstance(value, (list, tuple)) and value:
            critique = " ".join(str(x) for x in value).strip()
            break

    observations: Dict[str, Any] = {}
    for key in spec.evidence_keys:
        if key in lower and lower[key] not in (None, "", [], {}):
            observations[key] = _jsonable(lower[key])

    if overall is None:
        detail = ", ".join(sorted(lower.keys()))[:200]
        return (
            None,
            subscores,
            critique,
            observations,
            "scorecard carried no usable numeric score (keys: %s)" % detail,
        )
    return round(float(overall), 1), subscores, critique, observations, None


# --------------------------------------------------------------------------
# Judges
# --------------------------------------------------------------------------


def _blank_judge(spec: JudgeSpec, entry: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "role": spec.role,
        "title": spec.title,
        "model": str(entry.get("model") or spec.endpoint),
        "hf_model": str(entry.get("hf_model") or ""),
        "endpoint": str(entry.get("base_url") or ""),
        "legacy_key": spec.legacy_key,
        "score_key": spec.score_key,
        "phase": spec.phase,
        "vision": bool(entry.get("vision", True)),
        "score": None,
        "subscores": {},
        "observations": {},
        "critique": "",
        "degraded": True,
        "error": None,
        "latency_ms": 0.0,
    }


def _degraded(
    spec: JudgeSpec, runtime: Dict[str, Any], error: str, latency_ms: float = 0.0
) -> Dict[str, Any]:
    entry = endpoint_for(spec, runtime)
    judge = _blank_judge(spec, entry)
    judge["error"] = error
    judge["latency_ms"] = round(float(latency_ms), 1)
    LOG.degraded("judge %s [%s]" % (spec.role, judge["model"]), error)
    return judge


def _user_content_vision(
    prompt: str, job: Optional[Dict[str, Any]], data_uri: str
) -> List[Dict[str, Any]]:
    job = job or {}
    meta_bits = []
    for key in ("id", "seed", "study_type", "steps", "guidance", "width", "height", "adapter"):
        value = job.get(key)
        if value not in (None, ""):
            meta_bits.append("%s=%s" % (key, value))
    text = (
        "COMMISSIONING PROMPT\n"
        "--------------------\n"
        "%s\n\n"
        "RENDER METADATA\n"
        "---------------\n"
        "%s\n\n"
        "Inspect the attached render against your rubric and return your JSON "
        "scorecard now."
        % (prompt or "(no prompt recorded)", ", ".join(meta_bits) or "(none)")
    )
    return [
        {"type": "text", "text": text},
        {"type": "image_url", "image_url": {"url": data_uri}},
    ]


def _user_text_synthesis(prompt: str, evidence: Dict[str, Any]) -> str:
    return (
        "COMMISSIONING PROMPT\n"
        "--------------------\n"
        "%s\n\n"
        "SWORN TESTIMONY AND MECHANICAL EVIDENCE (JSON)\n"
        "----------------------------------------------\n"
        "%s\n\n"
        "Adjudicate on this evidence alone and return your JSON scorecard now."
        % (
            prompt or "(no prompt recorded)",
            json.dumps(_jsonable(evidence), indent=2, sort_keys=True)[:12000],
        )
    )


def judge_image(
    image_path: str,
    prompt: str,
    role: str,
    cfg: Optional[Dict[str, Any]] = None,
    job: Optional[Dict[str, Any]] = None,
    evidence: Optional[Dict[str, Any]] = None,
    arcane: bool = False,
) -> Dict[str, Any]:
    """Run one judge over one render.

    Returns::

        {"role": str, "model": str, "score": float|None, "subscores": {...},
         "critique": str, "degraded": bool, "error": str|None,
         "latency_ms": float, "observations": {...}, ...}

    ``degraded is True`` means the number is ABSENT, not low.  A degraded judge
    is excluded from the composite by :func:`evaluate`; it never contributes a
    substitute value.
    """
    started = time.time()
    runtime = load_runtime_config(cfg)
    spec = SPEC_BY_ROLE.get(role)
    if spec is None:
        LOG.error("unknown judge role %r requested" % (role,))
        return {
            "role": role,
            "title": "unknown",
            "model": "unknown",
            "hf_model": "",
            "endpoint": "",
            "legacy_key": "",
            "score_key": "",
            "phase": 0,
            "vision": False,
            "score": None,
            "subscores": {},
            "observations": {},
            "critique": "",
            "degraded": True,
            "error": "unknown judge role %r" % (role,),
            "latency_ms": 0.0,
        }

    entry = endpoint_for(spec, runtime)
    force_text = bool(runtime.get("text_from_gates"))
    endpoint_sees_images = bool(entry.get("vision", True)) and not force_text

    if not judge_enabled(spec, runtime):
        return _degraded(spec, runtime, "endpoint %r is disabled" % spec.endpoint)
    if not entry.get("base_url"):
        return _degraded(spec, runtime, "endpoint %r has no base_url" % spec.endpoint)

    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": system_prompt_for(spec.role, arcane=arcane)}
    ]

    sent_user = False
    if endpoint_sees_images and image_path and os.path.exists(image_path):
        data_uri, err = encode_image_data_uri(
            image_path,
            max_side=int(runtime.get("image_max_side", 1024)),
            fmt=str(runtime.get("image_format", "png")),
            max_bytes=int(runtime.get("image_max_bytes", 12 * 1024 * 1024)),
        )
        if err or not data_uri:
            if spec.phase == 1 and not evidence:
                return _degraded(spec, runtime, err or "image encoding failed")
            endpoint_sees_images = False
        else:
            content = _user_content_vision(prompt, job, data_uri)
            if evidence:
                content.insert(
                    0, {"type": "text", "text": _user_text_synthesis(prompt, evidence)}
                )
            messages.append({"role": "user", "content": content})
            sent_user = True

    if not sent_user:
        # Text-only path: score uniqueness + sensory-gate testimony (and any
        # surviving visual scorecards) when the seat cannot accept image_url.
        if not evidence:
            return _degraded(
                spec,
                runtime,
                "seat cannot see images and no mechanical evidence was supplied",
            )
        messages.append({"role": "user", "content": _user_text_synthesis(prompt, evidence)})

    if not judge_available(spec.role, runtime):
        return _degraded(
            spec,
            runtime,
            "endpoint unreachable at %s (liveness probe failed)" % entry.get("base_url"),
            (time.time() - started) * 1000.0,
        )

    timeout = float((runtime.get("timeouts") or {}).get("judge") or 25.0)
    try:
        text = _chat_completion(entry, messages, runtime, timeout)
    except Exception as exc:
        return _degraded(
            spec,
            runtime,
            "completion failed: %s" % _short_exc(exc),
            (time.time() - started) * 1000.0,
        )

    latency_ms = (time.time() - started) * 1000.0
    scorecard = extract_json(text)
    if scorecard is None:
        return _degraded(
            spec,
            runtime,
            "judge returned no parseable JSON scorecard: %r" % (text.strip()[:200],),
            latency_ms,
        )

    score, subscores, critique, observations, err = coerce_scorecard(scorecard, spec)
    if err or score is None:
        return _degraded(spec, runtime, err or "no score in scorecard", latency_ms)

    judge = _blank_judge(spec, entry)
    judge.update(
        {
            "score": score,
            "subscores": subscores,
            "observations": observations,
            "critique": critique or "(judge returned no critique)",
            "degraded": False,
            "error": None,
            "latency_ms": round(latency_ms, 1),
            "vision": endpoint_sees_images,
        }
    )
    LOG.info(
        "judge %-10s [%-14s] score=%5.1f  %5.0fms  %s"
        % (spec.role, judge["model"], score, latency_ms, (critique or "")[:80])
    )
    return judge


def _short_exc(exc: BaseException) -> str:
    return ("%s: %s" % (type(exc).__name__, exc))[:300]


# --------------------------------------------------------------------------
# Preserved scoring maths (identical behaviour, now fed by REAL scores)
# --------------------------------------------------------------------------


def calibrate_raw_score(
    raw_score: float, gamma: float, is_adversarial: bool = False
) -> float:
    """Gamma strictness curve.  Unchanged from the original evaluator."""
    normalized = max(0.0, min(100.0, float(raw_score))) / 100.0
    calibrated = 100.0 * (normalized ** float(gamma))
    if is_adversarial and raw_score < 85.0:
        calibrated *= 0.92
    return round(max(0.0, min(100.0, calibrated)), 1)


def compute_percentile_and_curved_score(
    raw_composite: float, db_path: Optional[str] = None
) -> Tuple[float, float]:
    """Empirical-CDF percentile rank over the rolling 300-frame history.

    Behaviour preserved verbatim from ``jury_evaluator``; the only change is
    that the history it curves over now contains real jury scores.
    """
    history: List[float] = []
    con = None
    try:
        con = sqlite3.connect(db_path or sqlite_path())
        cur = con.cursor()
        cur.execute(
            "SELECT raw_score FROM jury_verdicts WHERE raw_score IS NOT NULL "
            "ORDER BY created_at DESC LIMIT 300"
        )
        history = [r[0] for r in cur.fetchall() if r[0] is not None]
    except Exception:
        pass
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:
                pass

    if len(history) < 10:
        # Cold-start fallback: map linear
        pct = raw_composite
    else:
        less_equal = sum(1 for x in history if x <= raw_composite)
        pct = (less_equal / float(len(history))) * 100.0
        pct = max(1.0, min(99.9, pct))

    # Standardized Piecewise Percentile Curve
    if pct >= 98.0:
        curved = 98.0 + ((pct - 98.0) / 2.0) * 2.0
    elif pct >= 90.0:
        curved = 90.0 + ((pct - 90.0) / 8.0) * 7.9
    elif pct >= 70.0:
        curved = 80.0 + ((pct - 70.0) / 20.0) * 9.9
    elif pct >= 35.0:
        curved = 65.0 + ((pct - 35.0) / 35.0) * 14.9
    else:
        curved = (pct / 35.0) * 64.9

    return round(pct, 1), round(curved, 1)


def _safe_mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except Exception:
        return 0.0


def find_image_for_job(
    job: Dict[str, Any], out_dir: Optional[str] = None
) -> Tuple[str, str]:
    """Locate the settled render for a job.

    Returns ``(path, source)``.  ``"latest_png"`` means we fell back to the
    newest file in the output directory -- kept for parity with the original
    evaluator, but now surfaced in the receipt so a mismatched judgement is
    visible rather than silent.
    """
    root = out_dir or output_dir()
    jid = str(job.get("id") or "")
    seed = str(job.get("seed") or "")

    for key in ("output", "image", "path", "file"):
        candidate = job.get(key)
        if not candidate:
            continue
        candidate = str(candidate)
        if os.path.isdir(candidate):
            pngs = sorted(glob.glob(os.path.join(candidate, "*.png")), key=_safe_mtime)
            if pngs:
                return pngs[-1], "job.%s(dir)" % key
        elif os.path.exists(candidate):
            return candidate, "job.%s" % key

    if jid:
        matches = sorted(glob.glob(os.path.join(root, "*%s*.png" % jid)), key=_safe_mtime)
        if matches:
            return matches[-1], "glob.job_id"
    if seed:
        matches = sorted(
            glob.glob(os.path.join(root, "*seed-%s*.png" % seed)), key=_safe_mtime
        )
        if matches:
            return matches[-1], "glob.seed"

    all_pngs = sorted(glob.glob(os.path.join(root, "*.png")), key=_safe_mtime)
    if all_pngs:
        return all_pngs[-1], "latest_png"
    return "", "none"


# --------------------------------------------------------------------------
# Evidence gathering (real pixel work + optional sibling modules)
# --------------------------------------------------------------------------


def _jsonable(obj: Any, _depth: int = 0) -> Any:
    """Recursively coerce numpy/set/tuple values into JSON-serialisable ones."""
    if _depth > 8:
        return str(obj)
    if obj is None or isinstance(obj, (bool, int, str)):
        return obj
    if isinstance(obj, float):
        return None if obj != obj else obj
    if isinstance(obj, dict):
        return {str(k): _jsonable(v, _depth + 1) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [_jsonable(v, _depth + 1) for v in obj]
    if _np is not None:
        try:
            if isinstance(obj, _np.generic):
                return _jsonable(obj.item(), _depth + 1)
            if isinstance(obj, _np.ndarray):
                return _jsonable(obj.tolist(), _depth + 1)
        except Exception:
            pass
    for attr in ("_asdict", "to_dict"):
        fn = getattr(obj, attr, None)
        if callable(fn):
            try:
                return _jsonable(fn(), _depth + 1)
            except Exception:
                pass
    if hasattr(obj, "__dict__"):
        try:
            return _jsonable(dict(vars(obj)), _depth + 1)
        except Exception:
            pass
    return str(obj)


def _invoke_flexible(fn: Any, lookup: Dict[str, Any], positional: List[Any]) -> Any:
    """Call ``fn`` without knowing its exact signature.

    Sibling modules are owned by other agents; rather than hard-coding a guess
    we bind by parameter name where we can and fall back to positional order.
    """
    import inspect

    try:
        sig = inspect.signature(fn)
    except Exception:
        return fn(*positional)

    kwargs: Dict[str, Any] = {}
    missing: List[str] = []
    for name, param in sig.parameters.items():
        if param.kind in (param.VAR_KEYWORD, param.VAR_POSITIONAL):
            continue
        if name in lookup:
            kwargs[name] = lookup[name]
        elif param.default is param.empty and param.kind != param.KEYWORD_ONLY:
            missing.append(name)

    if missing or any(p.kind == p.POSITIONAL_ONLY for p in sig.parameters.values()):
        try:
            return fn(*positional)
        except TypeError:
            pass
    try:
        return fn(**kwargs)
    except TypeError:
        return fn(*positional)


def _bounded(fn: Any, timeout: float, label: str) -> Tuple[Any, Optional[str]]:
    """Run ``fn()`` in a worker thread with a wall-clock ceiling.

    ``sensory_gates.gate_scores()`` warms DINOv2/SigLIP on its first call, which
    can take far longer than one frame's budget; the straggler keeps running so
    the next frame gets a warm cache, but it cannot stall the jury.
    """
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(fn)
        try:
            return future.result(timeout=timeout), None
        except FutureTimeoutError:
            return None, "%s exceeded its %.1fs budget (still warming in background)" % (
                label,
                timeout,
            )
        except Exception as exc:
            return None, "%s raised: %s" % (label, _short_exc(exc))
    finally:
        try:
            executor.shutdown(wait=False, cancel_futures=False)
        except TypeError:  # pragma: no cover
            executor.shutdown(wait=False)


UNIQUENESS_UNAVAILABLE = {
    "score": None,
    "category": "UNAVAILABLE",
    "min_distance": None,
    "mean_distance": None,
    "mode_collapse": False,
    "available": False,
}


def uniqueness_block(job_id: str, image_path: str) -> Dict[str, Any]:
    """Real 128-d perceptual fingerprint work via ``uniqueness_tracker``."""
    if _uniqueness_tracker is None:
        block = dict(UNIQUENESS_UNAVAILABLE)
        block["error"] = "uniqueness_tracker unavailable (needs numpy + Pillow)"
        return block
    if not image_path or not os.path.exists(image_path):
        block = dict(UNIQUENESS_UNAVAILABLE)
        block["error"] = "no render on disk to fingerprint"
        return block

    _align_uniqueness_db()
    try:
        data = _uniqueness_tracker.evaluate_uniqueness(job_id, image_path)
    except Exception as exc:
        block = dict(UNIQUENESS_UNAVAILABLE)
        block["error"] = "uniqueness_tracker raised: %s" % _short_exc(exc)
        LOG.warn("uniqueness evaluation failed: %s" % block["error"])
        return block

    data = data if isinstance(data, dict) else {}
    return {
        "score": _num(data.get("uniqueness_score")),
        "category": str(data.get("category") or "UNKNOWN"),
        "min_distance": _num(data.get("min_distance")),
        "mean_distance": _num(data.get("mean_distance")),
        "mode_collapse": bool(data.get("mode_collapse")),
        "available": True,
    }


def sensory_gate_block(
    image_path: str, job: Dict[str, Any], prompt: str, timeout: float = 10.0
) -> Optional[Dict[str, Any]]:
    """``sensory_gates.gate_scores()`` when that module exists, else ``None``."""
    if _sensory_gates is None:
        return None
    fn = getattr(_sensory_gates, "gate_scores", None)
    if not callable(fn):
        return None
    lookup = {
        "image_path": image_path,
        "img_path": image_path,
        "path": image_path,
        "image": image_path,
        "job": job,
        "prompt": prompt,
    }
    result, err = _bounded(
        lambda: _invoke_flexible(fn, lookup, [image_path, prompt]),
        timeout,
        "sensory_gates.gate_scores",
    )
    if err:
        LOG.warn(err)
        return {"available": False, "degraded": True, "error": err}
    block = _jsonable(result)
    if isinstance(block, dict):
        block.setdefault("available", True)
        # NOTE: sensory_gates.gate_scores() renders its own arcane_log gate
        # block before returning, so calling LOG.gates(block) here would print
        # the whole disclosure panel twice. The compact GATES line inside
        # log.verdict() is the second, deliberate view of it.
        return block
    return {"available": True, "value": block}


def arcane_block(
    image_path: str, job: Dict[str, Any], prompt: str, timeout: float = 10.0
) -> Optional[Dict[str, Any]]:
    """``arcane_aesthetic.conformance()`` when that module exists, else ``None``."""
    if _arcane_aesthetic is None:
        return None
    fn = getattr(_arcane_aesthetic, "conformance", None)
    if not callable(fn):
        return None
    lookup = {
        "image_path": image_path,
        "img_path": image_path,
        "path": image_path,
        "image": image_path,
        "job": job,
        "prompt": prompt,
    }
    result, err = _bounded(
        lambda: _invoke_flexible(fn, lookup, [image_path, prompt]),
        timeout,
        "arcane_aesthetic.conformance",
    )
    if err:
        LOG.warn(err)
        return {"available": False, "degraded": True, "error": err}
    block = _jsonable(result)
    if isinstance(block, dict):
        block.setdefault("available", True)
        LOG.fortiche(block)
        return block
    return {"available": True, "value": block}


_ARCANE_HINT_KEYS = ("prompt", "subject", "id", "adapter", "style", "draft", "atlas")


def is_arcane_job(job: Dict[str, Any]) -> bool:
    """Detect an Arcane-lineage job, for FORTICHE rubric splicing."""
    if not isinstance(job, dict):
        return False
    if str(job.get("study_type") or "").strip().lower() == "atlas":
        return True
    if job.get("arcane") or job.get("is_arcane"):
        return True
    for key in _ARCANE_HINT_KEYS:
        if "arcane" in str(job.get(key) or "").lower():
            return True
    return False


# --------------------------------------------------------------------------
# The jury pass
# --------------------------------------------------------------------------


def _uniqueness_adjustment(uniq: Dict[str, Any], enabled: bool) -> float:
    """Bounded, evidence-backed nudge from real pixel novelty.

    Applied to the composite, never to an individual judge's score, and always
    recorded in the receipt so it is auditable.
    """
    if not enabled or not uniq.get("available"):
        return 0.0
    score = _num(uniq.get("score"))
    if score is None:
        return 0.0
    if uniq.get("mode_collapse") or score < 35.0:
        return -8.0
    if score >= 75.0:
        return 3.0
    return 0.0


def _run_phase1(
    specs: List[JudgeSpec],
    image_path: str,
    prompt: str,
    runtime: Dict[str, Any],
    job: Dict[str, Any],
    arcane: bool,
    deadline: float,
    evidence: Optional[Dict[str, Any]] = None,
) -> Dict[str, Dict[str, Any]]:
    results: Dict[str, Dict[str, Any]] = {}
    if not specs:
        return results

    if str(runtime.get("mode") or "parallel").lower() == "sequential":
        for spec in specs:
            if time.time() >= deadline:
                results[spec.role] = _degraded(
                    spec, runtime, "wall-clock budget exhausted before dispatch"
                )
                continue
            try:
                results[spec.role] = judge_image(
                    image_path,
                    prompt,
                    spec.role,
                    runtime,
                    job=job,
                    evidence=evidence,
                    arcane=arcane,
                )
            except Exception as exc:
                results[spec.role] = _degraded(spec, runtime, _short_exc(exc))
        return results

    executor = ThreadPoolExecutor(max_workers=max(1, len(specs)))
    try:
        futures = [
            (
                spec,
                executor.submit(
                    judge_image,
                    image_path,
                    prompt,
                    spec.role,
                    runtime,
                    job,
                    evidence,
                    arcane,
                ),
            )
            for spec in specs
        ]
        for spec, future in futures:
            remaining = max(0.1, deadline - time.time())
            try:
                results[spec.role] = future.result(timeout=remaining)
            except FutureTimeoutError:
                future.cancel()
                results[spec.role] = _degraded(
                    spec,
                    runtime,
                    "judge exceeded the %.1fs wall-clock budget for this frame"
                    % float((runtime.get("timeouts") or {}).get("total") or 30.0),
                )
            except Exception as exc:
                results[spec.role] = _degraded(spec, runtime, _short_exc(exc))
    finally:
        try:
            executor.shutdown(wait=False, cancel_futures=True)
        except TypeError:  # pragma: no cover
            executor.shutdown(wait=False)
    return results


def _run_phase2(
    specs: List[JudgeSpec],
    image_path: str,
    prompt: str,
    runtime: Dict[str, Any],
    evidence: Dict[str, Any],
    have_evidence: bool,
    arcane: bool,
    deadline: float,
) -> Dict[str, Dict[str, Any]]:
    results: Dict[str, Dict[str, Any]] = {}
    for spec in specs:
        entry = endpoint_for(spec, runtime)
        can_see = bool(entry.get("vision", False)) and bool(image_path)
        if not have_evidence and not can_see:
            results[spec.role] = _degraded(
                spec,
                runtime,
                "no surviving visual testimony and this endpoint cannot see the "
                "render; a text-only judge will not be asked to invent a verdict",
            )
            continue
        remaining = deadline - time.time()
        if remaining <= 0.2:
            results[spec.role] = _degraded(
                spec, runtime, "wall-clock budget exhausted before synthesis"
            )
            continue
        executor = ThreadPoolExecutor(max_workers=1)
        try:
            future = executor.submit(
                judge_image,
                image_path if can_see else "",
                prompt,
                spec.role,
                runtime,
                evidence=evidence,
                arcane=arcane,
            )
            try:
                results[spec.role] = future.result(timeout=remaining)
            except FutureTimeoutError:
                future.cancel()
                results[spec.role] = _degraded(
                    spec, runtime, "synthesis exceeded the remaining wall-clock budget"
                )
            except Exception as exc:
                results[spec.role] = _degraded(spec, runtime, _short_exc(exc))
        finally:
            try:
                executor.shutdown(wait=False, cancel_futures=True)
            except TypeError:  # pragma: no cover
                executor.shutdown(wait=False)
    return results


def evaluate(job: Dict[str, Any], cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Full jury pass over one settled render.

    Returns the receipt schema ``jury_evaluator`` writes to ``audit.jsonl`` and
    ``jury.sqlite3`` -- every pre-existing key preserved for
    ``internal/jury/jury.go`` and the ``/jury`` surface -- plus ``judges``,
    ``degraded_judges``, ``arcane`` and ``gates``.

    If no judge survives, ``tier`` is ``"unscored"`` and ``composite`` /
    ``raw_composite`` / ``curved_score`` / ``percentile_rank`` are ``None``.
    No number is ever invented to fill the gap.
    """
    started = time.time()
    job = job if isinstance(job, dict) else {}
    runtime = load_runtime_config(cfg)

    jid = str(job.get("id") or "job-unknown")
    prompt = str(job.get("prompt") or "")
    seed = job.get("seed", 0)
    mode = runtime.get("mode", "parallel")
    order = runtime.get("order", list(LEGACY_SEATS))
    strictness = runtime.get("strictness") or {}
    is_adv = bool(runtime.get("adversarial_mode", True))
    timeouts = runtime.get("timeouts") or {}
    tiers = runtime.get("tiers") or DEFAULT_TIERS
    budget = float(timeouts.get("total") or 30.0)
    phase1_share = float(timeouts.get("phase1_fraction") or 0.70)

    image_path, image_source = find_image_for_job(job, runtime.get("output_dir"))
    if image_source == "latest_png":
        LOG.warn(
            "job %s has no id/seed match on disk; falling back to the newest PNG "
            "(%s) -- receipt records image_source=latest_png"
            % (jid, os.path.basename(image_path))
        )

    arcane_job = is_arcane_job(job)

    # --- evidence -------------------------------------------------------
    uniq = uniqueness_block(jid, image_path)
    gate_budget = float(timeouts.get("gates") or 10.0)
    gates = sensory_gate_block(image_path, job, prompt, gate_budget)
    arcane = arcane_block(image_path, job, prompt, gate_budget)

    # --- judges ---------------------------------------------------------
    active = [s for s in JUDGES if judge_enabled(s, runtime)]
    phase1_specs = [s for s in active if s.phase == 1]
    phase2_specs = [s for s in active if s.phase == 2]

    deadline = started + budget
    phase1_deadline = min(deadline, time.time() + budget * phase1_share)

    # sensory_gates contract: `passed == (not failures)`. `reasons` is `failures`
    # PLUS a standing "DEGRADED: DINOv2 is mandatory..." line on any non-full
    # tier, so deriving the verdict from `reasons` would reject every frame on a
    # dev box. Read `passed` (or `failures`); never `reasons`.
    gate_rejected = bool(
        runtime.get("gate_triage")
        and isinstance(gates, dict)
        and gates.get("available")
        and gates.get("passed") is False
    )

    if gate_rejected:
        gate_failures = gates.get("failures")
        if not isinstance(gate_failures, list) or not gate_failures:
            gate_failures = gates.get("reasons") or []
        reason = "; ".join(str(r) for r in gate_failures[:3])
        results = {
            s.role: _degraded(
                s, runtime, "sensory gate triage rejected the frame: %s" % reason
            )
            for s in phase1_specs
        }
    elif not image_path or not os.path.exists(image_path):
        results = {
            s.role: _degraded(s, runtime, "render not found on disk: %r" % (image_path,))
            for s in phase1_specs
        }
    else:
        mechanical = {
            "prompt": prompt,
            "job": {
                k: job.get(k)
                for k in ("id", "seed", "study_type", "steps", "guidance", "width", "height")
                if job.get(k) is not None
            },
            "uniqueness": uniq,
            "sensory_gates": gates,
            "arcane_conformance": arcane,
            "note": (
                "Mechanical testimony from uniqueness tracking and sensory gates. "
                "This seat may not receive pixels; score the render from this evidence."
            ),
        }
        results = _run_phase1(
            phase1_specs,
            image_path,
            prompt,
            runtime,
            job,
            arcane_job,
            phase1_deadline,
            evidence=mechanical,
        )

    visual_survivors = [
        results[s.role]
        for s in phase1_specs
        if s.role in results and not results[s.role].get("degraded")
    ]

    evidence = {
        "prompt": prompt,
        "job": {
            k: job.get(k)
            for k in ("id", "seed", "study_type", "steps", "guidance", "width", "height")
            if job.get(k) is not None
        },
        "visual_testimony": [
            {
                "role": j["role"],
                "title": j["title"],
                "model": j["model"],
                "score": j["score"],
                "subscores": j["subscores"],
                "observations": j.get("observations") or {},
                "critique": j["critique"],
            }
            for j in visual_survivors
        ],
        "degraded_visual_judges": [
            {"role": results[s.role]["role"], "error": results[s.role]["error"]}
            for s in phase1_specs
            if s.role in results and results[s.role].get("degraded")
        ],
        "uniqueness": uniq,
        "sensory_gates": gates,
        "arcane_conformance": arcane,
        "arcane_job": arcane_job,
    }

    results.update(
        _run_phase2(
            phase2_specs,
            image_path,
            prompt,
            runtime,
            evidence,
            bool(visual_survivors),
            arcane_job,
            deadline,
        )
    )

    judges: List[Dict[str, Any]] = [results[s.role] for s in active if s.role in results]

    # --- weighting ------------------------------------------------------
    for judge in judges:
        spec = SPEC_BY_ROLE.get(judge["role"])
        if spec is None:
            continue
        gamma = judge_gamma(spec, runtime)
        judge["gamma"] = gamma
        judge["seats"] = [spec.legacy_key] + list(spec.extra_legacy_keys)
        judge["weight"] = judge_weight(spec, runtime) if not judge.get("degraded") else 0.0
        judge["calibrated_score"] = (
            calibrate_raw_score(judge["score"], gamma, is_adv)
            if judge.get("score") is not None and not judge.get("degraded")
            else None
        )

    survivors = [j for j in judges if not j.get("degraded")]
    degraded_judges = [j["role"] for j in judges if j.get("degraded")]
    min_judges = max(1, int(runtime.get("min_judges") or 1))

    raw_pre_uniqueness: Optional[float] = None
    uniqueness_adjustment = 0.0
    raw_composite: Optional[float] = None
    percentile_rank: Optional[float] = None
    curved_score: Optional[float] = None
    tier = "unscored"
    unscored_reason: Optional[str] = None

    if len(survivors) < min_judges:
        if gate_rejected:
            unscored_reason = (
                "sensory gate triage rejected the frame before the jury sat; no "
                "jury score exists for it"
            )
        else:
            unscored_reason = (
                "only %d of %d judges answered (quorum is %d); refusing to "
                "fabricate a composite" % (len(survivors), len(judges), min_judges)
            )
        LOG.error(
            "job %s UNSCORED -- %s | degraded: %s"
            % (jid, unscored_reason, ", ".join(degraded_judges) or "none")
        )
    else:
        total_weight = sum(j["weight"] for j in survivors)
        if total_weight <= 0:
            LOG.warn(
                "job %s: surviving judges carry zero configured weight; "
                "renormalising to equal weights" % jid
            )
            for judge in survivors:
                judge["weight"] = 1.0
            total_weight = float(len(survivors))
        weighted = sum(j["calibrated_score"] * j["weight"] for j in survivors)
        raw_pre_uniqueness = round(weighted / total_weight, 1)
        uniqueness_adjustment = _uniqueness_adjustment(
            uniq, bool(runtime.get("uniqueness_influence", True))
        )
        raw_composite = round(_clamp(raw_pre_uniqueness + uniqueness_adjustment), 1)
        percentile_rank, curved_score = compute_percentile_and_curved_score(
            raw_composite, os.path.join(str(runtime.get("output_dir")), "jury.sqlite3")
        )
        tier = "standard"
        if percentile_rank >= float(tiers.get("masterpiece_percentile", 98.0)):
            tier = "masterpiece"
        elif percentile_rank >= float(tiers.get("spectacle_percentile", 90.0)):
            tier = "spectacle"

        if degraded_judges:
            LOG.warn(
                "job %s scored on %d/%d judges, renormalised over weight %.2f; "
                "degraded: %s"
                % (
                    jid,
                    len(survivors),
                    len(judges),
                    total_weight,
                    ", ".join(degraded_judges),
                )
            )

    # --- legacy score block --------------------------------------------
    jury_scores: Dict[str, Any] = {key: None for key in LEGACY_SCORE_KEYS}
    feature_decoder_source = None
    for judge in judges:
        spec = SPEC_BY_ROLE.get(judge["role"])
        if spec is None:
            continue
        value = judge.get("calibrated_score")
        jury_scores[spec.score_key] = value
        for alias in spec.alias_score_keys:
            jury_scores[alias] = value
            if alias == "feature_decoder":
                feature_decoder_source = spec.role
    jury_scores["raw_composite"] = raw_composite
    jury_scores["composite"] = curved_score

    gammas = {
        "%s_gamma" % seat: float(
            _num(strictness.get(seat)) if _num(strictness.get(seat)) is not None else 1.5
        )
        for seat in LEGACY_SEATS
    }
    gammas["inquisitor_mode"] = is_adv

    epigram = None
    for judge in judges:
        obs = judge.get("observations") or {}
        candidate = obs.get("epigram")
        if isinstance(candidate, str) and candidate.strip():
            epigram = candidate.strip()
            break

    receipt: Dict[str, Any] = {
        # ---- keys the Go server, the SQLite writer and /jury already read ----
        "ts": time.time(),
        "job_id": jid,
        "seed": seed,
        "prompt": prompt,
        "mode": mode,
        "order": order,
        "tier": tier,
        "percentile_rank": percentile_rank,
        "curved_score": curved_score,
        "raw_composite": raw_composite,
        "uniqueness": {
            "score": uniq.get("score"),
            "category": uniq.get("category"),
            "min_distance": uniq.get("min_distance"),
            "mean_distance": uniq.get("mean_distance"),
            "mode_collapse": bool(uniq.get("mode_collapse")),
            "available": bool(uniq.get("available")),
        },
        "jury_scores": jury_scores,
        "strictness_multipliers": gammas,
        "is_spectacle": bool(
            percentile_rank is not None
            and percentile_rank >= float(tiers.get("spectacle_percentile", 90.0))
        ),
        "is_masterpiece": bool(
            percentile_rank is not None
            and percentile_rank >= float(tiers.get("masterpiece_percentile", 98.0))
        ),
        # ---- new keys ----
        "judges": judges,
        "degraded_judges": degraded_judges,
        "arcane": arcane,
        "gates": gates,
        # A compact trust summary so nothing downstream has to dig into the gate
        # block to learn that its numbers are provisional or its tier degraded.
        # `adherence` is unavailable on the heuristic tier and is excluded from
        # the gate verdict there rather than faked -- the same rule this module
        # applies to a silent judge -- so a consumer must check `measured`
        # before treating any gate number as measured.
        "gates_summary": (
            {
                "passed": gates.get("passed"),
                "tier": gates.get("tier"),
                "backend": gates.get("backend"),
                "degraded": gates.get("degraded"),
                "calibration": gates.get("calibration"),
                "mandatory_satisfied": gates.get("mandatory_satisfied"),
                "measured": gates.get("measured"),
                "failures": gates.get("failures"),
            }
            if isinstance(gates, dict)
            else None
        ),
        # ---- provenance ----
        "composite": curved_score,
        "unscored": raw_composite is None,
        "unscored_reason": unscored_reason,
        "surviving_judges": [j["role"] for j in survivors],
        "quorum": min_judges,
        "raw_composite_pre_uniqueness": raw_pre_uniqueness,
        "uniqueness_adjustment": uniqueness_adjustment,
        "feature_decoder_source": feature_decoder_source,
        "epigram": epigram,
        "image_path": image_path,
        "image_source": image_source,
        "arcane_job": arcane_job,
        "gate_rejected": gate_rejected,
        "evaluator": EVALUATOR_NAME,
        "evaluator_version": EVALUATOR_VERSION,
        "elapsed_ms": round((time.time() - started) * 1000.0, 1),
    }
    receipt = _jsonable(receipt)

    LOG.verdict(receipt)
    LOG.event(
        "jury.verdict",
        job_id=jid,
        seed=seed,
        tier=receipt["tier"],
        curved_score=receipt["curved_score"],
        raw_composite=receipt["raw_composite"],
        percentile_rank=receipt["percentile_rank"],
        surviving_judges=receipt["surviving_judges"],
        degraded_judges=receipt["degraded_judges"],
        uniqueness=receipt["uniqueness"].get("score"),
        arcane_job=arcane_job,
        unscored=receipt["unscored"],
        elapsed_ms=receipt["elapsed_ms"],
        evaluator_version=EVALUATOR_VERSION,
    )
    return receipt


# --------------------------------------------------------------------------
# CLI helpers
# --------------------------------------------------------------------------


def probe_all(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Report liveness and configuration for every judge in the roster."""
    runtime = load_runtime_config(cfg)
    _clear_probe_cache()
    report: Dict[str, Any] = {
        "logger_backend": LOG.backend,
        "output_dir": runtime.get("output_dir"),
        "continuum": "loaded" if load_continuum() else "unavailable",
        "endpoints": {},
        "judges": {},
    }
    for served, entry in (runtime.get("endpoints") or {}).items():
        report["endpoints"][served] = {
            "base_url": entry.get("base_url"),
            "model": entry.get("model"),
            "hf_model": entry.get("hf_model"),
            "vision": entry.get("vision"),
            "remote": entry.get("remote"),
            "enabled": bool(entry.get("enabled")),
            "alive": bool(
                entry.get("enabled")
                and _probe(
                    str(entry.get("base_url") or ""),
                    _headers(entry),
                    float((runtime.get("timeouts") or {}).get("probe") or 2.0),
                )
            ),
        }
    for spec in JUDGES:
        report["judges"][spec.role] = {
            "title": spec.title,
            "endpoint": spec.endpoint,
            "seats": [spec.legacy_key] + list(spec.extra_legacy_keys),
            "score_keys": [spec.score_key] + list(spec.alias_score_keys),
            "phase": spec.phase,
            "weight": judge_weight(spec, runtime),
            "gamma": judge_gamma(spec, runtime),
            "fortiche": spec.role in FORTICHE_ROLES,
            "enabled": judge_enabled(spec, runtime),
            "available": judge_available(spec.role, runtime),
        }
    return report


def _write_test_png(path: str, size: int = 24) -> None:
    """Emit a tiny valid PNG using only stdlib (no Pillow on the dev box)."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    rows = bytearray()
    for y in range(size):
        rows.append(0)  # filter type 0
        for x in range(size):
            rows.extend(((x * 9) % 256, (y * 7) % 256, ((x + y) * 5) % 256))
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(rows), 9))
        + chunk(b"IEND", b"")
    )
    with open(path, "wb") as handle:
        handle.write(png)


# --------------------------------------------------------------------------
# Offline self-test
# --------------------------------------------------------------------------


def _self_test() -> int:
    import tempfile

    failures: List[str] = []

    def check(label: str, condition: bool, detail: str = "") -> None:
        print(
            "  [%s] %s%s"
            % ("PASS" if condition else "FAIL", label, (" -- " + detail) if detail else "")
        )
        if not condition:
            failures.append(label)

    print("=" * 78)
    print(
        "moj_evaluator %s -- OFFLINE SELF-TEST (no network, no GPU, no /root)"
        % EVALUATOR_VERSION
    )
    print("Pass --serve to run the jury daemon instead.")
    print("=" * 78)

    workdir = tempfile.mkdtemp(prefix="moj-selftest-")
    os.environ["MOJ_OUTPUT_DIR"] = workdir
    output_dir(refresh=True)
    image_path = os.path.join(workdir, "job-selftest-seed-4242.png")
    _write_test_png(image_path)

    print("\n[1] Environment")
    print("  python            : %s" % sys.version.split()[0])
    print("  numpy             : %s" % ("present" if _np is not None else "ABSENT"))
    print("  Pillow            : %s" % ("present" if _PILImage is not None else "ABSENT"))
    print("  uniqueness_tracker: %s" % ("present" if _uniqueness_tracker else "ABSENT"))
    print("  pipeline_paths    : %s" % ("present" if _pipeline_paths else "ABSENT"))
    print("  sensory_gates     : %s" % ("present" if _sensory_gates else "ABSENT"))
    print("  arcane_aesthetic  : %s" % ("present" if _arcane_aesthetic else "ABSENT"))
    print("  arcane_log        : %s" % LOG.backend)
    print("  FORTICHE_RUBRIC   : %s" % ("loaded" if fortiche_rubric() else "unavailable"))
    print("  output_dir        : %s" % output_dir())

    print("\n[2] Roster (three distinct models, three distinct rubrics)")
    runtime = load_runtime_config(None)
    for spec in JUDGES:
        entry = endpoint_for(spec, runtime)
        print(
            "  %-10s phase=%d  %-16s %-40s seats=%s -> %s%s"
            % (
                spec.role,
                spec.phase,
                entry.get("model"),
                entry.get("hf_model"),
                "+".join([spec.legacy_key] + list(spec.extra_legacy_keys)),
                ",".join([spec.score_key] + list(spec.alias_score_keys)),
                "  [FORTICHE]" if spec.role in FORTICHE_ROLES else "",
            )
        )
        print("             %s" % entry.get("base_url"))
    check(
        "no banned model id in the resolved roster",
        not any(
            str(e.get("hf_model", "")).lower() in BANNED_MODEL_IDS
            for e in (runtime.get("endpoints") or {}).values()
        ),
    )
    check("three judges seated", len(JUDGES) == 3)
    check(
        "every legacy weight seat is claimed by some judge",
        set(LEGACY_SEATS)
        == set(
            sum([[s.legacy_key] + list(s.extra_legacy_keys) for s in JUDGES], [])
        ),
    )
    check(
        "every legacy score name is produced",
        set(LEGACY_SCORE_KEYS)
        == set(sum([[s.score_key] + list(s.alias_score_keys) for s in JUDGES], [])),
    )

    print("\n[3] Tolerant scorecard parsing")
    fenced = (
        '```json\n{"anatomy": 81, "geometry": 74, "overall": 78, '
        '"scene_inventory": ["a brass astrolabe", "a stone table"], '
        '"critique": "left hand has six fingers."}\n```'
    )
    prose = (
        "Sure! Here is my assessment.\n"
        '{"palette_cohesion": 88, "lighting_authenticity": 71, "overall": 79,\n'
        ' "critique": "unmotivated magenta rim light",}\n'
        "Let me know if you want more detail."
    )
    check("fenced JSON parsed", extract_json(fenced) is not None)
    check("prose-wrapped JSON with trailing comma parsed", extract_json(prose) is not None)
    check(
        "refusal text yields no scorecard",
        extract_json("I'm sorry, I can't help with that.") is None,
    )
    score, subs, critique, obs, err = coerce_scorecard(
        extract_json(fenced) or {}, JUDGE_STRUCTURE
    )
    check("scorecard coerced to a real number", score == 78.0 and err is None, "score=%s" % score)
    check("subscores survived coercion", subs.get("anatomy") == 81.0)
    check("non-scored evidence captured", bool(obs.get("scene_inventory")))

    print("\n[4] Degraded path (all three endpoints unreachable)")
    _clear_probe_cache()
    job = {
        "id": "job-selftest",
        "seed": 4242,
        "prompt": "an obsidian astrolabe under bioluminescent mist, sumi-e",
        "status": "done",
        "output": image_path,
        "study_type": "atlas",
    }
    cfg = {
        "mode": "parallel",
        "order": list(LEGACY_SEATS),
        "weights": {"pixtral": 0.35, "qwen": 0.35, "decoder": 0.15, "governor": 0.15},
        "strictness": {"pixtral": 2.0, "qwen": 1.2, "decoder": 1.5, "governor": 2.2},
        "adversarial_mode": True,
        "timeouts": {"probe": 0.35, "judge": 1.0, "total": 6.0, "gates": 1.0},
        "endpoints": {
            VISUAL_WITNESS: {"base_url": "http://127.0.0.1:59371/v1"},
            PIXTRAL_CRITIC: {"base_url": "http://127.0.0.1:59372/v1"},
            GOVERNOR: {"base_url": "http://127.0.0.1:59373/v1"},
        },
    }
    receipt = evaluate(job, cfg)
    print(
        "\n  receipt: tier=%r composite=%r raw_composite=%r percentile_rank=%r"
        % (
            receipt["tier"],
            receipt["jury_scores"]["composite"],
            receipt["raw_composite"],
            receipt["percentile_rank"],
        )
    )
    print("  degraded_judges: %s" % (receipt["degraded_judges"],))
    print("  jury_scores    : %s" % json.dumps(receipt["jury_scores"]))
    print("  unscored_reason: %s" % receipt["unscored_reason"])
    print("  arcane_job     : %s (study_type=atlas)" % receipt["arcane_job"])
    for judge in receipt["judges"]:
        print(
            "    - %-10s degraded=%-5s score=%-6s %s"
            % (judge["role"], judge["degraded"], judge["score"], (judge["error"] or "")[:70])
        )

    check("tier is 'unscored'", receipt["tier"] == "unscored")
    check("composite is None", receipt["jury_scores"]["composite"] is None)
    check("raw_composite is None", receipt["raw_composite"] is None)
    check("percentile_rank is None", receipt["percentile_rank"] is None)
    check("curved_score is None", receipt["curved_score"] is None)
    check(
        "no legacy score was fabricated",
        all(receipt["jury_scores"][k] is None for k in LEGACY_SCORE_KEYS),
    )
    check(
        "every judge is marked degraded",
        len(receipt["degraded_judges"]) == len(receipt["judges"]) == 3,
    )
    check("every degraded judge carries an error", all(j["error"] for j in receipt["judges"]))
    check("is_spectacle False", receipt["is_spectacle"] is False)
    check("is_masterpiece False", receipt["is_masterpiece"] is False)
    check("receipt is JSON serialisable", bool(json.dumps(receipt)))
    check(
        "legacy receipt keys all present",
        all(
            k in receipt
            for k in (
                "ts", "job_id", "seed", "prompt", "mode", "order", "tier",
                "percentile_rank", "curved_score", "raw_composite", "uniqueness",
                "jury_scores", "strictness_multipliers", "is_spectacle",
                "is_masterpiece",
            )
        ),
    )
    check(
        "new receipt keys present",
        all(k in receipt for k in ("judges", "degraded_judges", "arcane", "gates")),
    )
    check(
        "arcane_log verdict contract keys present",
        all(
            k in receipt
            for k in ("job_id", "prompt", "seed", "tier", "percentile_rank",
                      "curved_score", "raw_composite", "jury_scores", "judges",
                      "uniqueness", "degraded_judges")
        ),
    )
    check(
        "wall clock stayed inside the 6s budget",
        receipt["elapsed_ms"] <= 12000.0,
        "%.0fms" % receipt["elapsed_ms"],
    )

    print("\n[5] Partial-jury path (2 of 3 answer -- composite renormalises)")
    real_probe = globals()["_probe"]
    real_chat = globals()["_chat_completion"]

    def fake_probe(base_url, headers, timeout):  # noqa: ANN001
        return True  # every endpoint answers /v1/models

    def fake_chat(entry, messages, runtime_cfg, timeout):  # noqa: ANN001
        # Discriminate on the opening declaration, not a bare name: the
        # synthesist's rubric quotes the other two judges by title.
        system = str(messages[0].get("content", ""))
        if system.startswith("You are the Structural Inspector"):
            return (
                "```json\n"
                '{"anatomy": 84, "geometry": 79, "edge_integrity": 88,\n'
                ' "artifact_freedom": 91, "focus_coherence": 86, "overall": 85,\n'
                ' "scene_inventory": ["an obsidian astrolabe", "low mist"],\n'
                ' "worst_defect": "a contour break at the sleeve",\n'
                ' "critique": "minor contour break where the sleeve meets the wrist."}\n'
                "```"
            )
        if system.startswith("You are the Palette & Medium Critic"):
            return (
                "Here is my scorecard.\n"
                '{"palette_cohesion": 77, "lighting_authenticity": 72,'
                ' "medium_authenticity": 69, "tonal_range": 80, "atmosphere": 74,'
                ' "overall": 74, "observed_medium": "digital airbrush posing as ink",'
                ' "critique": "the ink wash reads as a digital filter",}'
            )
        # The synthesist returns a refusal; it must degrade, not be guessed at.
        return "I'm sorry, I cannot evaluate this image."

    globals()["_probe"] = fake_probe
    globals()["_chat_completion"] = fake_chat
    try:
        _clear_probe_cache()
        receipt2 = evaluate(job, cfg)
    finally:
        globals()["_probe"] = real_probe
        globals()["_chat_completion"] = real_chat
        _clear_probe_cache()

    print(
        "\n  receipt: tier=%r raw_composite=%r composite=%r percentile=%r"
        % (
            receipt2["tier"],
            receipt2["raw_composite"],
            receipt2["jury_scores"]["composite"],
            receipt2["percentile_rank"],
        )
    )
    print("  surviving_judges: %s" % (receipt2["surviving_judges"],))
    print("  degraded_judges : %s" % (receipt2["degraded_judges"],))
    print("  jury_scores     : %s" % json.dumps(receipt2["jury_scores"]))
    print("  feature_decoder_source: %s" % receipt2["feature_decoder_source"])
    for judge in receipt2["judges"]:
        print(
            "    - %-10s degraded=%-5s raw=%-6s calibrated=%-6s w=%.2f  %s"
            % (
                judge["role"],
                judge["degraded"],
                judge["score"],
                judge.get("calibrated_score"),
                judge.get("weight", 0.0),
                (judge["critique"] or judge["error"] or "")[:56],
            )
        )

    survivors = set(receipt2["surviving_judges"])
    check("structure judge (visual-witness) survived", "structure" in survivors)
    check("aesthetic judge (pixtral-critic) survived", "aesthetic" in survivors)
    check(
        "refusal-returning synthesist degraded", "synthesis" in receipt2["degraded_judges"]
    )
    check("composite is a real number", receipt2["raw_composite"] is not None)
    check(
        "semantic_fidelity left None (not substituted)",
        receipt2["jury_scores"]["semantic_fidelity"] is None,
    )
    check(
        "feature_decoder is the witness's attributed alias",
        receipt2["jury_scores"]["feature_decoder"]
        == receipt2["jury_scores"]["structure"]
        and receipt2["feature_decoder_source"] == "structure",
    )
    struct_j = next(j for j in receipt2["judges"] if j["role"] == "structure")
    aesth_j = next(j for j in receipt2["judges"] if j["role"] == "aesthetic")
    check(
        "witness carries the retired decoder seat's weight",
        abs(struct_j["weight"] - 0.50) < 1e-6,
        "weight=%s" % struct_j["weight"],
    )
    expected = round(
        (struct_j["calibrated_score"] * 0.50 + aesth_j["calibrated_score"] * 0.35) / 0.85,
        1,
    )
    expected_final = round(_clamp(expected + receipt2["uniqueness_adjustment"]), 1)
    check(
        "composite renormalised over surviving weight only",
        abs(receipt2["raw_composite"] - expected_final) < 0.051,
        "got %s expected %s" % (receipt2["raw_composite"], expected_final),
    )

    print("\n" + "=" * 78)
    if failures:
        print("SELF-TEST FAILED (%d): %s" % (len(failures), "; ".join(failures)))
        print("=" * 78)
        return 1
    print("SELF-TEST PASSED -- degradation yields tier='unscored', never a fake score.")
    print("=" * 78)
    return 0


# --------------------------------------------------------------------------
# Entrypoint
# --------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="moj_evaluator",
        description="Sovereign FLUX Mixture of Judges evaluation engine.",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="run the jury daemon loop (tails the jobs ledger via jury_evaluator)",
    )
    parser.add_argument("--probe", action="store_true", help="report judge liveness and exit")
    parser.add_argument("--score", metavar="IMAGE", help="run one real jury pass over IMAGE")
    parser.add_argument("--prompt", default="", help="prompt to score --score against")
    parser.add_argument("--role", default="", help="restrict --score to a single judge role")
    parser.add_argument("--quiet", action="store_true", help="suppress INFO logging")
    args = parser.parse_args(argv)
    LOG.set_quiet(bool(args.quiet))

    if args.probe:
        print(json.dumps(probe_all(), indent=2, sort_keys=True))
        return 0

    if args.score:
        job = {
            "id": "adhoc-%d" % int(time.time()),
            "seed": 0,
            "prompt": args.prompt,
            "output": args.score,
        }
        if args.role:
            print(
                json.dumps(
                    _jsonable(judge_image(args.score, args.prompt, args.role, None, job=job)),
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0
        print(json.dumps(evaluate(job, None), indent=2, sort_keys=True))
        return 0

    if args.serve:
        try:
            import jury_evaluator
        except Exception as exc:
            LOG.error("cannot start daemon: jury_evaluator import failed: %s" % _short_exc(exc))
            return 2
        jury_evaluator.main()
        return 0

    return _self_test()


if __name__ == "__main__":
    sys.exit(main())
