<div align="center">

```
 ___ _    _   _  __  _ 
| __| |  | | | |\ \/ /
| _|| |__| |_| | >  < 
|_| |____|\___/ /_/\_\
```

**FLUX.1-dev BF16 Local Runner**

*A minimal Python runner & colored Go CLI for the local BF16 Diffusers-format model.*

![Go](https://img.shields.io/badge/Go-00ADD8?style=for-the-badge&logo=go&logoColor=white)
![Python](https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white)
![macOS](https://img.shields.io/badge/macOS-000000?style=for-the-badge&logo=apple&logoColor=white)

</div>

---

## 📑 Table of Contents

- [⚡ Overview](#-overview)
- [📦 Installation](#-installation)
- [🚀 Usage](#-usage)
- [🔧 Architecture & Workers](#-architecture--workers)
- [📖 HTTP Server & API](#-http-server--api)
- [🤝 Apple Neural Engine (ANE)](#-apple-neural-engine-ane)
- [📄 Setup & Generation](#-setup--generation)

---

## ⚡ Overview

This repository provides a minimal Python runner for the local BF16 Diffusers-format model (FLUX.1-dev). It comes paired with a high-performance **Go CLI** (`flux`) which acts as a colored local control surface over the lean Python runner. 

> [!NOTE]
> It does not download model files or contact Hugging Face during generation by default, prioritizing local offline workflows.

---

## 📦 Installation

Build and install the local command:

```zsh
cd /Users/joshkornreich/FLUX
make flux
```

Once installed, use the CLI for various controls:

```zsh
flux doctor
flux accel
flux atlas motion
flux jobs
```

---

## 🚀 Usage

The Go CLI provides a comprehensive command set:

- `studio`: runtime posture and preset lanes.
- `gpu`: NVIDIA/Torch GPU state and active compute process view.
- `accel`: current acceleration stack and candidate backend availability.
- `bench`: socket benchmark for concrete backends; updates `.fluxd/profile.json` for `backend=auto` selection.
- `tree`: command topology in Council-style branches.
- `colors`: palette and state-color sample.
- `download`: prints the lean `hf download` command for BF16 Diffusers files.
- `preset`: named render lanes such as `sketch`, `hero`, `object`, `space`, `cover`, `future`, `anime`, and `noir`.
- `shape`: prompt composition without generation.
- `spark`: six fast prompt mutations for a subject.
- `muse`: a shot board that turns a subject into concrete local or remote render commands.
- `plan`: exact local command preview.
- `burst`: multiple seed variants with one command.
- `atlas motion`: install prerequisites and open the dedicated Motion Atlas Sphere web suite paths, geometry, quality, traversal, and cross-frame cache settings.
- `serve`: local HTTP API and dashboard backed by the Unix socket worker.
- `gallery`: museum-style live gallery backed by the same server and event streams.
- `tea`: setup, validate, and serve the isolated Tea garden and motion gallery in `apps/tea`.
- `apps/rosarium`: recovered Rosarium museum, FLUX production lineage,
  motion works, and local catalog of 7,218 available works.
- `render --async`: queue jobs on the worker.
- `jobs`: inspect queued, running, finished, or failed jobs.
- `install`: symlink `flux` into `~/.local/bin/flux`.

---

## 🔧 Architecture & Workers

### Warm Worker And Jobs

Direct renders load the model, render, then exit. For repeated work, start the worker so the model can stay resident:

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

> [!TIP]
> For a lightweight worker test that does not preload the 32 GB model, run `flux warm --preload=false`.

---

## 📖 HTTP Server & API

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

<details>
<summary>Remote Access & Security</summary>

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

The HTTP server starts the Unix socket worker on first real render or when calling `POST /api/warm`.
</details>

---

## 🤝 Apple Neural Engine (ANE)

The current runner does not use the Apple Neural Engine for full FLUX generation yet. The dedicated `ane` backend is wired as a strict adapter contract, becoming selectable only when `model/ane/registry.json` contains a validated full-pipeline package.

> [!IMPORTANT]
> `mps` and `mlx` are GPU/Metal paths; `coreml` is a scaffold for fixed-shape compiled packages and does not prove ANE execution by itself until validated via Instruments profiling.

First build command (converts the fixed-shape VAE decoder):
```zsh
flux ane init
flux ane convert-vae --width 1024 --height 1024
flux ane direct-capture --width 1024 --height 1024 --block-type dual --block-index 0
flux ane probe
```

Validation & Further Testing:
```zsh
flux ane validate --name PACKAGE_NAME --notes "Instruments run ..."
flux ane direct-pack --manifest /Users/joshkornreich/Models/flux1/ane/direct/dual_block_0_1024x1024.json
```

---

## 📄 Setup & Generation

### Setup

```zsh
cd /Users/joshkornreich/FLUX
make setup
make check
```

### Generate (via Make)

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

### Direct Python (Offline)

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
