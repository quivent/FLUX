#!/usr/bin/env bash
# PID 1 for the beauty studies stack.
#
# Adds the preflight the shipped container lacked: it refuses to start an
# over-subscribed stack instead of letting the FLUX BF16 worker OOM behind
# three resident vLLM servers.
set -euo pipefail

echo "================================================================================"
echo "  BEAUTY STUDIES STACK"
echo "================================================================================"

mkdir -p /var/log /var/run /root/Models/flux-output /root/renders "${HF_HOME:-/models/hf}"

# ── VRAM preflight ───────────────────────────────────────────────────────────
total_mib="$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1 || true)"
if [ -z "$total_mib" ]; then
    echo "!! no GPU visible (nvidia-smi returned nothing)." >&2
    echo "   run with --gpus all, or set BEAUTY_SKIP_PREFLIGHT=1 to continue anyway." >&2
    [ "${BEAUTY_SKIP_PREFLIGHT:-0}" = "1" ] || exit 1
else
    gpu_name="$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)"
    python3 - <<PY || exit 1
import os, sys

total_gib = ${total_mib} / 1024.0
util = sum(float(os.environ.get(k, "0") or 0) for k in
           ("BEAUTY_GOVERNOR_UTIL", "BEAUTY_CODER_UTIL", "BEAUTY_VISION_UTIL"))
reserve = float(os.environ.get("BEAUTY_FLUX_RESERVE_GIB", "35") or 35)

claimed = util * total_gib
free = total_gib - claimed

print(f"  gpu            ${gpu_name} ({total_gib:.1f} GiB)")
print(f"  vllm fractions {util:.2f} -> {claimed:.1f} GiB")
print(f"  flux reserve   {reserve:.1f} GiB")
print(f"  headroom       {free - reserve:+.1f} GiB")

if free < reserve:
    print()
    print(f"!! over-subscribed: vLLM claims {claimed:.1f} GiB of {total_gib:.1f} GiB,")
    print(f"   leaving {free:.1f} GiB for a FLUX worker that needs {reserve:.1f} GiB.")
    print( "   lower BEAUTY_GOVERNOR_UTIL, or disable the aux models by setting")
    print( "   BEAUTY_CODER_UTIL=0 and BEAUTY_VISION_UTIL=0.")
    print( "   set BEAUTY_SKIP_PREFLIGHT=1 to start anyway.")
    sys.exit(0 if os.environ.get("BEAUTY_SKIP_PREFLIGHT") == "1" else 1)
PY
fi

# ── Model cache ──────────────────────────────────────────────────────────────
if [ "${BEAUTY_MODELS_WARM:-0}" = "1" ]; then
    echo "  models         baked into image at ${HF_HOME}"
elif [ -n "$(ls -A "${HF_HOME:-/models/hf}" 2>/dev/null)" ]; then
    echo "  models         mounted cache at ${HF_HOME}"
else
    echo "  models         COLD — first boot will download from HuggingFace."
    echo "                 mount a cache with -v hf-cache:${HF_HOME} so this"
    echo "                 happens once, not once per container."
fi

render-supervisord
echo "--------------------------------------------------------------------------------"

supervisord -c /etc/supervisor/conf.d/supervisord.conf
trap 'supervisorctl shutdown; exit 0' SIGTERM SIGINT

tail -F /var/log/supervisord.log /var/log/governor-vllm.log \
        /var/log/governor-gateway.log /var/log/flux-tea.log 2>/dev/null
