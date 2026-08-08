#!/bin/sh
# Bootstrap a givemeanode H100 node into a working FLUX runner.
#
# Runs ON the node, not on the laptop. Idempotent: re-running it on a woken
# node is cheap, so it doubles as the repair path after a stop.
#
# The model lands in ~/models, which flux_paths.py already probes
# (~/models/FLUX.1-dev is in default_model_dir's candidate list), so nothing
# here needs a code change to be found.
set -eu

REPO_DIR="${REPO_DIR:-$HOME/FLUX}"
MODEL_ROOT="${MODEL_ROOT:-$HOME/models}"
OUT_DIR="${OUT_DIR:-$HOME/models/flux-output}"

say() { printf '\n== %s\n' "$1"; }

say "gpu"
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader

say "torch (from the image, not installed here)"
python3 - <<'PY'
import torch
print("torch", torch.__version__, "cuda", torch.version.cuda, "available", torch.cuda.is_available())
if torch.cuda.is_available():
    print("device", torch.cuda.get_device_name(0))
PY

say "flux dependencies"
pip install --no-input --quiet -r "$REPO_DIR/deploy/givemeanode/requirements-node.txt"
python3 - <<'PY'
import diffusers, transformers, accelerate
print("diffusers", diffusers.__version__)
print("transformers", transformers.__version__)
print("accelerate", accelerate.__version__)
PY

say "directories"
mkdir -p "$MODEL_ROOT" "$OUT_DIR"
printf 'models: %s\noutput: %s\n' "$MODEL_ROOT" "$OUT_DIR"

say "resolved paths"
cd "$REPO_DIR"
MODEL_DIR="${MODEL_DIR:-}" OUT_DIR="$OUT_DIR" python3 - <<'PY'
import flux_paths
model = flux_paths.default_model_dir()
print("model dir:", model)
print("valid:", flux_paths.valid_model_dir(model))
print("out dir:", flux_paths.default_out_dir())
PY

say "done"
echo "If 'valid' is False the weights are not down yet; run the model step."
