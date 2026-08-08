#!/bin/bash
# Re-point the public domain at the node after a stop/wake.
#
# A givemeanode endpoint is bound to the node's current container: stopping the
# node kills it, and waking never resurrects it. The capability URL also
# expires about a day after minting. Either way the Cloudflare Worker that
# fronts the domain is left holding a dead origin, and the site 404s while the
# node itself is perfectly healthy -- which reads as "the gallery is broken".
#
# So this is not a one-time setup step. It runs after every wake.
#
#   ./chorus/publish.sh                     # expose 7861 and re-point tea
#   HOST=other.influx.vision ./chorus/publish.sh
set -eu

NODE="${NODE:-flux-worker}"
PORT="${PORT:-7861}"
HOST="${HOST:-tea.influx.vision}"
SCRIPT="${SCRIPT:-tea-gateway}"
TOKEN_FILE="${TOKEN_FILE:-$HOME/.gemstone/tea-gateway-token}"
GEMSTONE="${GEMSTONE:-gemstone}"

# The origin is minted here rather than by gemstone, because gemstone's own
# provider token is a short-lived user credential whose refresh fails often;
# --origin-url lets a URL obtained any other way front the domain instead.
echo "== exposing $NODE:$PORT"
url=$(gman api POST "/preview/nodes/$NODE/endpoints" "{\"port\":$PORT}" 2>/dev/null \
	| python3 -c 'import sys,json;print(json.load(sys.stdin).get("url",""))' 2>/dev/null || true)

if [ -z "${url:-}" ]; then
	cat >&2 <<'MSG'
Could not mint an endpoint with the gman CLI (its token expires hourly and the
refresh can fail outright). Get the URL from the MCP surface instead --
expose_port(name, port) -- and pass it in:

    ORIGIN_URL=https://ept-....givemeanode.io ./chorus/publish.sh
MSG
	[ -n "${ORIGIN_URL:-}" ] || exit 1
	url="$ORIGIN_URL"
fi

echo "== origin $url"
exec "$GEMSTONE" domains gateway publish \
	--node "$NODE" --port "$PORT" \
	--host "$HOST" --script "$SCRIPT" \
	--token-file "$TOKEN_FILE" \
	--open --verify-path /atelier/ \
	--origin-url "$url"
