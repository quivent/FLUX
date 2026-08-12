#!/bin/bash
# Replace only the queue execution authority after an orchestration upgrade.
# Piper, the public server, Sentinel and the R2 durability lane remain live.
set -euo pipefail

REPO="${REPO:-$HOME/FLUX}"
VENV="${VENV:-$HOME/.venv}"
OUT_DIR="${OUT_DIR:-$HOME/models/flux-output}"
RUN="${RUN:-$HOME/.flux-run}"
mkdir -p "$RUN"
cd "$REPO"

stop_one() {
	local name="$1" pidfile="$RUN/$1.pid" pid
	pid=$(cat "$pidfile" 2>/dev/null || true)
	if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
		kill "$pid" 2>/dev/null || true
		for _ in $(seq 1 40); do
			kill -0 "$pid" 2>/dev/null || break
			sleep 0.25
		done
		kill -9 "$pid" 2>/dev/null || true
	fi
	rm -f "$pidfile"
}

start_one() {
	local name="$1"; shift
	nohup "$@" > "$HOME/$name.log" 2>&1 &
	echo $! > "$RUN/$name.pid"
}

# Silence the component that issued destructive full-stack recovery first.
for svc in hive queue-auditor queue-supervisor night-run nexus; do stop_one "$svc"; done

start_one nexus "$VENV/bin/python" scripts/nexus_local.py
for _ in $(seq 1 20); do
	"$VENV/bin/python" - <<'PY' >/dev/null 2>&1 && break || true
import json, socket
with socket.create_connection(("127.0.0.1", 9999), timeout=1) as c:
    c.sendall(b'{"type":"health"}\n'); c.shutdown(socket.SHUT_WR)
    assert json.loads(c.makefile().readline())["ok"]
PY
	sleep 0.25
done

start_one night-run "$VENV/bin/python" chorus/night_runner.py \
	--manifest "$REPO/chorus/night-run.json" --python "$VENV/bin/python" --run-dir "$RUN"
start_one queue-supervisor "$VENV/bin/python" chorus/queue_guardian.py \
	--role supervisor --root "$REPO" --out-dir "$OUT_DIR" --run-dir "$RUN" \
	--python "$VENV/bin/python"
start_one queue-auditor "$VENV/bin/python" chorus/queue_guardian.py \
	--role auditor --root "$REPO" --out-dir "$OUT_DIR" --run-dir "$RUN" \
	--python "$VENV/bin/python"

# These switches describe the actual deployed topology. Hive observes it; it
# must not reinterpret intentionally absent services as reasons to kill FLUX.
start_one hive env GEMMA=0 DRIFT=0 CHORUS_SECOND_ENGINE='' \
	"$VENV/bin/python" chorus/hive.py \
		--out-dir "$OUT_DIR" --public-base "${CHORUS_PUBLIC_BASE:-https://tea.influx.vision}" \
		--interval 180 --run-dir "$RUN"

sleep 2
for svc in nexus night-run queue-supervisor queue-auditor hive; do
	pid=$(cat "$RUN/$svc.pid" 2>/dev/null || true)
	if [ -z "$pid" ] || ! kill -0 "$pid" 2>/dev/null; then
		echo "$svc failed to start" >&2
		exit 1
	fi
	printf '%-18s up (%s)\n' "$svc" "$pid"
done
