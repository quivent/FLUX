# FLUX.1-dev BF16 Local Runner

This is a minimal Python runner for the local BF16 Diffusers-format model at:

```text
/Users/joshkornreich/Models/flux1
```

It does not start a server and does not use ComfyUI.

## Go CLI

Build and install the local command:

```zsh
cd /Users/joshkornreich/FLUX
make flux
```

Use it:

```zsh
flux doctor
flux accel
flux atlas motion
flux jobs
```

The Go CLI is a colored local control surface over the lean Python runner. It
does not download model files or contact Hugging Face during generation.

Second-pass features:

- `studio`: runtime posture and preset lanes.
- `gpu`: NVIDIA/Torch GPU state and active compute process view.
- `accel`: current acceleration stack and candidate backend availability.
- `ane`: strict ANE package registry, validation state, and first component
  conversion command.
- `bench`: socket benchmark for concrete backends; updates `.fluxd/profile.json`
  for `backend=auto` selection.
- `tree`: command topology in Council-style branches.
- `colors`: palette and state-color sample.
- `download`: prints the lean `hf download` command for BF16 Diffusers files.
- `preset`: named render lanes such as `sketch`, `hero`, `object`, `space`,
  `cover`, `future`, `anime`, and `noir`.
- `shape`: prompt composition without generation.
- `spark`: six fast prompt mutations for a subject.
- `muse`: a shot board that turns a subject into concrete local or remote
  render commands.
- `plan`: exact local command preview.
- `burst`: multiple seed variants with one command.
- `warm`: persistent worker that loads FLUX into memory.
- `atlas motion`: install prerequisites and open the dedicated Motion Atlas Sphere web suite
  paths, geometry, quality, traversal, and cross-frame cache settings.
- `serve`: local HTTP API and dashboard backed by the Unix socket worker.
- `gallery`: Atelier-style live gallery backed by the same server and event streams.
- `render --async`: queue jobs on the worker.
- `jobs`: inspect queued, running, finished, or failed jobs.
- `install`: symlink `flux` into `~/.local/bin/flux`.

## Warm Worker And Jobs

Direct renders load the model, render, then exit. For repeated work, start the
worker so the model can stay resident:

```zsh
flux warm
```

That starts `worker.py` and preloads the model into memory. Use the async path:

```zsh
flux render "glass cabin in snow" --preset hero --async
flux jobs
```

Stop the resident worker:

```zsh
flux stop
```

For a lightweight worker test that does not preload the 32 GB model:

```zsh
flux warm --preload=false
flux jobs
flux gpu
flux stop
```

## HTTP Server

Start the local server:

```zsh
flux serve --addr 127.0.0.1:7861
```

Open `http://127.0.0.1:7861` for the dashboard, or use the API directly:

```zsh
curl http://127.0.0.1:7861/api/health
curl http://127.0.0.1:7861/api/jobs
curl -X POST http://127.0.0.1:7861/api/render \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"glass cabin in snow","preset":"hero","dry_run":true}'
```

Expose it to another machine with a bearer token:

```zsh
export FLUX_HTTP_TOKEN="$(openssl rand -hex 24)"
flux serve --addr 0.0.0.0:7861
```

Remote client:

```zsh
FLUX_HTTP_TOKEN="shared-token" flux remote status --url http://YOUR_HOST:7861
FLUX_HTTP_TOKEN="shared-token" flux remote render --url http://YOUR_HOST:7861 "anime city at dawn" --preset hero --wait
```

The HTTP server does not load the model by itself. It starts the Unix socket
worker on first real render, or when you call `POST /api/warm`. Use
`POST /api/warm?preload=1` only when you intentionally want the model loaded
into memory.

Completed jobs include a public `output_url` when the output image is in the
configured output directory. The dashboard renders that image inline, and the
remote CLI prints the same URL from `remote jobs` or `remote render --wait`.

Start directly in the live gallery:

```zsh
flux gallery --addr 127.0.0.1:7861 --open
```

The gallery page uses the same server and streams `/api/jobs/events`,
`/api/img2img/events`, and `/api/gallery/events`.

## Backend Benchmarking

Benchmark concrete backends through the same Unix socket used by renders:

```zsh
flux bench --dry-run --backends mps,mlx
flux bench --backends mps,mlx --width 768 --height 768 --steps 8
```

If the socket is already running, `bench` reuses it and does not launch a
second worker. If no socket is running, it starts the non-preloading queue
worker first. Completed timings are stored in `.fluxd/profile.json`; later
`--backend auto` jobs can use that profile for the same size and step count.

## ANE Adapter Direction

The current runner does not use the Apple Neural Engine for full FLUX
generation. `mps` and `mlx` are GPU/Metal paths; `coreml` is a scaffold for
fixed-shape compiled packages and does not prove ANE execution by itself.

Apple exposes ANE to third-party apps through Core ML, not through a public
Metal-like kernel API. This project therefore treats Core ML as the supported
compiler/runtime path into ANE, and treats Instruments profiling as the proof
that Core ML actually scheduled useful work on the Neural Engine.

The dedicated `ane` backend is now wired as a strict adapter contract. It only
becomes benchmark/selectable when `model/ane/registry.json` contains a
validated full-pipeline package. The first build command converts the
fixed-shape VAE decoder component:

```zsh
flux ane init
flux ane convert-vae --width 1024 --height 1024
flux ane direct-capture --width 1024 --height 1024 --block-type dual --block-index 0
flux ane probe
```

That component is useful groundwork, but it is not a full render backend by
itself. ANE validation should be recorded only after Instruments shows Neural
Engine activity for the package.

Current ANE commands:

```zsh
flux ane probe
flux ane init
flux ane convert-vae --width 1024 --height 1024
flux ane validate --name PACKAGE_NAME --notes "Instruments run ..."
flux ane direct-capture --width 1024 --height 1024 --block-type dual --block-index 0
flux ane direct-pack --manifest /Users/joshkornreich/Models/flux1/ane/direct/dual_block_0_1024x1024.json
```

`flux accel` reports `ane_packages`, `ane_components`, `ane_validated`, and
`ane_renderable`. `ane_renderable=false` is expected until a validated
full-pipeline package exists. A converted VAE decoder package is a component
artifact, not a complete image-generation path.

`flux ane direct-capture` starts the direct-ANE research track by writing a
fixed-shape MMDiT block manifest under:

```text
/Users/joshkornreich/Models/flux1/ane/direct
```

That manifest records block input/output shapes, dtypes, parameter inventory,
and the target shape contract. It does not claim ANE execution.

`flux ane direct-pack` turns that manifest into a first weight-packing plan.
For `dual[0]` at `1024x1024`, the current bf16 source inventory is about
`648 MB`: roughly `288 MB` MLP, `216 MB` modulation, `144 MB` attention, plus
small norm/bias tensors.

## Setup

```zsh
cd /Users/joshkornreich/FLUX
make setup
make check
```

## Generate

```zsh
make generate PROMPT="a small glass cabin in a snowy forest, cinematic light"
```

Useful overrides:

```zsh
make generate \
  PROMPT="a product photo of a translucent orange mechanical keyboard" \
  WIDTH=1024 \
  HEIGHT=1024 \
  STEPS=28 \
  GUIDANCE=3.5 \
  SEED=1234
```

Images are written to:

```text
/Users/joshkornreich/Models/flux-output
```

## Direct Python

```zsh
source .venv/bin/activate
python generate.py \
  --prompt "a small glass cabin in a snowy forest, cinematic light" \
  --steps 28 \
  --guidance 3.5 \
  --width 1024 \
  --height 1024
```

The runner uses `local_files_only=True`, so generation should not contact Hugging Face.
