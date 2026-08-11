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
GEMMA_BIN="${GEMMA_BIN:-$HOME/llama.cpp/build/bin/llama-server}"
GEMMA_MODEL="${GEMMA_MODEL:-$HOME/models/gemma-4-31B-q4/gemma-4-31B_q4_0-it.gguf}"
GEMMA_MMPROJ="${GEMMA_MMPROJ:-$HOME/models/gemma-4-31B-q4/gemma-4-31B-it-mmproj.gguf}"
GEMMA_PORT="${GEMMA_PORT:-8080}"

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

for svc in watchdog r2sync hive sentinel drift gemma serve nexus piper; do stop_one "$svc"; done
sleep 1

# Order matters: the broker must own its socket before the server subscribes,
# and both must be up before the drift loop publishes its first cell.
start_one piper "$VENV/bin/python" scripts/piper_local.py
start_one nexus "$VENV/bin/python" scripts/nexus_local.py
sleep 2

MODEL_DIR="$MODEL_DIR" OUT_DIR="$OUT_DIR" PATH="$VENV/bin:$PATH" \
	start_one serve ./flux serve -addr "$ADDR" -backend cuda -unsafe-no-auth -public-read-only
sleep 4

# Gemma 4 is the local eye. Q4 keeps the full 31B vision model near one quarter
# of an H100 while FLUX retains the larger share. Reasoning is disabled here:
# the gate consumes strict JSON, and spending its response budget on a hidden
# monologue left otherwise sound judgements with an empty final answer.
gemma_ready=0
if [ "${GEMMA:-1}" = "1" ] && [ -x "$GEMMA_BIN" ] && [ -f "$GEMMA_MODEL" ] && [ -f "$GEMMA_MMPROJ" ]; then
	start_one gemma "$GEMMA_BIN" \
		-m "$GEMMA_MODEL" -mm "$GEMMA_MMPROJ" \
		--host 127.0.0.1 --port "$GEMMA_PORT" -ngl 99 -c 4096 -np 1 -fa on \
		--reasoning off --reasoning-format none
	for _ in $(seq 1 45); do
		if curl -fsS --max-time 2 "http://127.0.0.1:$GEMMA_PORT/health" >/dev/null 2>&1; then
			gemma_ready=1
			break
		fi
		sleep 1
	done
	if [ "$gemma_ready" != "1" ]; then echo "gemma   started but not ready"; fi
elif [ "${GEMMA:-1}" = "1" ]; then
	echo "gemma   skipped (binary or model missing)"
fi

if [ "$gemma_ready" = "1" ]; then
	export CHORUS_SECOND_ENGINE="${CHORUS_SECOND_ENGINE:-http://127.0.0.1:$GEMMA_PORT/v1/chat/completions}"
fi

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

# The hive proposes, trials and promotes language changes on evidence. It runs
# on a slower clock than the sentinel: judgement is cheap, changing the
# language is not.
if [ "${HIVE:-1}" = "1" ]; then
	CHORUS_SECOND_ENGINE="${CHORUS_SECOND_ENGINE:-}" \
		start_one hive "$VENV/bin/python" chorus/hive.py \
			--out-dir "$OUT_DIR" --public-base "${CHORUS_PUBLIC_BASE:-}" --interval 1800
fi

# Stream frames off the node as they land. The volume is one crypto-erase from
# taking the whole run with it, and a batch job is a thing that has not run yet.
# Credentials come from the environment; a command line would be recorded.
if [ "${R2SYNC:-1}" = "1" ] && [ -n "${R2_ACCESS_KEY_ID:-}" ]; then
	start_one r2sync "$VENV/bin/python" chorus/r2sync.py --out-dir "$OUT_DIR" --interval 15
elif [ "${R2SYNC:-1}" = "1" ]; then
	echo "r2sync  skipped (no R2_ACCESS_KEY_ID in the environment)"
fi

# A watch that dies unnoticed is not a watch. Nothing here was checking that
# the checkers were alive: the sentinel 524'd for forty minutes and the only
# reason anyone found out was a human reading a log by hand. This restarts a
# dead service and records the death, so absence stops looking like health.
if [ "${WATCHDOG:-1}" = "1" ]; then
	start_one watchdog bash -c '
		while true; do
			for svc in piper nexus serve gemma drift sentinel hive; do
				pidfile="'"$RUN"'/$svc.pid"
				[ -f "$pidfile" ] || continue
				pid=$(cat "$pidfile" 2>/dev/null || echo)
				if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then
					echo "$(date -u +%H:%M:%S) $svc died; restarting stack" \
						>> "$HOME/watchdog.log"
					ARCHIVE_PRIOR=0 bash "$HOME/FLUX/chorus/up.sh" >> "$HOME/watchdog.log" 2>&1
					break
				fi
			done
			sleep 60
		done'
fi

sleep 2
echo "--- health ---"
curl -sS -o /dev/null -w 'landing %{http_code}\n' "http://127.0.0.1:${ADDR##*:}/" || true
curl -sS -o /dev/null -w 'gallery %{http_code}\n' "http://127.0.0.1:${ADDR##*:}/gallery/" || true
curl -sS -o /dev/null -w 'health  %{http_code}\n' "http://127.0.0.1:${ADDR##*:}/api/health" || true
echo "--- running ---"
for svc in piper nexus serve gemma drift sentinel hive r2sync watchdog; do
	pid=$(cat "$RUN/$svc.pid" 2>/dev/null || echo -)
	if [ "$pid" != "-" ] && kill -0 "$pid" 2>/dev/null; then
		printf '%-8s up   (%s)\n' "$svc" "$pid"
	elif pgrep -f "chorus/$svc.py" >/dev/null 2>&1; then
		# Started outside this script, so there is no pid file. Reporting DOWN
		# for a running service invites someone to start a second copy.
		printf '%-8s up   (external)\n' "$svc"
	else
		printf '%-8s DOWN\n' "$svc"
	fi
done
