# FLUX: High-Performance BF16 Image Synthesis & Visual Estate Topology

## 1. System Architecture & Node Topology
FLUX is an autonomous BF16 image synthesis engine, prompt instrument studio, and multi-surface web estate operated by Node Beta (Gemini / Antigravity) under Council governance.

- **Engine Core**: Resident Python UDS daemon (`worker.py`) communicating over `.fluxd/flux.sock`.
- **Control Gateway**: Go HTTP/WebSocket Server (`cmd/flux` + `internal/server`) listening on `127.0.0.1:7861`.
- **Edge Routing & Ingress**: Caddy reverse-proxy managing `https://flux.influx.vision` with zero-trust edge routing to static assets and dynamic APIs.
- **State & Telemetry**: Ledger at `.fluxd/jobs.jsonl`, hardware profile at `.fluxd/profile.json`, worker PID at `.fluxd/worker.pid`.

## 2. Standardized Application Matrix (flux serve APPNAME)
All visual surfaces are served via standardized CLI commands and mapped to canonical edge URLs:

1. **Constellation Portal** (`flux serve portal` on `:8898` / `https://flux.influx.vision/`):
   Central index, interactive particle constellation, live node health, VRAM telemetry, and launchpad for all apps.
2. **Tea Living Garden** (`flux serve tea` on `:7861` / `https://flux.influx.vision/tea`):
   Living image garden, portrait exhibition salon, and Stallion motion-graph laboratory (`protocols/stallion-motion-v2.json`).
3. **Rosarium Grand Museum** (`flux serve rosarium` on `:7862` / `https://flux.influx.vision/rosarium`):
   Recovered visual vault containing 7,218 catalogued static records, 26 Arcane Princess haute-couture renders, and the 4D Rotunda salon.
4. **Motion Atlas Sphere** (`flux serve atlas` on `:7870` / `https://flux.influx.vision/atlas`):
   3D spherical latent trajectory navigator, Optics raytracer, and live Governor/Visionary dashboard.
5. **Atelier Synthesis Cockpit** (`flux serve atelier` on `:7860` / `https://flux.influx.vision/atelier`):
   Koyomi synthesis cockpit (`control.html`), prompt duel arena, and aesthetic evaluation runner.
6. **FLUX Studio Dashboard** (`flux serve studio` on `:7861` / `https://flux.influx.vision/studio`):
   Core HTTP API, WebSocket live preview stream, queue inspector, and preset lanes.
7. **Live Generation Gallery** (`flux serve gallery` on `:7861/gallery` / `https://flux.influx.vision/gallery`):
   Real-time event stream and generation ledger visualizer.
8. **API Gateway** (`https://flux.influx.vision/api/`):
   Endpoints `/api/health`, `/api/jobs`, `/api/render`, `/api/warm`, `/api/stop`, `/outputs/*`.

### Containerized Beauty Studies Stack
The Tea surface, the Gemstone governor gateway, and the aesthetic jury models are
packaged as a single container declared in `deploy/beauty/Dockerfile` and described
by `deploy/beauty/beauty.manifest.json`. It is driven by `flux beauty`
(`build`, `warm`, `pull`, `up`, `doctor`). `~/.gemstone/governor.json` is the
authority for the governor's model and VRAM posture; the archived R2 snapshot at
`containers/h200-beauty-studies-latest.tar.zst` predates it and is not the source
of truth.

## 3. The 5-Phase Pipeline Execution Graph

### Phase 1: Environment Provisioning (`setup`)
- Command: `flux setup`
- Automation: Ensures `uv` binary on PATH (downloads from astral.sh if missing), creates Python 3.13 `.venv`, and idempotently reconciles all 66 PyTorch/Diffusers dependencies.

### Phase 2: Diagnostics & Model Validation (`models` & `setup`)
- `flux doctor`: Verifies Safetensors shards (`black-forest-labs/FLUX.1-dev`), CUDA/MPS compute capabilities, and BF16 headers.
- `flux download --dry`: Outputs exact `hf download` command for model weights.
- `flux accel`: Identifies acceleration backends (`cuda`, `mps`, `mlx`, `ane`, `cpu`).
- `flux bench`: Benchmarks socket latency and records `.fluxd/profile.json`.

### Phase 3: Daemon Preloading & Residency (`models`)
- Command: `flux load` (or `flux load --preload=false`)
- Mechanism: Spawns `worker.py` attached to `.fluxd/flux.sock` and preloads 32 GB BF16 weights into GPU VRAM. Auto-starts on demand if unheated.

### Phase 4: Creative Synthesis & Workflows (`actions`)
- Single Renders: `flux render "prompt" --preset <hero|anime|noir|space|cover|sketch|object|future> [--async]`
- Image-to-Image: `flux img2img --image <source.png> [--image2 <style.png>] "prompt"`
- Multi-Lane Pipelines: `flux pipeline "subject" --mode <anime|explore|product|architecture|fashion> [--run]`
- Composition & Mutation: `flux shape`, `flux spark`, `flux evolve`, `flux recipes`.

### Phase 5: Queue & Telemetry Supervision (`actions` & `config`)
- Queue State: `flux jobs` (view running, queued, completed, failed tasks).
- Job Actions: `flux jobs cancel <id>`, `flux jobs open latest`, `flux jobs prune`.
- System Overview: `flux studio` (model paths, socket status, PID, presets) and `flux architecture`.
- Reversioning: Enforced on every build via `make flux` (`local/flux/internal/version`).
