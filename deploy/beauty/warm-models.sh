#!/usr/bin/env bash
# Resolve and cache HuggingFace repos into HF_HOME at build time.
#
# Resolve every id before baking it, so a genuinely bad id fails the build
# rather than surfacing at runtime as a crashlooping supervisor program.
#
# Note: the Hub redirects lowercased ids to their canonical casing, so casing
# alone is not a failure. model_info() follows the redirect and reports the
# canonical id, which is what gets logged.
set -euo pipefail

if [ -s /run/secrets/hf_token ]; then
    HF_TOKEN="$(cat /run/secrets/hf_token)"
    export HF_TOKEN HUGGING_FACE_HUB_TOKEN="$HF_TOKEN"
fi

for repo in "$@"; do
    [ -n "${repo:-}" ] || continue

    echo "==> resolving ${repo}"
    if ! python3 - "$repo" <<'PY'
import sys
from huggingface_hub import HfApi
repo = sys.argv[1]
try:
    info = HfApi().model_info(repo)
except Exception as exc:
    print(f"    repo id did not resolve: {repo}\n    {type(exc).__name__}: {exc}", file=sys.stderr)
    print("    Check the id against the Hub; the redirect only covers casing, not typos.", file=sys.stderr)
    raise SystemExit(1)
print(f"    ok {info.id}")
PY
    then
        echo "!! refusing to bake an unresolvable model id: ${repo}" >&2
        exit 1
    fi

    echo "==> warming ${repo}"
    hf download "$repo" --quiet
done

echo "==> warm cache: $(du -sh "${HF_HOME:-/models/hf}" 2>/dev/null | cut -f1)"
