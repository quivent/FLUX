#!/bin/bash
# Chorus — bring up the whole suite on a node: piper broker, nexus authority,
# the HTTP server that fans assets to the gallery, and the generating loop.
#
# This exists as a FILE rather than an inline command for a specific reason:
# `pkill -f piper_local` matches the very shell that is about to launch
# piper_local, because the launch line puts that name on the same command line.
# Running from a script keeps the caller's command line free of the names being
# matched. PID files then make the kills exact instead of pattern-matched.
#
# Idempotent: run it again to restart the stack in place.
set -u

REPO="${REPO:-$HOME/FLUX}"
VENV="${VENV:-$HOME/.venv}"
MODEL_DIR="${MODEL_DIR:-$HOME/models/FLUX.1-dev}"
OUT_DIR="${OUT_DIR:-$HOME/models/flux-output}"
RUN="${RUN:-$HOME/.flux-run}"
ADDR="${ADDR:-0.0.0.0:7861}"
DRIFT="${DRIFT:-1}"

ARCHIVE="${ARCHIVE:-$HOME/models/flux-archive}"
mkdir -p "$RUN" "$OUT_DIR" "$ARCHIVE"
cd "$REPO"

# Earlier versions archived the previous run's frames out of the served
# directory on every start. With eight restarts in an evening that silently
# emptied the wall each deploy, and the operator asked where their work had
# gone. The wall is the whole body of work; the gallery already leads with the
# newest, so old frames sink rather than intrude. Archiving is now explicit:
#   ARCHIVE_PRIOR=1 bash chorus/up.sh
if [ "${ARCHIVE_PRIOR:-0}" = "1" ]; then
	prior=$(ls "$OUT_DIR"/*.png 2>/dev/null | wc -l | tr -d " ")
	if [ "${prior:-0}" -gt 0 ]; then
		stamp=$(date -u +%Y%m%dT%H%M%SZ)
		mkdir -p "$ARCHIVE/$stamp"
		mv "$OUT_DIR"/*.png "$ARCHIVE/$stamp/" 2>/dev/null || true
		echo "archived $prior frame(s) to $ARCHIVE/$stamp"
	fi
fi

stop_one() { # name
	local pidfile="$RUN/$1.pid"
	[ -f "$pidfile" ] || return 0
	local pid
	pid=$(cat "$pidfile" 2>/dev/null || true)
	# Signal by recorded pid only. A pattern kill here would match this script.
	if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
		kill "$pid" 2>/dev/null || true
		for _ in 1 2 3 4 5 6 7 8 9 10; do
			kill -0 "$pid" 2>/dev/null || break
			sleep 0.5
		done
		kill -9 "$pid" 2>/dev/null || true
	fi
	rm -f "$pidfile"
}

start_one() { # name, command...
	local name="$1"; shift
	nohup "$@" > "$HOME/$name.log" 2>&1 &
	echo $! > "$RUN/$name.pid"
	printf '%-8s pid %s\n' "$name" "$(cat "$RUN/$name.pid")"
}

for svc in sentinel drift serve nexus piper; do stop_one "$svc"; done
sleep 1

# Order matters: the broker must own its socket before the server subscribes,
# and both must be up before the drift loop publishes its first cell.
start_one piper "$VENV/bin/python" scripts/piper_local.py
start_one nexus "$VENV/bin/python" scripts/nexus_local.py
sleep 2

MODEL_DIR="$MODEL_DIR" OUT_DIR="$OUT_DIR" PATH="$VENV/bin:$PATH" \
	start_one serve ./flux serve -addr "$ADDR" -backend cuda -unsafe-no-auth -public-read-only
sleep 4

if [ "$DRIFT" = "1" ]; then
	MODEL_DIR="$MODEL_DIR" OUT_DIR="$OUT_DIR" \
		start_one drift "$VENV/bin/python" chorus/loop.py \
			--out-dir "$OUT_DIR" --model-dir "$MODEL_DIR"
fi

# The gate runs beside the loop, not on request. An unjudged run is the
# failure mode this whole suite was rebuilt to escape.
if [ "${SENTINEL:-1}" = "1" ]; then
	start_one sentinel "$VENV/bin/python" chorus/sentinel.py --out-dir "$OUT_DIR" --interval 600
fi

sleep 2
echo "--- health ---"
curl -sS -o /dev/null -w 'atelier %{http_code}\n' "http://127.0.0.1:${ADDR##*:}/atelier/" || true
curl -sS -o /dev/null -w 'health  %{http_code}\n' "http://127.0.0.1:${ADDR##*:}/api/health" || true
echo "--- running ---"
for svc in piper nexus serve drift sentinel; do
	pid=$(cat "$RUN/$svc.pid" 2>/dev/null || echo -)
	if [ "$pid" != "-" ] && kill -0 "$pid" 2>/dev/null; then
		printf '%-8s up   (%s)\n' "$svc" "$pid"
	else
		printf '%-8s DOWN\n' "$svc"
	fi
done
