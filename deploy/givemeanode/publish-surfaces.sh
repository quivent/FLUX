#!/bin/bash
# Publish the two lightweight FLUX browser surfaces from one read-only origin.
#
# Tea deliberately is not part of this deployment: tea.influx.vision belongs
# to flux-worker and keeps its independent Worker, token, and lifecycle.
# GiveMeANode capability URLs expire and rotate on stop/wake, so run this after
# either event (or pass ORIGIN_URL from expose_port when gman auth is stale).
set -eu

NODE="${NODE:-anime-productions}"
PORT="${PORT:-7861}"
GEMSTONE="${GEMSTONE:-gemstone}"
ORIGIN_URL="${ORIGIN_URL:-}"

if [ -z "$ORIGIN_URL" ]; then
	ORIGIN_URL=$(gman api POST "/preview/nodes/$NODE/endpoints" "{\"port\":$PORT}" 2>/dev/null \
		| python3 -c 'import json,sys; print(json.load(sys.stdin).get("url", ""))' 2>/dev/null || true)
fi
if [ -z "$ORIGIN_URL" ]; then
	echo "No origin URL. Run expose_port for $NODE:$PORT and pass ORIGIN_URL." >&2
	exit 1
fi

publish() {
	host="$1"
	script="$2"
	token_file="$3"
	landing="$4"

	"$GEMSTONE" domains gateway publish \
		--node "$NODE" --port "$PORT" --origin-url "$ORIGIN_URL" \
		--host "$host" --script "$script" --token-file "$token_file" \
		--open --landing-path "$landing" --verify-path "$landing"
}

publish \
	atelier-flux.influx.vision \
	atelier-flux-gateway \
	"$HOME/.gemstone/atelier-flux-gateway-token" \
	/gallery/

publish \
	motion-atlas.influx.vision \
	motion-atlas-gateway \
	"$HOME/.gemstone/motion-atlas-gateway-token" \
	/motion-atlas/
