#!/usr/bin/env bash
# Build a self-contained, installable Beauty Protocol Suite bundle from the repo.
#
#   package/build-bundle.sh            build dist/beauty-suite-<ver>/ + tarball
#   package/build-bundle.sh --no-tar   just the directory
#
# The bundle carries everything: the compiled flux binary, the Python loop +
# eye-gate, the continuum config, the web surface, the ralpheye EGRL protocol +
# agents, the deploy scripts, the doctrine, and a one-command bootstrap.sh.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

VER="$(sed -n 's/^version = "\(.*\)"/\1/p' package/beauty.suite.toml | head -1)"
VER="${VER:-1.0.0}"
NAME="beauty-suite-${VER}"
DIST="$REPO/dist/$NAME"
MAKE_TAR=1
[ "${1:-}" = "--no-tar" ] && MAKE_TAR=0

echo "▸ building Beauty Protocol Suite bundle v$VER"
rm -rf "$DIST"; mkdir -p "$DIST"

# 1. Compile the flux binary fresh so the bundle is never stale.
echo "  · compiling flux binary"
if command -v go >/dev/null 2>&1; then
  ( cd "$REPO" && go build -o "$DIST/flux" ./cmd/flux )
elif [ -x "$REPO/flux" ]; then
  echo "    go not found; copying the existing ./flux binary"
  cp "$REPO/flux" "$DIST/flux"
else
  echo "    ERROR: no go toolchain and no prebuilt ./flux to copy" >&2; exit 1
fi

# 2. Copy the payload, preserving repo-relative paths.
echo "  · copying payload"
copy() { # copy <src> preserving its relative dir under $DIST
  local src="$1" dst="$DIST/$1"
  mkdir -p "$(dirname "$dst")"
  cp -R "$src" "$dst"
}
copy beauty_pipeline.py
copy beauty_eye_gate.py
copy pipeline_paths.py
copy jury_continuum.toml
copy apps/beauty/public
copy protocols/EGRL.md
copy protocols/agents
copy deploy/deploy_h100_beauty.sh
copy scripts/beauty-stack.sh
copy chorus/LAWS.md
copy chorus/PROTOCOL.md
copy chorus/beauty-queue.json
copy package/beauty.suite.toml

# 3. Drop the installer + README into the bundle root.
echo "  · adding bootstrap.sh + README"
cp package/bootstrap.sh "$DIST/bootstrap.sh"
cp package/BUNDLE_README.md "$DIST/README.md"
chmod +x "$DIST/bootstrap.sh" "$DIST/flux" 2>/dev/null || true

# 4. Manifest of exactly what shipped.
( cd "$DIST" && find . -type f | sort | sed 's|^\./||' > MANIFEST.txt )
echo "  · $(wc -l < "$DIST/MANIFEST.txt") files in bundle"

# 5. Tarball.
if [ "$MAKE_TAR" = 1 ]; then
  ( cd "$REPO/dist" && tar -czf "$NAME.tar.gz" "$NAME" )
  echo "▸ bundle: dist/$NAME.tar.gz  ($(du -h "$REPO/dist/$NAME.tar.gz" | cut -f1))"
fi
echo "▸ dir:    dist/$NAME/"
echo "  install with:  cd dist/$NAME && ./bootstrap.sh --help"
