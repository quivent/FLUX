#!/usr/bin/env bash
set -euo pipefail

# Small, dependency-free process wrapper for the Beauty Protocol.
#
# The FLUX worker is deliberately separate from the feedback processes.  This
# lets the evaluator stay alive while a GPU is reserved by another tenant, and
# prevents an accidental second FLUX load from taking down Qwen/Gemma.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${BEAUTY_PYTHON:-$ROOT/.venv/bin/python}"
MODEL_DIR="${BEAUTY_MODEL_DIR:-/home/dev/models/flux1}"
OUT_DIR="${BEAUTY_OUT_DIR:-/home/dev/Models/flux-output}"
FLUXD_DIR="$ROOT/.fluxd"
WORKER_SOCK="${BEAUTY_WORKER_SOCKET:-$FLUXD_DIR/flux.sock}"
WORKER_PID="$FLUXD_DIR/worker.pid"
JURY_PID="$FLUXD_DIR/jury_evaluator.pid"
PIPELINE_PID="$FLUXD_DIR/beauty_pipeline.pid"
JOBS_LEDGER="${BEAUTY_JOBS_LEDGER:-$FLUXD_DIR/jobs.jsonl}"
MIN_FREE_MIB="${BEAUTY_MIN_FREE_MIB:-45000}"

mkdir -p "$FLUXD_DIR" "$OUT_DIR"

log() { printf '[beauty-stack] %s\n' "$*"; }
alive() { [[ "$1" =~ ^[0-9]+$ ]] && kill -0 "$1" 2>/dev/null; }
pid_from() { [[ -f "$1" ]] && tr -d '[:space:]' < "$1" || true; }

worker_live() {
  [[ -S "$WORKER_SOCK" ]] || return 1
  "$PYTHON" - "$WORKER_SOCK" <<'PY'
import json, socket, sys
try:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        s.connect(sys.argv[1])
        s.sendall(b'{"op":"ping"}\n')
        line = s.makefile().readline()
        data = json.loads(line)
        raise SystemExit(0 if data.get("ok") else 1)
except Exception:
    raise SystemExit(1)
PY
}

gpu_report() {
  if command -v nvidia-smi >/dev/null 2>&1; then
    nvidia-smi --query-gpu=index,name,memory.used,memory.free,memory.total --format=csv,noheader,nounits 2>/dev/null || true
  else
    log "nvidia-smi unavailable; GPU guard cannot verify free memory"
  fi
}

gpu_safe_for_flux() {
  command -v nvidia-smi >/dev/null 2>&1 || return 1
  local free used total
  IFS=',' read -r _ used free total < <(nvidia-smi --query-gpu=index,memory.used,memory.free,memory.total --format=csv,noheader,nounits | head -1)
  used="${used//[[:space:]]/}"
  free="${free//[[:space:]]/}"
  total="${total//[[:space:]]/}"
  [[ "${free:-0}" =~ ^[0-9]+$ ]] || return 1
  (( free >= MIN_FREE_MIB )) || {
    log "refusing FLUX start: only ${free} MiB free; need ${MIN_FREE_MIB} MiB"
    log "a resident language model is probably occupying this GPU"
    return 1
  }
}

start_worker() {
  if worker_live; then
    log "FLUX worker already live at $WORKER_SOCK"
    return 0
  fi
  gpu_safe_for_flux || return 2
  [[ -x "$PYTHON" ]] || { log "missing Python: $PYTHON"; return 1; }
  [[ -d "$MODEL_DIR" ]] || { log "missing FLUX model: $MODEL_DIR"; return 1; }
  log "starting resident FLUX worker (preload enabled)"
  nohup "$PYTHON" -u "$ROOT/worker.py" \
    --socket "$WORKER_SOCK" \
    --state "$JOBS_LEDGER" \
    --profile "$FLUXD_DIR/profile.json" \
    --model-dir "$MODEL_DIR" \
    --out-dir "$OUT_DIR" \
    --backend cuda --preload \
    >> "$FLUXD_DIR/worker.log" 2>&1 &
  local pid=$!
  printf '%s\n' "$pid" > "$WORKER_PID"
  for _ in $(seq 1 90); do
    worker_live && { log "FLUX worker ready (pid $pid)"; return 0; }
    alive "$pid" || { log "worker exited; see $FLUXD_DIR/worker.log"; return 1; }
    sleep 1
  done
  log "worker did not become ready; see $FLUXD_DIR/worker.log"
  return 1
}

start_jury() {
  local pid
  pid="$(pid_from "$JURY_PID")"
  if alive "$pid"; then log "jury evaluator already live (pid $pid)"; return 0; fi
  log "starting feedback/jury daemon"
  MOJ_OUTPUT_DIR="$OUT_DIR" FLUX_OUTPUT_DIR="$OUT_DIR" \
  MOJ_JOBS_LEDGER="$JOBS_LEDGER" FLUX_JOBS_LEDGER="$JOBS_LEDGER" \
  nohup "$PYTHON" -u "$ROOT/moj_evaluator.py" --serve \
    >> "$FLUXD_DIR/jury_evaluator.log" 2>&1 &
  printf '%s\n' "$!" > "$JURY_PID"
}

start_pipeline() {
  local pid
  pid="$(pid_from "$PIPELINE_PID")"
  if alive "$pid"; then log "Beauty coordinator already live (pid $pid)"; return 0; fi
  worker_live || { log "cannot start coordinator until the FLUX worker is live"; return 2; }
  log "starting feedback-to-next-prompt coordinator"
  OUT_DIR="$OUT_DIR" FLUX_OUTPUT_DIR="$OUT_DIR" \
  nohup "$PYTHON" -u "$ROOT/beauty_pipeline.py" \
    --socket "$WORKER_SOCK" --state "$FLUXD_DIR/protocol_stream_gpu3.json" \
    --pid "$PIPELINE_PID" --steps "${BEAUTY_STEPS:-18}" \
    >> "$FLUXD_DIR/beauty_pipeline.log" 2>&1 &
  printf '%s\n' "$!" > "$PIPELINE_PID"
}

stop_one() {
  local label="$1" file="$2" pid
  pid="$(pid_from "$file")"
  if alive "$pid"; then kill -TERM "$pid"; log "stopped $label (pid $pid)"; else log "$label is not running"; fi
}

status() {
  printf 'FLUX worker: '
  if worker_live; then echo "live ($WORKER_SOCK)"; else echo "down"; fi
  printf 'jury daemon: '
  alive "$(pid_from "$JURY_PID")" && echo "live (pid $(pid_from "$JURY_PID"))" || echo "down"
  printf 'coordinator: '
  alive "$(pid_from "$PIPELINE_PID")" && echo "live (pid $(pid_from "$PIPELINE_PID"))" || echo "down"
  echo 'GPU:'
  gpu_report
}

case "${1:-status}" in
  start-worker) start_worker ;;
  start-feedback) start_jury ;;
  start-pipeline) start_pipeline ;;
  start) start_jury; start_worker; start_pipeline ;;
  stop) stop_one coordinator "$PIPELINE_PID"; stop_one jury "$JURY_PID"; stop_one worker "$WORKER_PID" ;;
  status) status ;;
  gpu) gpu_report ;;
  *) echo "usage: $0 {start|start-worker|start-feedback|start-pipeline|stop|status|gpu}" >&2; exit 2 ;;
esac
