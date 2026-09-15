# Beauty Protocol Suite

The FLUX Beauty Protocol, bundled to install in one command.

```sh
./bootstrap.sh                 # install here + verify
./bootstrap.sh --serve         # + start the web surface on 127.0.0.1:7863
./bootstrap.sh --serve --publish   # + map beauty.influx.vision via gemstone
./bootstrap.sh --dry-run       # show what would happen
```

## What's in the box

| Piece | File(s) | Role |
|---|---|---|
| Server + CLI | `flux` | the web surface and `flux suites beauty …` commands |
| Generator loop | `beauty_pipeline.py` | latency-first FLUX render loop (512×512, 18 steps, one bounded advisor per frame) |
| Operator eye-gate | `beauty_eye_gate.py` | EGRL three gates + **final submission** to the worker + **metrics** |
| Hardware profiles | `jury_continuum.toml` | 5 H200 profiles + 3 H100 shapes, each with an `[eye_gate]` block |
| Protocol of record | `protocols/EGRL.md`, `protocols/agents/*` | the canonical EGRL doctrine + ralph agent specs |
| Web surface | `apps/beauty/public/*` | gallery, collections, protocol, overview, **profiles**, jury, control |
| Doctrine | `chorus/LAWS.md`, `chorus/PROTOCOL.md`, `chorus/beauty-queue.json` | design laws + the 48-study queue |
| Deploy | `deploy/deploy_h100_beauty.sh`, `scripts/beauty-stack.sh` | provisioning entrypoints |

## The protocol

Three gates, then a hand-off — because the operator is only periodically in the loop:

1. **Gate 1 — builder observation.** The *witness seat* reads the rendered frame. Unviewed output is undefined output.
2. **Gate 2 — independent critic.** Pixtral (built nothing) ranks candidates against calibrated anchors.
3. **Gate 3 — operator.** crown / kill / note; overrides the critic in either direction; persisted verbatim.
4. **Final submission.** An approved brief is pushed back to the FLUX worker as the next generation.

Seats are **roles**, set in `[profiles.*.eye_gate]` — not hardcoded models. `witness = observation` (Qwen by default; a resident Gemma under `adaptive-coexist`), `critic = pixtral`, `synthesis = governor` (Gemma).

## Hardware profiles

Five competing H200 protocols (143 GiB, sm_90, FP8/W4A16) plus the three H100 shapes:

- `h200-resident-tribunal` — every judge on-card, self-contained EGRL, Kontext BF16 resident
- `h200-speed-swarm` — two resident FLUX workers into one tribunal
- `h200-multi-critic` — Pixtral + InternVL; disagreement is the operator signal
- `h200-deep-context` — 131k windows; full anchor set + taste log in-context
- `h200-adaptive-coexist` — **adapts to what's already resident**: reuse live Gemmas as witness+governor, add only FLUX+Pixtral+gates

Pick one: `ARCANE_PROFILE=h200-adaptive-coexist`.

## The loop, day to day

```sh
flux suites beauty architectures     # the profiles
flux suites beauty protocols         # egrl / egrl-recal / tribunal / adaptive / ralpheye
flux suites beauty eye-gate          # the three gates + final submission, explained
flux suites beauty slate             # pending candidates, machine-ranked
flux suites beauty record --job-id <id> --verdict crown --words '…' --delta <n>
flux suites beauty submit  --job-id <id>     # final submission → the worker
flux suites beauty metrics           # agreement rate, override yield, blind-rate (target 0), trend
```

The web surface exposes the same at `/overview` (protocol + live metrics) and `/profiles` (the competing hardware protocols).

## Requirements

- Python 3.11+ preferred (3.10 works — the `tomllib` import is optional).
- An NVIDIA GPU with a matching `[profiles.*]` entry (H200 NVL is native).
- vLLM ≥ 0.13.0 for the served tenants; a resident FLUX BF16 worker for generation.

Nothing here installs system packages or needs root. It lays the suite down, seeds the EGRL ledgers, points `flux` at the bundle, and verifies.
