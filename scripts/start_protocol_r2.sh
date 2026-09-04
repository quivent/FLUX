#!/bin/bash
# Stream settled protocol-*.png frames from FLUX_OUTPUT_DIR to R2 (governor/outputs/...).
set -euo pipefail
ROOT=/home/ubuntu/CLIs/flux
mkdir -p "$ROOT/.fluxd"
PIDF="$ROOT/.fluxd/protocol_r2_stream.pid"
if [ -f "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null; then
  echo "protocol_r2_stream already running pid=$(cat "$PIDF")"
  exit 0
fi
export HOME=/home/ubuntu
export FLUX_OUTPUT_DIR="${FLUX_OUTPUT_DIR:-/home/ubuntu/models/flux-output}"
export PATH="/home/ubuntu/.local/bin:/usr/local/bin:$PATH"
cd "$ROOT"
nohup "$ROOT/.venv/bin/python" -u "$ROOT/protocol_r2_stream.py" \
  >>"$ROOT/.fluxd/protocol_r2_stream.log" 2>&1 &
echo $! >"$PIDF"
echo "protocol_r2_stream pid=$(cat "$PIDF") log=$ROOT/.fluxd/protocol_r2_stream.log"
