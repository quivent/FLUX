# Koyomi Atelier

A live, operator-steered image-generation platform on a single H100. A worker loop
renders anime-styled vintage product-label cards — spice and tea — and a web control
surface lets the operator steer it while it runs.

Plenty of things call themselves a control panel. What makes this one usable is
narrower and worth stating first.

## The ack contract

Every control on this platform is **revision-stamped, delivered to the running worker
over SIGUSR1, and acknowledged by the worker itself.**

The UI never claims a change landed. It shows the worker's own acknowledgement.

    tunables.json         DESIRED   the panel writes it, rev bumped once per edit
    tunables_ack.json     APPLIED   the WORKER writes it, once it has adopted a rev
    tunables_audit.jsonl            append-only, one line per adoption

Same three files for creative direction (`direction.json` / `direction_ack.json` /
`direction_audit.jsonl`). The panel renders **DESIRED → APPLIED** side by side and
stays visibly PENDING until `applied_rev` catches `desired_rev` — and only the worker
can move `applied_rev`.

The delivery path:

1. `POST /api/tune` (or `/api/direction`, `/api/freeze`) clamps the values worker-side
   in `tunables.py`, merges them, bumps the revision, writes the desired file.
2. The control surface `pgrep`s the real worker — matching argv, not a substring, so a
   shell that merely mentions `perpetual.py` is not mistaken for the loop — and sends
   `SIGUSR1` to each pid.
3. `tunables.install()` has a handler armed inside the worker. It adopts the new rev
   *wherever the loop happens to be*, writes the ack with its pid, its cycle, its
   timestamp, and the before/after of every field it moved, and appends the audit line.
4. Measured latency from publish to ack is in the ack itself (`latency_s`), and is
   typically **~10 ms**.

Two properties fall out of this that are easy to lose and hard to get back:

- **Bounds are enforced worker-side**, in `tunables.py`, because the panel is not the
  only thing that can write `tunables.json` — `governord.py` writes it too.
- **A restarted worker re-adopts the standing revision** (`apply(..., force=True)` at
  install). The ack file outlives the process, so without the forced re-adopt a fresh
  worker would run its argv while the panel reported the *previous* worker's ack as
  current.

`direction.py` carries the same contract for the things that change what a picture
looks like: prompt axes, the STYLE template, the Kontext mutation vectors, the pins,
the frozen subject, the variation policy.

## What the pieces are

| file | role | port |
|---|---|---|
| `fluxd.py` | inference daemon; FLUX + Kontext resident, one GPU lock | 8080 |
| `gallery.py` | the public feed — generations newest-first, SSE, thumbs, zips | 8090 |
| `control.py` | the control surface: UI, WS `/ws`, SSE `/events`, all control endpoints | 8091 |
| `triaged.py` | defect triage — structural (numpy) + CLIP zero-shot | 8092 |
| `perpetual.py` | **the worker.** Generate → triage → score → select → mutate → repeat | — |
| `governord.py` | the governor's own clock; consults the LLM on an interval and applies him | — |
| `tunables.py` | live ENGINE knobs; revision + ack + SIGUSR1 contract | — |
| `direction.py` | live CREATIVE controls; same contract | — |
| `collection.py` | the card spec, batch render, and the PIL typography pass | — |
| `concepts.py` | the concept pool — the thing that actually evolves | — |
| `taste.py` | the fitness landscape: CLIP anchors and repulsors from human verdicts | — |
| `estate.py` | the governor's throttle: tempo, stall, ignite, VRAM, co-signs | — |
| `protocol.py` | the governor wire: packet out, directive in, everything audited | — |
| `fluxlib.py` | pipeline loaders, `generate`, `generate_batch`, `edit`, `release` | — |
| `bench_batch.py` | the benchmark that produced the performance table below | — |

The pipeline rule that took the longest to learn: **Kontext edits ART, never a
composited card.** Type is set by PIL afterwards, so a diffusion pass can never mangle
text that is already rendered perfectly. And **aspect is passed explicitly** — left
alone, Kontext snaps to its own preferred resolution and a 2:3 card comes back square
with the type panel cropped off.

## Why the layout is flat

Because rewriting it would be a change nobody could verify without the H100.

These fifteen modules import each other by **bare name** (`import collection as C`,
`import taste as T`) and assume the deployment root is on `PYTHONPATH`. Beyond that,
the root is not merely on the path — it is hardcoded as `/home/dev` in **all fifteen
files, 49 occurrences.** Five call sites do `sys.path.insert(0, "/home/dev")`
outright. `control.py` goes further and **reads `perpetual.py` off disk as text**, to
parse the `MUTATIONS` list out of it so the panel and the worker can never drift.

A `platform/` + `pipeline/` split reads better and would break all of that. Splitting
honestly means rewriting the import graph and every path constant, and the only place
that could be tested is a node running production work. So: flat, exactly as it runs.
`fonts/` is the one subdirectory, because the code already expects
`<root>/fonts/EBGaramond.ttf`.

The deployment root is a constant, not a configurable. **This tree installs to
`/home/dev`.** If you need it elsewhere, symlink `/home/dev` at it, or accept that you
are doing a port and budget for it.

## Hardware

- **NVIDIA H100 80GB HBM3** (79.6 GiB usable).
- **~56 GiB resident** for FLUX + Kontext: FLUX 31.5 GiB, Kontext transformer 22.2 GiB
  (it shares T5, CLIP, VAE and the scheduler with the base pipeline). Measured live:
  54.15 GiB allocated, 55.61 GiB reserved, 20.85 GiB free.
- CLIP (`openai/clip-vit-base-patch32`, fp16, ~0.32 GiB) sits beside FLUX for both
  triage and taste scoring without evicting anything.
- `hf-cache` is ~60 GiB on local disk.

`fluxlib.release()` runs after every render, and must. Without it the caching allocator
holds the peak high-water mark forever — one 896×1344 batch of 8 reserved 71 GiB
against 31.5 GiB actually live, and nothing else fit on the card.

## Prerequisites

- Python 3.12 (3.12.3 in production).
- CUDA 13 driver stack; `nvidia-smi` on PATH (the panel point-samples it).
- ~60 GiB of disk for the model cache, plus room for `runs/`.
- The two FLUX repos in the HF cache: `black-forest-labs/FLUX.1-dev` and
  `black-forest-labs/FLUX.1-Kontext-dev`, plus `openai/clip-vit-base-patch32`.
- `curl`, `pgrep` (procps), and GNU `make`.

### The HF cache gotcha

The single-file checkpoints (`flux1-*.safetensors`, `ae.safetensors` — the ComfyUI
layout, ~24 GiB duplicating the diffusers shards) are deliberately **not** downloaded.
`huggingface_hub` 1.x then calls the snapshot *incomplete* and, under
`HF_HUB_OFFLINE=1`, refuses to hand back the cached path at all.

`fluxlib.resolve()` works around this by passing the same `ignore_patterns` when it
resolves and then loading `from_pretrained` on a plain directory. If you re-download,
**you must re-pass the same `ignore_patterns`** or the cache will read as incomplete
again.

## Bringing it up from cold

The order matters. Each stage depends on the one before it being reachable on loopback.

    make setup      # venv + deps + the directories the code writes into
    make up         # fluxd → gallery → triaged → control → worker, with health waits

Or one stage at a time:

    make fluxd      # 1. the GPU. ~8 s FLUX, ~11 s with Kontext. Wait for /health.
    make gallery    # 2. the feed. perpetual fetches /img/<gen>/<key> from it.
    make triage     # 3. defect triage. The worker's stage 1.
    make control    # 4. the control surface. Reads the three above.
    make worker     # 5. the loop itself. Last, because it calls all of them.

**Why that order.** `perpetual.py` posts renders to `fluxd` on :8080, then asks
`triaged` on :8092 to triage them by handing it *URLs served by `gallery` on :8090*. A
worker started first finds a dead officer and — this is the failure mode that ran for a
whole session — **passes every render through unfiltered while logging one line about
it.** A gate that fails open forever is not a gate. Start the loop last.

    make down       # STOP file, then stop the daemons
    make status     # health of all four ports, worker pid, GPU, rev sync
    make logs       # tail the worker log

`make down` writes `/home/dev/STOP` first. The worker halts **after the current cycle
finishes rendering** — it does not abandon a batch mid-flight.

### Environment

`fluxd`, `triaged` and the worker want:

    HF_HOME=/home/dev/hf-cache
    HF_HUB_OFFLINE=1
    PYTHONPATH=/home/dev

The Makefile sets these. If you start something by hand, set them yourself — the
`PYTHONPATH` is what makes the bare-name imports resolve.

## Exposing the ports

Ports are exposed through the givemeanode control plane, not from inside the node.

    make expose

prints the exact calls. In an MCP-tooled agent session:

    expose_port(node="flux-prod", port=8091)   # the control surface
    expose_port(node="flux-prod", port=8090)   # the public feed

Do not expose 8080 or 8092. They are loopback services with no auth; `perpetual.py`
reaches them at `127.0.0.1` deliberately.

## Measured

H100 80GB, FLUX.1-dev, 448×672, 32 steps. From `bench_batch.py`, 3 reps per size,
warmup discarded, worker stopped so nothing contended for the GPU.

| batch | median wall | per image | images/min | vs batch 4 |
|---|---|---|---|---|
| 1 | 2.49 s | **2.490 s** | 24.1 | 1.15× |
| 2 | 4.65 s | **2.325 s** | 25.8 | 1.07× |
| 4 | 8.69 s | **2.173 s** | 27.6 | 1.00× |

**Batch 1 costs 14.5% more GPU time per image than batch 4** — but latency to the
*first* image is 2.49 s against 8.69 s. The loop runs `VARIANTS = 4` because a cycle
needs all four before it can select a champion, so throughput is the right thing to
optimise there; a UI that wanted one picture fast would want batch 1.

Other measured costs:

| | |
|---|---|
| Kontext edit, 832×1248, 28 steps | **12.4 s** (recent live sample: 12.1 s) |
| 896×1344, 32 steps, batch 8 | 6.9 s/image |
| Cold model load | ~8 s FLUX, ~11 s with Kontext |
| Publish → worker ack (SIGUSR1) | **~10 ms** |

## Known rough edges

These are real and current. None of them is a mystery; all of them will bite you.

**Exposed endpoints die when the node stops and are never resurrected.** A wake does
not bring them back. Every public URL must be re-minted with `expose_port` after every
stop. This is not cosmetic: an earlier build had `perpetual.py` hardcoding an
`expose_port` URL for the triage officer, the node stopped, and the loop spent an
entire session logging `officer unreachable` and passing every render through
unfiltered. That is why triage now runs on loopback, on the same node as the loop,
living and dying with the work it filters. **Do not put an `expose_port` URL in a
constant.**

**The governor is frequently slow, and often returns 524.** `protocol.py` talks to
`https://governor.influx.vision/v1/chat/completions`. He has taken 120 s on a good day,
so `governord.py` uses a 300 s timeout — a short one would report him unreachable while
he was still thinking. Consecutive failures widen the interval up to a 900 s cap so an
outage does not become a retry storm. Silence is logged as a fact, never raised: a tick
that fails does not stop the loop, and the current cycle finishes regardless.

**`taste.py`'s landscape is unanchored by default.** Fitness is closeness to the anchor
cluster, and until a *human* verdict is harvested, the only anchors are two the machine
seeded for itself. Both are flagged `provisional`; `taste.promote_real()` never fires
and `perpetual.py` reports `cold start — provisional champion`. On the live node right
now: **2 anchors, 0 real, 0 repulsors.** The eye-gate in the control surface —
`POST /api/verdict` with `critic: "operator"`, then `POST /api/harvest` — is the only
thing on the node that can produce a non-provisional anchor. Until you use it, the loop
is climbing a hill it drew itself.

**Triage is CLIP, not a VLM.** The design named Qwen2.5-VL; it is not in the cache and
`HF_HUB_OFFLINE=1` means it cannot be fetched. CLIP cannot look at a render and tell
you the hands are wrong. It runs two gates — structural (near-zero variance, crushed
black, blown white, a mosaic of flat tiles: arithmetic, not opinion) and CLIP zero-shot
against a fixed probe set. Real cards measure `p_defect` 0.03–0.10, degenerate ones
0.80+, so the 0.55 threshold has room on both sides. **This is defect detection, not
quality rating** — deliberately, so a merely ugly render still reaches the scorer and a
Retire verdict can teach the anchor set something.

**The UI is on a font CDN.** `control.html` currently loads Zen Maru Gothic, M PLUS
Rounded 1c and JetBrains Mono from `fonts.googleapis.com`, so the panel needs outbound
network for its typography. `control.py` still serves EB Garamond at
`/font/EBGaramond.ttf` and `/font/EBGaramond-Italic.ttf`, and the shipped `fonts/`
directory is still load-bearing — `collection.py` prints every card in it — but the
control surface itself no longer uses it. See `STYLE.md`.

## Migration path, not shipped

The live node carries about thirty `patch_*.py` scripts. They are one-shot migration
scripts — each one rewrote a specific file in place to add a specific capability
(`patch_signal.py` armed the SIGUSR1 handler, `patch_direction.py` introduced the
creative channel, `patch_studio.py` reflowed the panel into the studio grid). They are
the history of how this system got here, and they are all already applied to the code
in this directory. Running one against this tree would corrupt it. They are
deliberately not packaged.

Also not packaged: `runs/`, `out/`, `art/`, `cards/`, `thumbs/`, `hf-cache/`, `venv/`,
logs, and the `*_state.json` / `*_ack.json` / `*.jsonl` runtime state. All of it is
generated. `make setup` creates the directories; the platform fills them.
