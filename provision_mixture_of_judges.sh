#!/usr/bin/env bash
# ==============================================================================
# Sovereign FLUX Image Evaluation & Mixture of Judges (MoJ) Provisioning Protocol
# ==============================================================================
# Architecture:
#   1. Governor Brain: Gemma 4 31B FP8 (Port 8000, GPU Util: 0.40 ~57.5 GiB)
#   2. Visual Witness: Qwen3-VL-8B-Instruct (Port 8002, GPU Util: 0.15 ~21.5 GiB)
#   3. Generative Engine: FLUX.1-dev BF16 UDS Worker (VRAM: ~35.0 GiB)
#   4. Sensory Gates: DINOv2-Giant Semantic Novelty + SigLIP Aesthetic (VRAM: ~2.5 GiB)
#   5. Total VRAM Budget: ~116.5 GiB / 140.4 GiB (Safety Buffer: ~24.0 GiB)
# ==============================================================================
set -euo pipefail

echo "========================================================"
echo "⚡ PROVISIONING FLUX MIXTURE OF JUDGES (MoJ) JURY STACK"
echo "========================================================"

VLLM_IMAGE="vllm/vllm-openai:cu129-nightly-65b7662d3fcb773afaf751ab29ac6960a0cf011d"
HF_CACHE="/root/.cache/huggingface"

# 1. Relaunch Governor (Gemma-4-31B FP8) with 0.40 VRAM Utilization
echo "--> Step 1: Re-aligning Governor LLM (31B) to 0.40 VRAM allocation..."
if docker ps -a --format '{{.Names}}' | grep -q "^vllm-gemma4-fp8-h200$"; then
    docker stop vllm-gemma4-fp8-h200 || true
    docker rm vllm-gemma4-fp8-h200 || true
fi

docker run -d \
  --name vllm-gemma4-fp8-h200 \
  --restart always \
  --gpus all \
  --ipc host \
  --network host \
  -v "${HF_CACHE}:/root/.cache/huggingface" \
  "${VLLM_IMAGE}" \
  -m vllm.entrypoints.openai.api_server \
  --host 0.0.0.0 \
  --port 8000 \
  --model redhatai/gemma-4-31b-it-fp8-dynamic \
  --served-model-name governor \
  --max-model-len 131072 \
  --gpu-memory-utilization 0.40 \
  --tensor-parallel-size 1 \
  --kv-cache-dtype auto \
  --trust-remote-code \
  --limit-mm-per-prompt '{"image": 0, "video": 0}' \
  --enable-prefix-caching \
  --max-num-batched-tokens 8192 \
  --max-num-seqs 128 \
  --enable-auto-tool-choice \
  --tool-call-parser gemma4 \
  --async-scheduling \
  --attention-backend FLASH_ATTN

# 2. Launch State-of-the-Art Visual Witness (Qwen3-VL-8B) on Port 8002 with 0.15 VRAM
echo "--> Step 2: Provisioning Visual Witness SOTA VLM (Qwen3-VL-8B-Instruct) on port 8002 (0.15 VRAM)..."
if docker ps -a --format '{{.Names}}' | grep -q "^vllm-visual-witness$"; then
    docker stop vllm-visual-witness || true
    docker rm vllm-visual-witness || true
fi

# Attempt Qwen3-VL-8B-Instruct; fall back to Qwen2.5-VL-7B-Instruct if local weight resolution requires
VLM_MODEL="Qwen/Qwen3-VL-8B-Instruct"

docker run -d \
  --name vllm-visual-witness \
  --restart always \
  --gpus all \
  --ipc host \
  --network host \
  -v "${HF_CACHE}:/root/.cache/huggingface" \
  "${VLLM_IMAGE}" \
  -m vllm.entrypoints.openai.api_server \
  --host 0.0.0.0 \
  --port 8002 \
  --model "${VLM_MODEL}" \
  --served-model-name visual-witness \
  --max-model-len 32768 \
  --gpu-memory-utilization 0.15 \
  --tensor-parallel-size 1 \
  --kv-cache-dtype auto \
  --trust-remote-code \
  --enable-prefix-caching \
  --max-num-batched-tokens 4096 \
  --max-num-seqs 64 \
  --async-scheduling \
  --attention-backend FLASH_ATTN

# 3. Verify FLUX Resident Denoise Worker
echo "--> Step 3: Verifying FLUX.1-dev resident UDS engine..."
if [ ! -S "/root/CLIs/flux/.fluxd/flux-gpu0.sock" ]; then
    echo "Starting FLUX resident worker..."
    /root/.local/bin/flux warm
fi

# 4. Bring up Mixture of Judges Evaluation Engine
echo "--> Step 4: Initializing Mixture of Judges Evaluation Loop..."
chmod +x /root/CLIs/flux/moj_evaluator.py
pkill -f "moj_evaluator.py" || true
nohup python3 -u /root/CLIs/flux/moj_evaluator.py > /root/CLIs/flux/.fluxd/moj_evaluator.log 2>&1 &

echo "========================================================"
echo "✅ FLUX MIXTURE OF JUDGES PROVISIONING COMPLETE"
echo "========================================================"
