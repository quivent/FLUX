#!/usr/bin/env python3
"""Single source of truth for every path, endpoint and VRAM number in the
Arcane pipeline.

This module exists to end the `/root/CLIs/flux/` and `/root/Models/flux-output/`
hardcoding that made this repo runnable on exactly one machine. Everything here
is environment-overridable and every default resolves to something that works on
a developer laptop with no CUDA, no Docker and no `/root`.

Import it. Do not re-derive paths.

    import pipeline_paths as pp
    pp.ensure_dirs()
    cfg = pp.load_continuum()
    budget = pp.vram_budget(kontext=False)

Environment variables, all optional:

    FLUX_HOME               repo root                 (default: this file's dir)
    FLUX_OUT_DIR            settled-output root       (default: $FLUX_HOME/outputs)
    FLUX_BIN                path to the `flux` binary (default: which/`$FLUX_HOME/flux`)
    ARCANE_CONTINUUM        path to jury_continuum.toml
    ARCANE_LOG_DIR          structured log sink        (default: $OUT_DIR/logs)
    ARCANE_PROFILE          profile name              (default: toml default_profile)
    ARCANE_LAYOUT           balanced | dense | tp     (multi-GPU profiles only)
    ARCANE_KONTEXT          1/0 — the only tenant toggle
    ARCANE_GOVERNOR_REMOTE  1/0 — serve the governor off-card
    ARCANE_<TENANT>_PRECISION   e.g. ARCANE_FLUX_PRECISION=q4_k_s
    GOVERNOR_BASE_URL       OpenAI-compatible base URL for the governor

There is deliberately no ARCANE_PIXTRAL and no DINO toggle. Pixtral and the
sensory gates are mandatory tenants in every profile — operator standing order.
"""

from __future__ import annotations

import os
import shutil
import tomllib
from pathlib import Path
from typing import Any

__all__ = [
    "FLUX_HOME", "OUT_DIR", "ATLAS_DIR", "FLUXD_DIR", "JOBS_LEDGER", "FLUXD_SOCK",
    "FLUX_BIN", "CONTINUUM_TOML", "AUDIT_LOG", "SQLITE_DB", "CONFIG_JSON",
    "SPECTACLE_LOG", "MASTERPIECE_LOG", "DEFECT_LOG", "WINNING_GENOME_LOG",
    "GOVERNOR_BASE_URL", "GOVERNOR_REMOTE_URL_DEFAULT", "LOG_DIR",
    "ensure_dirs", "load_continuum", "active_profile", "vram_budget",
    "env_flag", "tenant_endpoint", "log_path",
]

GOVERNOR_REMOTE_URL_DEFAULT = "https://governor.influx.vision/v1"

# The four tenant names that are toggle-free, plus the one that is not. Kept
# here so callers can assert against them without parsing the toml.
MANDATORY_TENANTS = ("flux", "witness", "governor", "pixtral", "gates")
TOGGLEABLE_TENANTS = ("kontext",)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

_TRUE = {"1", "true", "yes", "on", "y", "t"}
_FALSE = {"0", "false", "no", "off", "n", "f", ""}


def env_flag(name: str, default: bool | None = None) -> bool | None:
    """Parse a boolean env var. Returns `default` when unset or unparseable."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    lowered = raw.strip().lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    return default


def _env_path(*names: str) -> Path | None:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return Path(value).expanduser()
    return None


def _writable_dir(path: Path) -> bool:
    """True when `path` exists as a usable directory, or could be created.

    Deliberately does not follow a dangling symlink into a directory tree we
    cannot write. `$FLUX_HOME/outputs` in this repo is a symlink to
    /root/Models/flux-output, which exists on exactly one machine.
    """
    try:
        if path.is_symlink() and not path.exists():
            return False          # dangling symlink: the /root case
        if path.is_dir():
            return os.access(path, os.W_OK)
        # Not there yet. Walk up to the nearest existing ancestor.
        parent = path.parent
        while not parent.exists() and parent != parent.parent:
            parent = parent.parent
        return parent.is_dir() and os.access(parent, os.W_OK)
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Roots
# ---------------------------------------------------------------------------

def _resolve_flux_home() -> Path:
    override = _env_path("FLUX_HOME")
    if override is not None:
        return override.resolve()
    # This file lives at the repo root. Resolve from __file__ so a checkout at
    # any path works, including one reached through a symlink.
    return Path(__file__).resolve().parent


FLUX_HOME: Path = _resolve_flux_home()


def _resolve_out_dir() -> Path:
    """Settled-output root.

    Order, mirroring the Makefile's own OUT_DIR chain so the two agree:
      1. $FLUX_OUT_DIR / $OUT_DIR / $FLUX_OUTPUT_DIR
      2. $FLUX_HOME/outputs, if it is usable
      3. /runs/flux-output, if /runs exists (production container layout)
      4. ~/Models/flux-output
    """
    override = _env_path("FLUX_OUT_DIR", "OUT_DIR", "FLUX_OUTPUT_DIR")
    if override is not None:
        return override

    repo_outputs = FLUX_HOME / "outputs"
    if _writable_dir(repo_outputs):
        # Resolve through the symlink only once we know the target is real.
        return repo_outputs.resolve() if repo_outputs.is_symlink() else repo_outputs

    if Path("/runs").is_dir():
        return Path("/runs/flux-output")

    return Path.home() / "Models" / "flux-output"


OUT_DIR: Path = _resolve_out_dir()
ATLAS_DIR: Path = OUT_DIR / "atlas"
ROSARIUM_DIR: Path = OUT_DIR / "rosarium"

FLUXD_DIR: Path = _env_path("FLUXD_DIR") or (FLUX_HOME / ".fluxd")
JOBS_LEDGER: Path = FLUXD_DIR / "flux-gpu0.jobs.jsonl"
FLUXD_SOCK: Path = FLUXD_DIR / "flux-gpu0.sock"
KONTEXT_SOCK: Path = FLUXD_DIR / "flux-kontext.sock"


def _resolve_flux_bin() -> str:
    override = os.environ.get("FLUX_BIN", "").strip()
    if override:
        return str(Path(override).expanduser())
    found = shutil.which("flux")
    if found:
        return found
    return str(FLUX_HOME / "flux")


FLUX_BIN: str = _resolve_flux_bin()

# State files. Every one of these was an absolute /root literal before.
AUDIT_LOG: Path = OUT_DIR / "audit.jsonl"
SQLITE_DB: Path = OUT_DIR / "jury.sqlite3"
CONFIG_JSON: Path = OUT_DIR / "jury_config.json"
SPECTACLE_LOG: Path = OUT_DIR / "spectacle_genome.jsonl"
MASTERPIECE_LOG: Path = OUT_DIR / "masterpiece_vault.jsonl"
DEFECT_LOG: Path = OUT_DIR / "defect_blacklist.jsonl"
WINNING_GENOME_LOG: Path = OUT_DIR / "winning_genome.jsonl"

# Daemon logs live next to the daemon state, not in the output tree.
MOJ_LOG: Path = FLUXD_DIR / "moj_evaluator.log"
STUDIO_LOG: Path = FLUXD_DIR / "studio.log"
JURY_LOG: Path = FLUXD_DIR / "jury_evaluator.log"
FEEDER_LOG: Path = FLUXD_DIR / "feeder.log"

CONTINUUM_TOML: Path = (
    _env_path("ARCANE_CONTINUUM") or (FLUX_HOME / "jury_continuum.toml")
)

# Structured JSONL sink. arcane_log.py derives its default sink from this, so
# every component's log lands under one root that moves with OUT_DIR.
LOG_DIR: Path = _env_path("ARCANE_LOG_DIR") or (OUT_DIR / "logs")


def log_path(component: str) -> Path:
    """Structured JSONL sink for one component, e.g.

        log_path("evaluator")  ->  $OUT_DIR/logs/arcane-evaluator.jsonl

    The component name is slugged so a module's __name__ can be passed straight
    in without producing a nested path.
    """
    slug = "".join(
        ch if (ch.isalnum() or ch in "-_") else "-" for ch in str(component).strip().lower()
    ).strip("-") or "unnamed"
    return LOG_DIR / f"arcane-{slug}.jsonl"


def ensure_dirs() -> None:
    """Create every directory the pipeline writes into. Idempotent, and safe to
    call on a machine where the production paths do not exist."""
    for directory in (OUT_DIR, ATLAS_DIR, ROSARIUM_DIR, FLUXD_DIR, LOG_DIR):
        directory.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Continuum config
# ---------------------------------------------------------------------------

_RAW_CACHE: dict[str, dict[str, Any]] = {}


def _read_toml(path: Path) -> dict[str, Any]:
    key = str(path)
    cached = _RAW_CACHE.get(key)
    if cached is not None:
        return cached
    with open(path, "rb") as handle:
        data = tomllib.load(handle)
    _RAW_CACHE[key] = data
    return data


def active_profile(path: str | None = None) -> str:
    """Name of the profile in force.

    `ARCANE_PROFILE` wins, else the toml's `continuum.default_profile`. A
    multi-GPU machine's layouts are sibling profiles rather than a nested table,
    so that the schema stays exactly `[profiles.<name>.tenants.<role>]`;
    `ARCANE_LAYOUT` selects between them by suffix. `balanced` is the base name.
    """
    name = os.environ.get("ARCANE_PROFILE", "").strip()
    if not name:
        try:
            raw = _read_toml(Path(path) if path else CONTINUUM_TOML)
        except (OSError, tomllib.TOMLDecodeError):
            return "rtx-pro-6000"
        name = str(raw.get("continuum", {}).get("default_profile", "rtx-pro-6000"))

    layout = os.environ.get("ARCANE_LAYOUT", "").strip().lower()
    if not layout:
        return name
    base = name
    for suffix in ("-balanced", "-dense", "-tp"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    return base if layout == "balanced" else f"{base}-{layout}"


def _tenant_precision(name: str, declared: str) -> str:
    return os.environ.get(f"ARCANE_{name.upper()}_PRECISION", "").strip() or declared


def _resolve_tenant(
    name: str,
    tenant: dict[str, Any],
    per_gpu_gib: float,
    warnings: list[str],
) -> dict[str, Any]:
    """Flatten one tenant: apply the precision knob, pull the matching entry out
    of `variants`, and derive the VRAM reservation rather than trusting the
    literal in the file."""
    variants: dict[str, Any] = dict(tenant.get("variants", {}))
    declared = str(tenant.get("precision", ""))
    precision = _tenant_precision(name, declared)

    chosen = variants.get(precision)
    if chosen is None and variants:
        if precision != declared:
            warnings.append(
                f"{name}: no '{precision}' variant; falling back to '{declared}'. "
                f"Available: {', '.join(sorted(variants))}"
            )
        precision = declared
        chosen = variants.get(declared, {})
    chosen = dict(chosen or {})

    resolved: dict[str, Any] = {
        k: v for k, v in tenant.items() if k != "variants"
    }
    resolved.update(chosen)
    resolved["name"] = name
    resolved["precision"] = precision
    resolved["variants_available"] = sorted(variants)

    kind = str(resolved.get("kind", "vllm"))
    tp = int(resolved.get("tensor_parallel", 1) or 1)

    # Which physical cards this tenant occupies. `gpu_span` is explicit for a
    # tensor-parallel tenant; otherwise it is the single card named by `gpu`.
    span = resolved.get("gpu_span")
    if span:
        span = [int(g) for g in span]
    else:
        span = [int(resolved.get("gpu", 0) or 0)]
    if tp != len(span):
        warnings.append(
            f"{name}: tensor_parallel={tp} but gpu_span covers {len(span)} card(s) "
            f"({span}). A TP tenant occupies exactly one card per rank."
        )
    resolved["gpu"] = span[0]
    resolved["gpu_span"] = span
    resolved["tensor_parallel"] = tp

    # Derive the reservation. For a vLLM tenant, gpu_memory_utilization is a
    # fraction of ONE card and already covers weights + activations + KV. A
    # TP tenant pays that fraction on every card it spans.
    if kind == "vllm":
        util = float(resolved.get("gpu_memory_utilization", 0.0) or 0.0)
        per_card = round(util * per_gpu_gib, 2)
        derived = round(per_card * len(span), 2)
        declared_vram = resolved.get("vram_expected_gib")
        if declared_vram is not None and abs(float(declared_vram) - derived) > 0.011:
            warnings.append(
                f"{name}: declared vram_expected_gib {declared_vram} != "
                f"util*vram*cards {derived}; using the derived value"
            )
        resolved["vram_per_card_gib"] = per_card
        resolved["vram_expected_gib"] = derived
    else:
        per_card = float(resolved.get("vram_expected_gib", 0.0) or 0.0)
        resolved["vram_per_card_gib"] = per_card
        resolved["vram_expected_gib"] = round(per_card * len(span), 2)
        resolved.setdefault("gpu_memory_utilization", None)

    resolved.setdefault("weights_gib", resolved["vram_expected_gib"])
    resolved.setdefault("port", None)
    resolved.setdefault("role", "")
    resolved.setdefault("enabled", True)
    resolved.setdefault("dense", True)

    if resolved.get("degrades_generator"):
        warnings.append(
            f"{name}: precision '{precision}' degrades the generator. It sacrifices the "
            "impasto and brush-texture detail the Fortiche aesthetic rubric measures. "
            "Use it only as a deliberate low-VRAM escape hatch."
        )
    if resolved.get("dense") is False:
        warnings.append(
            f"{name}: dense=false. MoE NVFP4 kernels are broken on sm_120 "
            "(vllm#33416, vllm#31085, flashinfer#2577). This will not run on "
            "RTX PRO 6000."
        )
    return resolved


def _apply_toggles(name: str, tenant: dict[str, Any]) -> bool:
    """Whether this tenant is on. Only kontext is actually toggleable; the rest
    are mandatory and stay on whatever anyone passes."""
    if tenant.get("mandatory", False) or name in MANDATORY_TENANTS:
        return True
    if name == "kontext":
        return bool(env_flag("ARCANE_KONTEXT", bool(tenant.get("enabled", False))))
    return bool(tenant.get("enabled", False))


def _governor_is_remote(tenant: dict[str, Any]) -> bool:
    return bool(env_flag("ARCANE_GOVERNOR_REMOTE", bool(tenant.get("remote", False))))


def tenant_endpoint(tenant: dict[str, Any]) -> str | None:
    """OpenAI-compatible base URL for a served tenant, or None if it has no
    HTTP surface (UDS worker, in-process gate)."""
    if tenant.get("remote"):
        return str(tenant.get("remote_base_url") or GOVERNOR_REMOTE_URL_DEFAULT)
    port = tenant.get("port")
    if not port:
        return None
    host = os.environ.get("ARCANE_BIND_HOST", "127.0.0.1")
    return f"http://{host}:{int(port)}/v1"


def load_continuum(path: str | None = None) -> dict:
    """Parse jury_continuum.toml and return the ACTIVE profile, fully resolved.

    The returned dict carries:
        profile              name of the active profile
        hardware             gpu / sm / vram_gib / reserve / wheel availability
        tenants              {name: resolved tenant}, precision applied,
                             vram_expected_gib derived, `enabled` reflecting
                             the toggles actually in force
        endpoints            {name: base_url} for everything with an HTTP surface
        weights              [verdict].weights, unchanged shape
        verdict              the whole [verdict] table, paths made absolute
        toggles              what the toggle state resolved to and why
        vram                 the budget for the current toggle state
        warnings             anything the caller should know about
    """
    toml_path = Path(path) if path else CONTINUUM_TOML
    raw = _read_toml(toml_path)
    warnings: list[str] = []

    profile_name = active_profile(str(toml_path))
    profiles = raw.get("profiles", {})
    if profile_name not in profiles:
        available = ", ".join(sorted(profiles)) or "<none>"
        raise KeyError(
            f"ARCANE_PROFILE={profile_name!r} is not defined in {toml_path}. "
            f"Available profiles: {available}"
        )
    profile = profiles[profile_name]

    per_gpu = float(profile.get("vram_per_gpu_gib", profile.get("vram_gib", 0.0)))
    gpu_count = int(profile.get("gpus", profile.get("gpu_count", 1)) or 1)
    # On a multi-card profile `vram_gib` is the PER-CARD figure; the aggregate is
    # per_gpu * gpus. It is never a pool — see vram_budget()'s per-GPU `fits`.
    total_gib = (round(per_gpu * gpu_count, 2) if gpu_count > 1
                 else float(profile.get("vram_gib", per_gpu)))

    tenants: dict[str, Any] = {}
    for name, tenant in profile.get("tenants", {}).items():
        resolved = _resolve_tenant(name, tenant, per_gpu, warnings)
        resolved["enabled"] = _apply_toggles(name, tenant)
        if name == "governor":
            resolved["remote"] = _governor_is_remote(resolved)
            if resolved["remote"]:
                resolved["vram_expected_gib"] = 0.0
                resolved["weights_gib"] = 0.0
                resolved["port"] = None
        tenants[name] = resolved

    endpoints = {
        name: url
        for name, tenant in tenants.items()
        if tenant.get("enabled") and (url := tenant_endpoint(tenant))
    }

    verdict = dict(raw.get("verdict", {}))
    if verdict.get("path_base") == "out_dir":
        for key in ("audit_log", "masterpiece_destination"):
            value = verdict.get(key)
            if value and not os.path.isabs(str(value)):
                verdict[key] = str(OUT_DIR / str(value))

    governor = tenants.get("governor", {})
    budget = vram_budget(
        profile=profile_name,
        kontext=tenants.get("kontext", {}).get("enabled", False),
        governor_remote=governor.get("remote", False),
        _path=str(toml_path),
    )
    warnings.extend(w for w in budget.get("warnings", []) if w not in warnings)

    return {
        "profile": profile_name,
        "hardware": {
            "gpu": profile.get("gpu"),
            "sm": profile.get("sm"),
            "layout": profile.get("layout"),
            "layout_siblings": profile.get("layout_siblings", []),
            "vram_gib": total_gib,
            "vram_per_gpu_gib": per_gpu,
            "gpus": gpu_count,
            "gpu_count": gpu_count,
            "reserve_gib": float(profile.get("reserve_gib", 0.0)),
            "interconnect": profile.get("interconnect", "none"),
            "interconnect_verified": bool(profile.get("interconnect_verified", False)),
            "interconnect_detected": profile.get("interconnect_detected") or None,
            "tensor_parallel_viable": bool(
                profile.get("tensor_parallel_viable", gpu_count > 1)
            ),
            "vllm_min_version": profile.get("vllm_min_version"),
            "native_nvfp4_dense": profile.get("native_nvfp4_dense"),
            "native_nvfp4_moe": profile.get("native_nvfp4_moe"),
            "prebuilt_wheel_available": profile.get("prebuilt_wheel_available"),
            "notes": profile.get("notes"),
        },
        "continuum": raw.get("continuum", {}),
        "ports": raw.get("ports", {}),
        "tenants": tenants,
        "endpoints": endpoints,
        "governor_base_url": _governor_base_url(tenants, raw),
        "weights": verdict.get("weights", {}),
        "masterpiece_threshold": verdict.get("masterpiece_threshold"),
        "verdict": verdict,
        "retired_models": raw.get("retired", {}).get("models", []),
        "toggles": {
            "kontext": tenants.get("kontext", {}).get("enabled", False),
            "governor_remote": governor.get("remote", False),
            "pixtral": True,   # mandatory, not a toggle
            "gates": True,     # mandatory, not a toggle
        },
        "paths": {
            "flux_home": FLUX_HOME,
            "out_dir": OUT_DIR,
            "atlas_dir": ATLAS_DIR,
            "fluxd_dir": FLUXD_DIR,
            "jobs_ledger": JOBS_LEDGER,
            "fluxd_sock": FLUXD_SOCK,
            "flux_bin": FLUX_BIN,
            "log_dir": LOG_DIR,
            "continuum_toml": toml_path,
        },
        "vram": budget,
        "warnings": warnings,
    }


def _governor_base_url(tenants: dict[str, Any], raw: dict[str, Any]) -> str:
    """Explicit env wins. Otherwise: localhost when the governor is a local
    tenant, the remote endpoint when it is not."""
    override = os.environ.get("GOVERNOR_BASE_URL", "").strip()
    if override:
        return override
    section = raw.get("governor", {})
    remote_url = str(section.get("remote_base_url") or GOVERNOR_REMOTE_URL_DEFAULT)
    local_url = str(section.get("local_base_url") or "http://127.0.0.1:8000/v1")
    governor = tenants.get("governor", {})
    if governor.get("enabled") and not governor.get("remote"):
        return local_url
    return remote_url


# ---------------------------------------------------------------------------
# VRAM budget
# ---------------------------------------------------------------------------

def vram_budget(
    profile: str | None = None,
    kontext: bool | None = None,
    pixtral: bool = True,
    governor_remote: bool | None = None,
    *,
    _path: str | None = None,
) -> dict:
    """Model the VRAM reservation for one toggle combination.

    Returns:
        {"total_gib", "allocated_gib", "free_gib", "fits", "tenants",
         "overcommit_reason", "per_gpu", ...}

    `fits` is PER-GPU and stays per-GPU on multi-card profiles. A 384 GiB
    aggregate does not absorb a tenant that overflows a single 96 GiB card, and
    NVLink does not change that — tensor parallelism changes what can SPAN
    cards, not what a single card can hold.

    `pixtral` is accepted for signature compatibility only. Pixtral is a
    mandatory tenant in every profile and is always counted; passing False does
    not remove it, it just records a note saying so.
    """
    toml_path = Path(_path) if _path else CONTINUUM_TOML
    raw = _read_toml(toml_path)
    profile_name = profile or active_profile(str(toml_path))
    profiles = raw.get("profiles", {})
    if profile_name not in profiles:
        available = ", ".join(sorted(profiles)) or "<none>"
        raise KeyError(f"unknown profile {profile_name!r}; available: {available}")
    prof = profiles[profile_name]

    per_gpu = float(prof.get("vram_per_gpu_gib", prof.get("vram_gib", 0.0)))
    gpu_count = int(prof.get("gpus", prof.get("gpu_count", 1)) or 1)
    reserve_per_gpu = float(prof.get("reserve_gib", 0.0))
    if gpu_count > 1:
        total = round(per_gpu * gpu_count, 2)
        reserve = round(reserve_per_gpu * gpu_count, 2)
    else:
        total = float(prof.get("vram_gib", per_gpu))
        reserve = reserve_per_gpu
    usable_per_gpu = round(per_gpu - reserve_per_gpu, 2)
    usable = round(total - reserve, 2)

    warnings: list[str] = []
    notes: list[str] = []
    if pixtral is False:
        notes.append(
            "pixtral=False was passed and ignored: Pixtral is a mandatory tenant "
            "in every profile (operator standing order). It is counted below."
        )

    raw_tenants = prof.get("tenants", {})
    gov_raw = raw_tenants.get("governor", {})
    if governor_remote is None:
        governor_remote = _governor_is_remote(gov_raw)

    tp_viable = bool(prof.get("tensor_parallel_viable", gpu_count > 1))
    interconnect = str(prof.get("interconnect", "none"))
    verified = bool(prof.get("interconnect_verified", False))
    detected = str(prof.get("interconnect_detected", "") or "")

    # cards[i] accumulates the reservation landed on physical GPU i.
    cards = [0.0 for _ in range(gpu_count)]
    card_tenants: list[list[str]] = [[] for _ in range(gpu_count)]

    rows: list[dict[str, Any]] = []
    allocated = 0.0
    weights_total = 0.0
    for name, tenant in raw_tenants.items():
        resolved = _resolve_tenant(name, tenant, per_gpu, warnings)

        if name == "kontext":
            on = bool(tenant.get("enabled", False)) if kontext is None else bool(kontext)
        else:
            on = True     # every other tenant is mandatory

        vram = float(resolved["vram_expected_gib"])
        per_card = float(resolved.get("vram_per_card_gib", vram))
        weights = float(resolved.get("weights_gib", vram) or 0.0)
        span = list(resolved.get("gpu_span", [0]))
        tp = int(resolved.get("tensor_parallel", 1) or 1)
        remote = False
        if name == "governor" and governor_remote:
            remote, vram, per_card, weights, span = True, 0.0, 0.0, 0.0, []

        if tp > 1 and not tp_viable:
            warnings.append(
                f"{name}: tensor_parallel={tp} on profile {profile_name}, whose "
                f"interconnect is {interconnect!r} and tensor_parallel_viable is "
                "false. TP needs full weight tensors on the wire; over PCIe Gen5 "
                "x16 (~64 GB/s vs NVLink's ~900 GB/s) that is a performance trap, "
                "not a capacity win. This is a misconfiguration."
            )
        if tp > 1 and not verified:
            warnings.append(
                f"{name}: tensor_parallel={tp} relies on a DECLARED interconnect "
                f"({interconnect!r}) that has not been verified on this host. "
                "Confirm with `nvidia-smi nvlink --status` and `nvidia-smi topo -m` "
                "before trusting this layout"
                + (f" (detected: {detected})." if detected else ".")
            )

        for g in span:
            if 0 <= g < gpu_count:
                cards[g] += per_card
                if on:
                    card_tenants[g].append(name)
            else:
                warnings.append(
                    f"{name}: placed on gpu {g}, but profile {profile_name} has "
                    f"only {gpu_count} card(s)."
                )
        if not on:
            for g in span:
                if 0 <= g < gpu_count:
                    cards[g] -= per_card

        if on:
            allocated += vram
            weights_total += weights

        rows.append({
            "name": name,
            "role": resolved.get("role", ""),
            "kind": resolved.get("kind"),
            "model": resolved.get("model"),
            "precision": resolved.get("precision"),
            "gpu": span[0] if span else None,
            "gpu_span": span,
            "tensor_parallel": tp,
            "port": None if remote else resolved.get("port"),
            "gpu_memory_utilization": resolved.get("gpu_memory_utilization"),
            "vram_expected_gib": round(vram, 2),
            "vram_per_card_gib": round(per_card, 2),
            "weights_gib": round(weights, 2),
            "dense": resolved.get("dense", True),
            "enabled": on,
            "remote": remote,
        })

    allocated = round(allocated, 2)
    weights_total = round(weights_total, 2)
    free = round(total - allocated, 2)
    headroom = round(usable - allocated, 2)

    per_gpu_rows = []
    worst_over = 0.0
    worst_card = None
    for i in range(gpu_count):
        used = round(cards[i], 2)
        card_head = round(usable_per_gpu - used, 2)
        card_fits = used <= usable_per_gpu
        if not card_fits and (used - usable_per_gpu) > worst_over:
            worst_over = round(used - usable_per_gpu, 2)
            worst_card = i
        per_gpu_rows.append({
            "gpu": i,
            "total_gib": per_gpu,
            "reserve_gib": reserve_per_gpu,
            "usable_gib": usable_per_gpu,
            "allocated_gib": used,
            "free_gib": round(per_gpu - used, 2),
            "headroom_gib": card_head,
            "fits": card_fits,
            "tenants": card_tenants[i],
        })

    fits = all(r["fits"] for r in per_gpu_rows)

    reason: str | None = None
    if not fits:
        bad = [r for r in per_gpu_rows if not r["fits"]]
        detail = "; ".join(
            f"gpu{r['gpu']} {r['allocated_gib']}/{r['usable_gib']} usable "
            f"(over by {round(r['allocated_gib'] - r['usable_gib'], 2)}, "
            f"holding {', '.join(r['tenants']) or 'nothing'})"
            for r in bad
        )
        shed: list[str] = []
        if kontext is not False and any(
            t["name"] == "kontext" and t["enabled"] for t in rows
        ):
            shed.append("turn Kontext OFF (ARCANE_KONTEXT=0)")
        if not governor_remote:
            gov = next((r for r in rows if r["name"] == "governor"), None)
            if gov:
                shed.append(
                    f"move the governor off-card (ARCANE_GOVERNOR_REMOTE=1, "
                    f"frees {gov['vram_per_card_gib']} GiB on gpu{gov['gpu']})"
                )
        flux = next((r for r in rows if r["name"] == "flux"), None)
        if flux and flux["precision"] != "q4_k_s":
            shed.append(
                "or ARCANE_FLUX_PRECISION=q4_k_s (frees ~17 GiB per generator card, "
                "at the cost of generator texture fidelity)"
            )
        if gpu_count > 1:
            shed.append("or move a tenant to a card with headroom (`gpu = N`)")
        reason = (
            f"{profile_name}: overcommitted — {detail}. "
            + ("Shed one of: " + "; ".join(shed) + "." if shed else
               "No toggle can recover this; the profile itself is misconfigured.")
        )
        if gpu_count > 1:
            reason += (
                f" Aggregate is {allocated}/{usable} GiB across {gpu_count} cards, "
                "which looks fine and is irrelevant: `fits` is per-GPU."
            )
        warnings.append(reason)
    else:
        thin = min(per_gpu_rows, key=lambda r: r["headroom_gib"])
        if thin["headroom_gib"] < 1.0:
            warnings.append(
                f"CRITICAL — {profile_name}: gpu{thin['gpu']} fits by "
                f"{thin['headroom_gib']} GiB "
                f"({round(100.0 * thin['headroom_gib'] / per_gpu, 2)}% of a "
                f"{per_gpu} GiB card). That is a rounding error, not a margin. It "
                "will boot and it will OOM the first time a KV cache grows, a "
                "driver update moves the CUDA context size, or a display is "
                "attached. Do NOT run this posture unattended: set "
                "ARCANE_GOVERNOR_REMOTE=1."
            )
        elif thin["headroom_gib"] < 5.0:
            warnings.append(
                f"{profile_name}: gpu{thin['gpu']} fits with only "
                f"{thin['headroom_gib']} GiB of headroom over the "
                f"{reserve_per_gpu} GiB reserve. It will run, and it will be the "
                "first thing to OOM if a KV cache grows or the CUDA context size "
                "moves. For unattended runs set ARCANE_GOVERNOR_REMOTE=1."
            )

    if prof.get("prebuilt_wheel_available") is False:
        warnings.append(
            f"{profile_name}: no prebuilt vLLM wheel for {prof.get('sm')} in the "
            f"R2 artifact bank (sm100/sm80 only); build vLLM >= "
            f"{prof.get('vllm_min_version', '0.13.0')} for {prof.get('sm')} first."
        )
    if prof.get("native_nvfp4_moe") is False:
        notes.append(
            f"{prof.get('sm')}: NVFP4 MoE kernels are broken (vllm#33416, "
            "vllm#31085, flashinfer#2577). Every tenant on this roster is dense, "
            "so this roster is safe. Do not substitute an MoE checkpoint. This is "
            "a kernel-family issue and is unaffected by the interconnect."
        )
    if gpu_count > 1 and not verified:
        notes.append(
            f"{profile_name}: interconnect {interconnect!r} is DECLARED, not "
            "verified. Provisioning must confirm it with `nvidia-smi nvlink "
            "--status` and `nvidia-smi topo -m` and warn on mismatch. Public specs "
            "for RTX PRO 6000 Blackwell state no NVLink; the operator reports "
            "otherwise for this machine. Detect, do not assume."
        )

    return {
        "profile": profile_name,
        "layout": prof.get("layout"),
        "gpu": prof.get("gpu"),
        "sm": prof.get("sm"),
        "gpus": gpu_count,
        "interconnect": interconnect,
        "interconnect_verified": verified,
        "interconnect_detected": detected or None,
        "tensor_parallel_viable": tp_viable,
        "total_gib": total,
        "vram_per_gpu_gib": per_gpu,
        "reserve_gib": reserve,
        "usable_gib": usable,
        "allocated_gib": allocated,
        "weights_gib": weights_total,
        "free_gib": free,
        "headroom_gib": headroom,
        "fits": fits,
        "per_gpu": per_gpu_rows,
        "kontext": next((t["enabled"] for t in rows if t["name"] == "kontext"), False),
        "pixtral": True,
        "governor_remote": bool(governor_remote),
        "tenants": rows,
        "overcommit_reason": reason,
        "warnings": warnings,
        "notes": notes,
    }


# ---------------------------------------------------------------------------

def _selftest() -> None:  # pragma: no cover - operator convenience
    print(f"FLUX_HOME           {FLUX_HOME}")
    print(f"OUT_DIR             {OUT_DIR}")
    print(f"ATLAS_DIR           {ATLAS_DIR}")
    print(f"FLUXD_DIR           {FLUXD_DIR}")
    print(f"JOBS_LEDGER         {JOBS_LEDGER}")
    print(f"FLUXD_SOCK          {FLUXD_SOCK}")
    print(f"FLUX_BIN            {FLUX_BIN}")
    print(f"LOG_DIR             {LOG_DIR}")
    print(f"CONTINUUM_TOML      {CONTINUUM_TOML}")
    print(f"GOVERNOR_BASE_URL   {GOVERNOR_BASE_URL}")
    print()
    names = sorted(_read_toml(CONTINUUM_TOML).get("profiles", {}))
    for name in names:
        for kontext in (False, True):
            for remote in (False, True):
                b = vram_budget(name, kontext=kontext, governor_remote=remote)
                cards = " ".join(
                    f"g{r['gpu']}={r['allocated_gib']:.2f}" for r in b["per_gpu"]
                )
                print(
                    f"{name:<24} kontext={str(kontext):<5} remote={str(remote):<5} "
                    f"alloc={b['allocated_gib']:>7.2f}/{b['total_gib']:<6.1f} "
                    f"free={b['free_gib']:>7.2f} fits={str(b['fits']):<5} {cards}"
                )


# GOVERNOR_BASE_URL is a module constant, so it has to resolve at import time
# and must never raise: a malformed or missing toml falls back to the remote
# endpoint, which is always a valid answer.
def _initial_governor_base_url() -> str:
    override = os.environ.get("GOVERNOR_BASE_URL", "").strip()
    if override:
        return override
    try:
        raw = _read_toml(CONTINUUM_TOML)
        profile_name = active_profile()
        gov = raw.get("profiles", {}).get(profile_name, {}).get("tenants", {}).get("governor", {})
        section = raw.get("governor", {})
        if gov and not _governor_is_remote(gov):
            return str(section.get("local_base_url") or "http://127.0.0.1:8000/v1")
        return str(section.get("remote_base_url") or GOVERNOR_REMOTE_URL_DEFAULT)
    except (OSError, tomllib.TOMLDecodeError, KeyError, TypeError, ValueError):
        return GOVERNOR_REMOTE_URL_DEFAULT


GOVERNOR_BASE_URL: str = _initial_governor_base_url()


if __name__ == "__main__":  # pragma: no cover
    _selftest()
