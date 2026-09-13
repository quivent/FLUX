# Beauty Protocol — H100 Compact Provisioning Contract

This is the short handoff for agents provisioning the Beauty Protocol. If this
file conflicts with a larger prose document, this file and the active
`jury_continuum.toml` profile win.

## Supported shapes

### One H100

- Hardware: **1 × NVIDIA H100 80 GB HBM3**, `sm_90`.
- Profile: `h100`.
- Governor: **remote by default** at `https://governor.influx.vision/v1`.
- Local GPU tenants:
  - FLUX.1-dev BF16: 35.0 GiB, UDS socket `flux-gpu0.sock`.
  - Qwen3.8-27B-FP8 witness: 30.0 GiB, port `8001`.
  - Pixtral-12B W4A16 critic: 8.8 GiB, port `8002`.
  - DINOv2-Giant + SigLIP gates: 3.0 GiB, in-process.
  - Reserve: 3.2 GiB.
- Total: 80.0 GiB exactly. Do not add another resident model.

### Studio + remote Qwen witness

Profile: `h100-remote-witness`.

- Studio H100: FLUX.1-dev BF16 + Pixtral W4A16 + DINO/SigLIP gates.
- Qwen machine: dedicated remote Qwen witness.
- Gemma governor: remote.
- Kontext: optional; the studio has enough headroom, but it is off by default.
- Set `MOJ_VISUAL_WITNESS_URL` to the Qwen machine's OpenAI-compatible `/v1`.
- If the witness requires authentication, set `MOJ_VISUAL_WITNESS_API_KEY`.

### Distributed Atelier — studio + Qwen + Gemma machines

Profile: `h100-distributed-atelier`.

- Studio H100: FLUX.1-dev BF16 + Pixtral W4A16 + DINO/SigLIP + Kontext.
- Qwen machine: dedicated BF16 visual witness with a larger context/KV budget.
- Gemma machine: dedicated governor for evidence synthesis and prompt evolution.
- Witness and Pixtral evaluation fan out independently; their evidence converges
  at Gemma.
- Keep every tenant at `tensor_parallel=1`.
- Pixtral never moves away from FLUX in any Beauty profile.

## Required H100 settings

- FLUX precision: `bf16`.
- Witness precision: `fp8`, utilization `0.375`, max model length `32768`,
  KV cache `fp8`.
- Pixtral precision: `w4a16`, utilization `0.11`, max model length `32768`,
  KV cache `fp8`.
- Gates: `fp16`, mandatory, in-process.
- vLLM attention backend: `TRITON_ATTN`.
- No speculative decoding.
- Kontext: off. It requires evicting a resident tenant on an 80 GB card.
- Do not use NVFP4 kernels; H100 is Hopper (`sm_90`), not Blackwell.

## Provisioning commands

Preferred deploy entrypoint:

```zsh
sudo /Users/jay/FLUX/deploy/deploy_h100_beauty.sh --dry-run
sudo /Users/jay/FLUX/deploy/deploy_h100_beauty.sh
sudo /Users/jay/FLUX/deploy/deploy_h100_beauty.sh --status
```

```zsh
sudo /Users/jay/FLUX/deploy/deploy_h100_beauty.sh \
  --profile h100-remote-witness \
  --witness-url https://beauty.governor.influx.vision/v1 \
  --dry-run

sudo /Users/jay/FLUX/deploy/deploy_h100_beauty.sh \
  --profile h100-distributed-atelier \
  --witness-url https://beauty.governor.influx.vision/v1 \
  --governor-url https://governor.influx.vision/v1
```

```zsh
cd /Users/jay/FLUX
ARCANE_PROFILE=h100 ./provision_jury.sh --dry-run
ARCANE_PROFILE=h100 ./provision_jury.sh --status
ARCANE_PROFILE=h100 ./provision_jury.sh
```

Before declaring success, require the dry-run to report a fitting studio VRAM
budget, the detected card to be H100/80 GiB, and every configured remote
endpoint plus the local Pixtral critic to pass health checks.

## ralpheye / EGRL operator gate

All three Beauty profiles require the operator eye-gate. A machine
`masterpiece` is queued as a recommendation; it is not promoted automatically.

```zsh
python3.12 beauty_eye_gate.py slate
python3.12 beauty_eye_gate.py record \
  --job-id <job-id> --verdict crown --words '<verbatim operator words>' \
  --delta <signed-placement-delta> --generation <n>
```

Qwen provides Gate 1 observation evidence, Pixtral is the structurally separate
Gate 2 critic, Gemma synthesizes the reports, and the command above records Gate
3 to `taste-log.jsonl`. A crown is the only path into
`masterpiece_vault.jsonl` for Beauty profiles.

Source of truth: `jury_continuum.toml`, `[profiles.h100]` and its tenant tables.
