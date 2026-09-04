#!/bin/bash
set -e
ROOT=/home/ubuntu/CLIs/flux
OUT=/home/ubuntu/models/flux-output
mkdir -p "$OUT/collections/microgreens" "$ROOT/.fluxd"
cd "$ROOT"
exec setsid "$ROOT/.venv/bin/python" -u "$ROOT/protocol_stream.py" \
  --n 256 --steps 28 --depth 2 \
  --prompt "Red Rambo radish microgreens, deep violet-purple cotyledons, plum and amethyst leaves, ruby-magenta stems, soil-grown microgreens only, no people, no hands, extreme macro photography, dew droplets, 100mm macro lens, photorealistic culinary still, Belarro Berlin harvest" \
  --socket "$ROOT/.fluxd/flux-gpu0.sock" \
  --state "$ROOT/.fluxd/protocol_stream_branch_microgreens.json" \
  --lane microgreens \
  --branch microgreens \
  >> "$ROOT/.fluxd/studio_microgreens.log" 2>&1
