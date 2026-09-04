#!/bin/bash
# Rolling preserve: hive discourses, stills, jury state, training → R2 ledger + index.
set -euo pipefail
ROOT=/home/ubuntu/CLIs/flux
mkdir -p "$ROOT/.fluxd"
PIDF="$ROOT/.fluxd/tea_ledger.pid"
if [ -f "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null; then
  echo "tea_ledger already running pid=$(cat "$PIDF")"
  exit 0
fi
export HOME=/home/ubuntu
export FLUX_OUTPUT_DIR="${FLUX_OUTPUT_DIR:-/home/ubuntu/models/flux-output}"
export PATH="/home/ubuntu/.local/bin:/usr/local/bin:$PATH"
cd "$ROOT"
nohup python3 -u "$ROOT/scripts/tea_ledger.py" --serve \
  >>"$ROOT/.fluxd/tea_ledger.log" 2>&1 &
echo $! >"$PIDF"
echo "tea_ledger pid=$(cat "$PIDF") log=$ROOT/.fluxd/tea_ledger.log"
