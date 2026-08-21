# Sovereign FLUX Visual Jury & Continuum Provisioning

The model roster, VRAM budget, ports, and precision for every supported card
are no longer documented here — they live in **`jury_continuum.toml`** as the
single source of truth, read by `provision_jury.sh` via `pipeline_paths.py`.
This file used to hardcode a roster; it drifted stale (wrong model IDs, a
VRAM budget for a card this org doesn't have) while the toml moved on. See
`jury_continuum.toml` line ~300 for the record of what was wrong and why.

## Provisioning

```zsh
cd ~/FLUX
ARCANE_PROFILE=<profile> ./provision_jury.sh --dry-run   # print the plan, touch nothing
ARCANE_PROFILE=<profile> ./provision_jury.sh --status    # what's currently up
ARCANE_PROFILE=<profile> ./provision_jury.sh             # provision it
```

Profiles currently defined in `jury_continuum.toml`: `rtx-pro-6000` (default),
`rtx-pro-6000-x4` (+ its `-dense` / `-tp` layout variants), `b200`, `b300`,
and `h100` — the shape givemeanode's workspace actually hands out. `h100`
runs the governor **remote** (`https://governor.influx.vision/v1`, the
standing governor node) by default: FLUX (35 GiB, BF16) + witness (Qwen3.8-27B
FP8, ~30 GiB) + Pixtral (w4a16, ~9 GiB) + sensory gates (3 GiB) + 3.2 GiB
reserve fills the 80 GiB card without a local governor. A local FP8 governor
copy does not co-tenant with the rest on this card — see the `notes` and the
`governor` tenant's fp8 `variants` note in that profile block for why, and
`deploy/givemeanode/vllm-judge.sh` for the two H100-specific vLLM landmines
(no speculative decoding, `VLLM_ATTENTION_BACKEND=TRITON_ATTN`).

## Pipeline & scoring

See `jury_pipeline.md` for the evaluation flow (sensory gates → jury chamber
→ decoder → governor → verdict) and the composite-score formula; both mirror
`jury_continuum.toml`'s `[verdict]` tables exactly and are not duplicated
here.

## Environment toggles

`ARCANE_LAYOUT`, `ARCANE_KONTEXT`, `ARCANE_GOVERNOR_REMOTE`,
`ARCANE_<TENANT>_PRECISION`, `FLUX_HOME`, `FLUX_OUT_DIR`, `FLUX_BIN`,
`ARCANE_PYTHON`, `HF_HOME`, `VLLM_IMAGE`, `ARCANE_FORCE_RECREATE` — see the
header comment in `provision_jury.sh` for the full, current list; it is
kept there rather than mirrored here for the same reason as the roster.
