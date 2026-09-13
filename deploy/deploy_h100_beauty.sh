#!/usr/bin/env bash
# Beauty Protocol H100 studio deployer.
#
# Profiles:
#   h100                       compact studio; local Qwen, remote Gemma
#   h100-remote-witness        local FLUX/Pixtral/gates; remote Qwen/Gemma
#   h100-distributed-atelier   same visual studio plus resident Kontext;
#                              dedicated remote Qwen and Gemma machines
#
# Pixtral always stays on the FLUX studio machine. Remote served-model URLs can
# be supplied with --witness-url/--governor-url or their MOJ_* environment vars.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE=h100
MODE=apply
WITNESS_URL="${MOJ_VISUAL_WITNESS_URL:-}"
GOVERNOR_URL="${MOJ_GOVERNOR_URL:-${GOVERNOR_BASE_URL:-}}"

usage() {
  sed -n '2,24p' "$0"
}

while (($#)); do
  case "$1" in
    --profile) PROFILE="${2:?--profile requires a profile name}"; shift 2 ;;
    --witness-url) WITNESS_URL="${2:?--witness-url requires a URL}"; shift 2 ;;
    --governor-url) GOVERNOR_URL="${2:?--governor-url requires a URL}"; shift 2 ;;
    --dry-run) MODE=dry-run; shift ;;
    --status) MODE=status; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

case "$PROFILE" in
  h100|h100-remote-witness|h100-distributed-atelier) ;;
  *) echo "ERROR: unsupported Beauty profile: $PROFILE" >&2; exit 2 ;;
esac

command -v nvidia-smi >/dev/null 2>&1 || { echo "ERROR: nvidia-smi is required" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "ERROR: python3 is required" >&2; exit 1; }

if [[ "$MODE" == apply && "$(id -u)" != 0 ]]; then
  echo "ERROR: apply mode must run as root (use --dry-run or --status without root)." >&2
  exit 1
fi

if [[ "$PROFILE" != h100 && -z "$WITNESS_URL" ]]; then
  WITNESS_URL="https://beauty.governor.influx.vision/v1"
fi
if [[ -z "$GOVERNOR_URL" ]]; then
  GOVERNOR_URL="https://governor.influx.vision/v1"
fi

echo "Beauty Protocol · H100 · $PROFILE"
echo "root: $ROOT"
echo "GPU: $(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits | head -n 1)"
echo "contract: $ROOT/docs/H100_BEAUTY_COMPACT.md"
echo "profile: ARCANE_PROFILE=$PROFILE"
echo "governor: remote ($GOVERNOR_URL)"
if [[ "$PROFILE" == h100 ]]; then
  echo "witness: local"
else
  echo "witness: remote ($WITNESS_URL)"
fi
echo "invariant: BF16 FLUX + W4A16 Pixtral on studio · TRITON_ATTN · no TP"

run_studio() {
  local studio_out="${BEAUTY_OUTPUT:-$ROOT/.beauty-h100/$PROFILE/output}"
  mkdir -p "$studio_out"
  local -a env_args=(
    "CUDA_VISIBLE_DEVICES=${BEAUTY_STUDIO_GPU:-0}"
    "FLUX_HOME=$ROOT"
    "FLUX_OUT_DIR=$studio_out"
    "ARCANE_PROFILE=$PROFILE"
    "ARCANE_GOVERNOR_REMOTE=1"
    "GOVERNOR_BASE_URL=$GOVERNOR_URL"
    "MOJ_GOVERNOR_URL=$GOVERNOR_URL"
    "VLLM_ATTENTION_BACKEND=TRITON_ATTN"
  )
  if [[ "$PROFILE" == h100 ]]; then
    env_args+=("ARCANE_WITNESS_REMOTE=0" "ARCANE_KONTEXT=0")
  else
    env_args+=("ARCANE_WITNESS_REMOTE=1" "MOJ_VISUAL_WITNESS_URL=$WITNESS_URL")
    if [[ "$PROFILE" == h100-distributed-atelier ]]; then
      env_args+=("ARCANE_KONTEXT=1")
    else
      env_args+=("ARCANE_KONTEXT=0")
    fi
  fi

  echo "studio: GPU ${BEAUTY_STUDIO_GPU:-0} · Pixtral local :8002 · output $studio_out"
  if [[ "$MODE" == dry-run ]]; then
    (cd "$ROOT" && env "${env_args[@]}" ./provision_jury.sh --dry-run)
  elif [[ "$MODE" == status ]]; then
    (cd "$ROOT" && env "${env_args[@]}" ./provision_jury.sh --status)
  else
    (cd "$ROOT" && env "${env_args[@]}" ./provision_jury.sh)
  fi
}

run_studio
