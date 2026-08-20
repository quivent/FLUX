# Sovereign FLUX Visual Jury & Continuum Provisioning Blueprint

This document provides the exact, reproducible provisioning procedure to stand up the complete **FLUX Visual Generative Continuum, Multi-Model Sovereign Jury, Visual Uniqueness Diff Tracker, and Autonomous Evolutionary Feedback Loop** on single-node Hopper (H100/H200) or multi-GPU environments.

---

## 1. System Topology & VRAM Allocation Matrix (141 GiB SXM5)

| Subsystem / Model | Model HuggingFace ID | Precision | VRAM Fraction | VRAM Target | Port / Socket | Role |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **FLUX.1-dev** | `black-forest-labs/FLUX.1-dev` | BF16 / Auto | Dynamic | ~35.0 GiB | `unix:/root/.fluxd/flux-gpu0.sock` | Primary Resident Generative Engine |
| **Governor (Gemma 31B)** | `redhatai/gemma-4-31b-it-fp8-dynamic` | FP8 Dynamic | **0.40** | ~56.0 GiB | `http://127.0.0.1:8000/v1` | Council Leader, Semantic Auditor & Prompt Mutator |
| **Pixtral 12B** | `mistralai/Pixtral-12B-2409` | FP8 / AWQ | **0.10** | ~14.0 GiB | Ingest Cache / Evaluator | Fine Art Critic (Tonal density, lighting, color theory) |
| **Qwen3-VL 8B** | `Qwen/Qwen3-VL-8B-Instruct` | FP8 Dynamic | **0.08** | ~11.2 GiB | Ingest Cache / Evaluator | Structural Inspector (Anatomy, contours, spatial grounding) |
| **Gemma 12B** | `google/gemma-4-12b-it` | FP8 Dynamic | **0.10** | ~14.0 GiB | Ingest Cache / Evaluator | Multimodal Feature Decoder & Consensus Synthesizer |
| **Reserved / System** | CUDA Context + KV Cache | - | 0.08 | ~11.0 GiB | GPU 0 VRAM Headroom | Dynamic Peak Buffer |

---

## 2. Step-by-Step Provisioning Procedure

### Step 1: System Dependencies & Toolchain
```bash
apt-get update && apt-get install -y git build-essential cmake ninja-build caddy sqlite3 jq
pip install --break-system-packages numpy pillow
```

### Step 2: Ingest Model Weights
```bash
# Model cache ingestion into HuggingFace Hub
python3 -c '
from huggingface_hub import snapshot_download
snapshot_download("Qwen/Qwen3-VL-8B-Instruct")
snapshot_download("mistralai/Pixtral-12B-2409")
snapshot_download("google/gemma-4-12b-it")
'
```

### Step 3: Build and Install FLUX CLI
```bash
cd /root/CLIs/flux
go build -o /root/.local/bin/flux ./cmd/flux
cp /root/.local/bin/flux /usr/local/bin/flux
```

### Step 4: Initialize SQLite State & R2 Sync
```bash
# Initialize local SQLite schema and seed defaults
flux jury

# Push baseline database state to Cloudflare R2
flux jury sync
```

### Step 5: Install & Enable Systemd Supervision
```bash
systemctl daemon-reload
systemctl enable --now flux-studio.service
systemctl enable --now flux-jury-evaluator.service
systemctl enable --now flux-perpetual-feeder.service
```

### Step 6: Configure Caddy Web Routing
Ensure `/etc/caddy/conf.d/gemstone_motion_influx_vision.caddy` routes:
```caddy
motion.influx.vision {
	encode zstd gzip
	handle {
		reverse_proxy 127.0.0.1:7861 {
			flush_interval -1
		}
	}
}
```
Reload Caddy:
```bash
systemctl reload caddy
```

---

## 3. Tier Stratification & Curving Architecture

* **👑 Opus Masterpiece Tier**: Top 2.0% ($P \ge 98.0^{\text{th}}$ percentile) · Display Score $98.0–100.0$.
* **✨ Spectacle Tier**: Top 10.0% ($P \ge 90.0^{\text{th}}$ percentile) · Display Score $90.0–97.9$.
* **Standard Frame**: $40.0^{\text{th}} - 89.9^{\text{th}}$ percentile · Display Score $65.0–89.9$.
* **Banal / Redundant**: $< 40.0^{\text{th}}$ percentile · Display Score $< 65.0$.

---

## 4. Visual Uniqueness Diff Tracker
The perceptual differential model (`uniqueness_tracker.py`) extracts a 128-dimensional multi-scale vector (chromatic moments, 2D Fourier spatial frequency, and gradient direction histograms) and calculates distance against the rolling historical cluster:
* **$U \ge 75.0\%$**: `BREAKTHROUGH_ORIGINAL` ($+8.0$ pt bonus).
* **$U < 35.0\%$**: `REDUNDANT_CLUSTER` ($-18.0$ pt penalty + triggers Governor Orthogonal Jump).
