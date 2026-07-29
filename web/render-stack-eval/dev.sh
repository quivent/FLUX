#!/usr/bin/env bash
# Starts all three motion-atlas comparison implementations in the background.
set -euo pipefail
cd "$(dirname "$0")"

JINJA_PORT="${JINJA_PORT:-9201}"
GO_PORT="${GO_PORT:-9202}"
RUST_PORT="${RUST_PORT:-9203}"

mkdir -p .run

nohup ./go_html_template/server \
  > .run/go.log 2>&1 &
echo $! > .run/go.pid

nohup ./rust_axum/target/release/rust_axum \
  > .run/rust.log 2>&1 &
echo $! > .run/rust.pid

nohup ./jinja_fastapi/.venv/bin/uvicorn main:app --app-dir jinja_fastapi \
  --host 127.0.0.1 --port "$JINJA_PORT" \
  > .run/jinja.log 2>&1 &
echo $! > .run/jinja.pid

sleep 1

echo "Comparison harness running:"
echo "  Go    http://127.0.0.1:${GO_PORT}/    (also /optics /queue /registry /governor /visionary)"
echo "  Rust  http://127.0.0.1:${RUST_PORT}/  (same routes)"
echo "  Jinja http://127.0.0.1:${JINJA_PORT}/ (same routes)"
echo "Logs: render-stack-eval/.run/{go,rust,jinja}.log"
echo "Stop with: make stop"
