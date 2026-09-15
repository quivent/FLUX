#!/usr/bin/env bash
# Beauty Protocol Suite — one-command installer.
#
#   ./bootstrap.sh                    install into ./  (the bundle dir) and verify
#   ./bootstrap.sh --prefix DIR       install into DIR
#   ./bootstrap.sh --serve            install, then start the web surface on :7863
#   ./bootstrap.sh --serve --publish  ... and map beauty.influx.vision via gemstone
#   ./bootstrap.sh --dry-run          show what would happen, change nothing
#
# Installs no system packages and needs no root. It lays out the suite, seeds
# the EGRL ledgers, points the flux binary at the bundle, and verifies.
set -eu

BUNDLE="$(cd "$(dirname "$0")" && pwd)"
PREFIX="$BUNDLE"
DRY=0; SERVE=0; PUBLISH=0
ADDR="127.0.0.1:7863"
HOSTNAME_PUB="beauty.influx.vision"

while [ $# -gt 0 ]; do
  case "$1" in
    --prefix) PREFIX="$2"; shift 2 ;;
    --serve) SERVE=1; shift ;;
    --publish) PUBLISH=1; shift ;;
    --addr) ADDR="$2"; shift 2 ;;
    --dry-run|-n) DRY=1; shift ;;
    -h|--help) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

say() { printf '  %s\n' "$1"; }
run() { if [ "$DRY" = 1 ]; then echo "    would: $*"; else eval "$@"; fi; }

echo "▸ Beauty Protocol Suite installer"
say "bundle : $BUNDLE"
say "prefix : $PREFIX"

# 1. Lay the suite down at the prefix (no-op if installing in place).
if [ "$PREFIX" != "$BUNDLE" ]; then
  say "copying suite → $PREFIX"
  run "mkdir -p '$PREFIX'"
  run "cp -R '$BUNDLE/.' '$PREFIX/'"
fi

# 2. Pick a Python (>=3.11 preferred; 3.10 works since pipeline_paths import is optional).
PY=""
for c in python3.12 python3.11 python3; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
say "python : ${PY:-NONE FOUND}"
[ -z "$PY" ] && { echo "  ✗ no python3 on PATH" >&2; exit 1; }

# 3. Seed the EGRL ledgers + anchor set so the eye-gate works from first run.
OUT="$PREFIX/outputs"
say "seeding ledgers → $OUT"
run "mkdir -p '$OUT' '$PREFIX/chorus'"
for f in taste-log.jsonl eye-gate-candidates.jsonl masterpiece_vault.jsonl submission-ledger.jsonl; do
  run "touch '$OUT/$f'"
done
run "touch '$PREFIX/chorus/anchors.jsonl'"

# 4. Make the binary executable and confirm the suite is readable to it.
run "chmod +x '$PREFIX/flux' '$PREFIX/bootstrap.sh' 2>/dev/null || true"

# 5. Verify: the flux binary sees the bundle, and the eye-gate metrics run.
if [ "$DRY" = 0 ]; then
  echo "▸ verifying"
  if ( cd "$PREFIX" && ./flux beauty check ) >/dev/null 2>&1; then
    say "✓ flux beauty check"
  else
    say "… flux beauty check reported issues (see: cd $PREFIX && ./flux beauty check)"
  fi
  if ( cd "$PREFIX" && "$PY" beauty_eye_gate.py --output-dir "$OUT" metrics ) >/dev/null 2>&1; then
    say "✓ eye-gate metrics run under $PY"
  else
    say "… eye-gate metrics did not run under $PY"
  fi
  PROFILES="$(cd "$PREFIX" && ./flux suites beauty architectures 2>/dev/null | grep -c '→' || true)"
  say "✓ ${PROFILES:-?} hardware profiles registered"
fi

# 6. Optionally serve + publish.
if [ "$SERVE" = 1 ]; then
  echo "▸ starting the web surface on $ADDR"
  run "( cd '$PREFIX' && setsid ./flux beauty dev --addr '$ADDR' > '$OUT/beauty-dev.log' 2>&1 < /dev/null & )"
  say "→ http://$ADDR/overview"
  if [ "$PUBLISH" = 1 ]; then
    if command -v gemstone >/dev/null 2>&1; then
      echo "▸ publishing $HOSTNAME_PUB via gemstone"
      run "gemstone domains publish '$HOSTNAME_PUB' --upstream '$ADDR'"
    else
      say "… gemstone not found; skipping --publish"
    fi
  fi
fi

echo "▸ done."
echo "   suite:   $PREFIX"
echo "   protocols: flux suites beauty architectures | protocols | eye-gate"
echo "   serve:   cd $PREFIX && ./flux beauty dev --addr $ADDR   → /overview /profiles"
echo "   loop:    flux suites beauty slate | record | submit | metrics"
