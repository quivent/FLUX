#!/usr/bin/env bash
# ==============================================================================
# Sovereign FLUX · Mixture of Judges (MoJ) Provisioning Protocol
# ==============================================================================
# Idempotent. Toggle-aware. Refuses to launch an overcommitted VRAM combination.
#
# Every model id, port, precision and VRAM figure comes from jury_continuum.toml
# by way of pipeline_paths.py. Nothing is hardcoded here — not the roster, not
# the ports, not the paths, and above all not /root.
#
#   ./provision_jury.sh              provision the active profile
#   ./provision_jury.sh --dry-run    print the plan, touch nothing
#   ./provision_jury.sh --status     report what is currently up
#   ./provision_jury.sh --help
#
# Environment:
#   ARCANE_PROFILE           rtx-pro-6000 (default) | rtx-pro-6000-x4 | b200 | b300
#   ARCANE_LAYOUT            balanced | dense | tp   (multi-GPU profiles)
#   ARCANE_KONTEXT           1/0 — the only tenant toggle
#   ARCANE_GOVERNOR_REMOTE   1/0 — serve the governor off-card
#   ARCANE_<TENANT>_PRECISION   e.g. ARCANE_FLUX_PRECISION=q4_k_s
#   FLUX_HOME, FLUX_OUT_DIR, FLUX_BIN, ARCANE_PYTHON, HF_HOME
#   VLLM_IMAGE               container image for the vLLM tenants
#   ARCANE_FORCE_RECREATE=1  rebuild containers even if their config is unchanged
#   NO_COLOR / FLUX_NO_COLOR / FLUX_FORCE_COLOR
#
# There is no ARCANE_PIXTRAL and no DINO toggle. Pixtral and the sensory gates
# are mandatory tenants in every profile.
# ==============================================================================
set -euo pipefail

# ------------------------------------------------------------------------------
# Roots. Resolve through symlinks so the script works from anywhere.
# ------------------------------------------------------------------------------
_self="${BASH_SOURCE[0]}"
while [ -L "$_self" ]; do
  _dir=$(cd -P "$(dirname "$_self")" && pwd)
  _self=$(readlink "$_self")
  case "$_self" in /*) ;; *) _self="$_dir/$_self" ;; esac
done
SCRIPT_DIR=$(cd -P "$(dirname "$_self")" && pwd)
FLUX_HOME="${FLUX_HOME:-$SCRIPT_DIR}"
export FLUX_HOME

# ------------------------------------------------------------------------------
# House style — mirrors internal/ui/ui.go. Colour is padded-then-painted so the
# columns line up, and it switches itself off when piped or nohup'd so the log
# files stay free of escape-code litter.
# ------------------------------------------------------------------------------
_ui_color() {
  if [ -n "${FLUX_FORCE_COLOR:-}${CLICOLOR_FORCE:-}" ]; then return 0; fi
  if [ -n "${NO_COLOR:-}${FLUX_NO_COLOR:-}" ]; then return 1; fi
  [ -t 1 ]
}
if _ui_color; then
  C_RESET=$'\033[0m';  C_BOLD=$'\033[1m';   C_DIM=$'\033[2m'
  C_VIOLET=$'\033[38;5;141m'; C_INDIGO=$'\033[38;5;99m'; C_TEAL=$'\033[38;5;73m'
  C_MINT=$'\033[38;5;121m';   C_GOLD=$'\033[38;5;220m'; C_ROSE=$'\033[38;5;204m'
  C_AMBER=$'\033[38;5;214m';  C_INK=$'\033[38;5;246m';  C_LINE=$'\033[38;5;238m'
else
  C_RESET=''; C_BOLD=''; C_DIM=''; C_VIOLET=''; C_INDIGO=''; C_TEAL=''
  C_MINT='';  C_GOLD=''; C_ROSE=''; C_AMBER=''; C_INK='';    C_LINE=''
fi
RULE="━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

ui_rule()  { printf '%s%s%s\n' "$C_INDIGO" "$RULE" "$C_RESET"; }
ui_thin()  { printf '%s%s%s\n' "$C_LINE" "$RULE" "$C_RESET"; }
ui_header() {
  printf '\n%s%s%s%s  %s%s\n' "$C_BOLD" "$C_VIOLET" "$1" "$C_RESET" "$C_DIM$2" "$C_RESET"
  ui_rule
}
ui_section() { printf '\n%s%s%s\n' "$C_BOLD$C_TEAL" "$1" "$C_RESET"; ui_thin; }
ui_kv() {
  local k="$1"; shift
  printf '  %s%-22s%s %s\n' "$C_INK" "$(printf '%s' "$k" | tr '[:lower:]' '[:upper:]')" "$C_RESET" "$*"
}
ui_step() { printf '\n  %s⟐%s %s%s%s\n' "$C_GOLD" "$C_RESET" "$C_BOLD" "$*" "$C_RESET"; }
ui_good() { printf '    %s●%s %s%sOK%s      %s\n' "$C_MINT" "$C_RESET" "$C_BOLD" "$C_MINT" "$C_RESET" "$*"; }
ui_warn() { printf '    %s●%s %s%sWARN%s    %s\n' "$C_AMBER" "$C_RESET" "$C_BOLD" "$C_AMBER" "$C_RESET" "$*"; }
ui_bad()  { printf '    %s✕%s %s%sFAIL%s    %s\n' "$C_ROSE" "$C_RESET" "$C_BOLD" "$C_ROSE" "$C_RESET" "$*" >&2; }
ui_skip() { printf '    %s○%s %s%sSKIP%s    %s\n' "$C_INK" "$C_RESET" "$C_BOLD" "$C_INK" "$C_RESET" "$*"; }
ui_na()   { printf '    %s○%s %s%sN/A%s     %s\n' "$C_INK" "$C_RESET" "$C_BOLD" "$C_INK" "$C_RESET" "$*"; }
ui_soft() { printf '      %s%s%s\n' "$C_DIM" "$*" "$C_RESET"; }
ui_wordmark() {
  printf '%s  ___ _    _   ___  __%s\n'      "$C_VIOLET" "$C_RESET"
  printf '%s | __| |  | | | \\ \\/ /%s\n'    "$C_VIOLET" "$C_RESET"
  printf '%s | _|| |__| |_| |>  < %s\n'      "$C_INDIGO" "$C_RESET"
  printf '%s |_| |____|____//_/\\_\\%s\n'    "$C_TEAL"   "$C_RESET"
}

# Stage ledger (bash 3.2: parallel indexed arrays, no associative arrays).
STAGE_NAME=(); STAGE_STATE=(); STAGE_DETAIL=()
record() { STAGE_NAME+=("$1"); STAGE_STATE+=("$2"); STAGE_DETAIL+=("${3:-}"); }

die() { ui_bad "$*"; exit 1; }

MODE="provision"
for arg in "$@"; do
  case "$arg" in
    --dry-run) MODE="dry-run" ;;
    --status)  MODE="status" ;;
    -h|--help)
      awk 'NR>1 && /^#/ {sub(/^# ?/, ""); print; next} NR>1 {exit}' "$_self"
      exit 0 ;;
    *) die "unknown argument: $arg (try --help)" ;;
  esac
done

# ------------------------------------------------------------------------------
# Interpreter. Needs stdlib tomllib, so 3.11+. Probed by round-tripping a token
# rather than by exit status: a broken shim can exit 0 while doing nothing.
# ------------------------------------------------------------------------------
pick_python() {
  local cand out
  for cand in "${ARCANE_PYTHON:-}" python3.14 python3.13 python3.12 python3.11 python3; do
    [ -n "$cand" ] || continue
    command -v "$cand" >/dev/null 2>&1 || continue
    out=$("$cand" -c 'import tomllib,sys; sys.stdout.write("tomllib-ok")' 2>/dev/null || true)
    if [ "$out" = "tomllib-ok" ]; then printf '%s\n' "$cand"; return 0; fi
  done
  return 1
}

ui_header "provision jury" "mixture of judges · $(basename "$FLUX_HOME")"
ui_wordmark
printf '\n'

PY=$(pick_python || true)
if [ -z "$PY" ]; then
  die "no Python 3.11+ with stdlib tomllib on PATH. Set ARCANE_PYTHON=/path/to/python3."
fi

[ -f "$FLUX_HOME/pipeline_paths.py" ] || die "pipeline_paths.py not found under $FLUX_HOME"
[ -f "$FLUX_HOME/jury_continuum.toml" ] || die "jury_continuum.toml not found under $FLUX_HOME"

# ------------------------------------------------------------------------------
# Resolve the whole plan in one call. Tab-separated so bash 3.2 can read it
# without jq, and so a model id containing a space cannot corrupt a field.
# ------------------------------------------------------------------------------
PLAN=$(cd "$FLUX_HOME" && "$PY" - <<'PYPLAN'
import sys
import pipeline_paths as pp

def emit(*fields):
    sys.stdout.write("\t".join("" if f is None else str(f) for f in fields) + "\n")

cfg = pp.load_continuum()
hw, budget = cfg["hardware"], cfg["vram"]

emit("PROFILE", cfg["profile"], hw["gpu"], hw["sm"], hw["vllm_min_version"],
     "yes" if hw["prebuilt_wheel_available"] else "no")
# budget["reserve_gib"] is the AGGREGATE reserve (per-GPU reserve x gpus);
# hw["reserve_gib"] is the per-card figure. The summary line wants the aggregate.
emit("BUDGET", budget["total_gib"], budget["reserve_gib"], budget["usable_gib"],
     budget["allocated_gib"], budget["free_gib"], budget["headroom_gib"],
     "yes" if budget["fits"] else "no", budget["overcommit_reason"] or "")
emit("HW", hw["gpus"], hw["interconnect"],
     "yes" if hw["interconnect_verified"] else "no",
     "yes" if hw["tensor_parallel_viable"] else "no",
     max((int(t.get("tensor_parallel") or 1) for t in cfg["tenants"].values()), default=1),
     hw.get("layout") or "")
for row in budget["per_gpu"]:
    emit("GPUROW", row["gpu"], row["allocated_gib"], row["usable_gib"],
         row["free_gib"], row["headroom_gib"], "yes" if row["fits"] else "no",
         ",".join(row["tenants"]))
emit("TOGGLE", "kontext", "on" if cfg["toggles"]["kontext"] else "off")
emit("TOGGLE", "governor_remote", "on" if cfg["toggles"]["governor_remote"] else "off")
emit("PATHS", pp.FLUX_HOME, pp.OUT_DIR, pp.FLUXD_DIR, pp.FLUXD_SOCK,
     pp.KONTEXT_SOCK, pp.FLUX_BIN, pp.LOG_DIR, pp.MOJ_LOG, cfg["governor_base_url"])

for name, t in cfg["tenants"].items():
    emit("TENANT", name, t.get("kind"), "on" if t.get("enabled") else "off",
         "yes" if t.get("remote") else "no", t.get("model"), t.get("precision"),
         t.get("port") or "", t.get("gpu_memory_utilization") or "",
         t.get("vram_expected_gib"), t.get("served_name") or name,
         t.get("max_model_len") or "", t.get("kv_cache_dtype") or "auto",
         t.get("tensor_parallel") or 1, t.get("quantization") or "",
         t.get("gguf_file") or "", t.get("socket") or "", t.get("role") or "",
         t.get("gpu") if t.get("gpu") is not None else "",
         ",".join(str(g) for g in (t.get("gpu_span") or [])),
         t.get("vram_per_card_gib", t.get("vram_expected_gib")),
         t.get("shard_id") if t.get("shard_id") is not None else "",
         t.get("shard_total") or "")

for name, url in cfg["endpoints"].items():
    emit("ENDPOINT", name, url)
for w in cfg["warnings"]:
    emit("WARN", w)
PYPLAN
) || die "could not resolve the continuum plan (see the traceback above)"

field() { printf '%s' "$1" | cut -d'	' -f"$2"; }

PROFILE=""; GPU=""; SM=""; VLLM_MIN=""; WHEEL=""
VRAM_TOTAL=""; VRAM_RESERVE=""; VRAM_USABLE=""; VRAM_ALLOC=""; VRAM_FREE=""
VRAM_HEADROOM=""; VRAM_FITS=""; VRAM_REASON=""
KONTEXT="off"; GOV_REMOTE="off"
GPUS=1; INTERCONNECT="none"; IC_VERIFIED="no"; TP_VIABLE="no"; MAX_TP=1; LAYOUT=""
GPUROWS=()
P_HOME=""; P_OUT=""; P_FLUXD=""; P_SOCK=""; P_KSOCK=""; P_BIN=""; P_LOGDIR=""
P_MOJLOG=""; GOV_URL=""
TENANTS=(); WARNINGS=(); ENDPOINTS=()

while IFS= read -r line; do
  [ -n "$line" ] || continue
  case "$(field "$line" 1)" in
    PROFILE)
      PROFILE=$(field "$line" 2); GPU=$(field "$line" 3); SM=$(field "$line" 4)
      VLLM_MIN=$(field "$line" 5); WHEEL=$(field "$line" 6) ;;
    BUDGET)
      VRAM_TOTAL=$(field "$line" 2); VRAM_RESERVE=$(field "$line" 3)
      VRAM_USABLE=$(field "$line" 4); VRAM_ALLOC=$(field "$line" 5)
      VRAM_FREE=$(field "$line" 6);  VRAM_HEADROOM=$(field "$line" 7)
      VRAM_FITS=$(field "$line" 8);  VRAM_REASON=$(field "$line" 9) ;;
    HW)
      GPUS=$(field "$line" 2); INTERCONNECT=$(field "$line" 3)
      IC_VERIFIED=$(field "$line" 4); TP_VIABLE=$(field "$line" 5)
      MAX_TP=$(field "$line" 6); LAYOUT=$(field "$line" 7) ;;
    GPUROW) GPUROWS+=("$line") ;;
    TOGGLE)
      case "$(field "$line" 2)" in
        kontext)         KONTEXT=$(field "$line" 3) ;;
        governor_remote) GOV_REMOTE=$(field "$line" 3) ;;
      esac ;;
    PATHS)
      P_HOME=$(field "$line" 2);   P_OUT=$(field "$line" 3)
      P_FLUXD=$(field "$line" 4);  P_SOCK=$(field "$line" 5)
      P_KSOCK=$(field "$line" 6);  P_BIN=$(field "$line" 7)
      P_LOGDIR=$(field "$line" 8); P_MOJLOG=$(field "$line" 9)
      GOV_URL=$(field "$line" 10) ;;
    TENANT)   TENANTS+=("$line") ;;
    ENDPOINT) ENDPOINTS+=("$line") ;;
    WARN)     WARNINGS+=("$(field "$line" 2)") ;;
  esac
done <<EOF
$PLAN
EOF

# ------------------------------------------------------------------------------
# 1 · Posture
# ------------------------------------------------------------------------------
ui_section "posture"
ui_kv "profile"   "${C_BOLD}${PROFILE}${C_RESET}"
ui_kv "gpu"       "$GPU  ${C_DIM}($SM)${C_RESET}"
ui_kv "mode"      "$MODE"
ui_kv "flux home" "$P_HOME"
ui_kv "out dir"   "$P_OUT"
ui_kv "log dir"   "$P_LOGDIR"
ui_kv "flux bin"  "$P_BIN"
ui_kv "python"    "$($PY -c 'import sys;print(sys.executable, ".".join(map(str,sys.version_info[:3])))')"
ui_kv "topology"  "${GPUS} x $([ "$GPUS" -gt 1 ] && printf '96 GiB card' || printf 'card')$([ -n "$LAYOUT" ] && printf '  %slayout: %s%s' "$C_DIM" "$LAYOUT" "$C_RESET")"
ui_kv "kontext"   "$([ "$KONTEXT" = on ] && printf '%sENABLED%s' "$C_MINT" "$C_RESET" || printf '%sdisabled%s' "$C_INK" "$C_RESET")"
ui_kv "governor"  "$([ "$GOV_REMOTE" = on ] && printf '%sREMOTE%s  %s' "$C_TEAL" "$C_RESET" "$GOV_URL" || printf '%slocal%s   %s' "$C_INK" "$C_RESET" "$GOV_URL")"

# ------------------------------------------------------------------------------
# 1b · Interconnect. DETECT, DO NOT ASSUME.
#
# The toml records the DECLARED interconnect. NVIDIA's published spec for
# RTX PRO 6000 Blackwell says no NVLink; the operator reports NVLink on their
# four-card box. Neither claim is hardcoded as truth — this probes the actual
# host and shouts when reality and declaration disagree. Tensor parallelism over
# PCIe Gen5 x16 (~64 GB/s against NVLink's ~900 GB/s) is a performance trap, so
# a TP tenant on a PCIe-only host is reported as a misconfiguration, never
# silently launched.
# ------------------------------------------------------------------------------
IC_DETECTED="unknown"
if [ "$GPUS" -gt 1 ] || [ "$MAX_TP" -gt 1 ]; then
  ui_section "interconnect"
  ui_kv "declared" "$INTERCONNECT $([ "$IC_VERIFIED" = yes ] && printf '(verified)' || printf '%s(UNVERIFIED)%s' "$C_AMBER" "$C_RESET")"
  if command -v nvidia-smi >/dev/null 2>&1; then
    nvlink_out=$(nvidia-smi nvlink --status 2>/dev/null || true)
    topo_out=$(nvidia-smi topo -m 2>/dev/null || true)
    if printf '%s' "$nvlink_out" | grep -qi 'GB/s'; then
      IC_DETECTED="nvlink"
    elif printf '%s' "$topo_out" | grep -qE 'NV[0-9]+'; then
      IC_DETECTED="nvlink"
    elif [ -n "$topo_out" ]; then
      IC_DETECTED="pcie"
    fi
    ui_kv "detected" "$IC_DETECTED"
    [ -n "$topo_out" ] && printf '%s\n' "$topo_out" | sed "s/^/      ${C_DIM}/;s/\$/${C_RESET}/" | head -12
  else
    ui_na "nvidia-smi not present — interconnect cannot be verified on this host"
  fi

  case "$IC_DETECTED:$INTERCONNECT" in
    unknown:*) ui_warn "interconnect UNVERIFIED. Declared '$INTERCONNECT'. Run 'nvidia-smi nvlink --status' and 'nvidia-smi topo -m' on the real node before trusting a TP layout."
               record "interconnect" unavailable "undetectable here" ;;
    nvlink:nvlink*) ui_good "detected interconnect matches the declaration ($INTERCONNECT)"
               record "interconnect" ok "nvlink confirmed" ;;
    pcie:nvlink*)
      ui_bad "INTERCONNECT MISMATCH: toml declares '$INTERCONNECT', this host is PCIe-only."
      ui_soft "PCIe Gen5 x16 moves ~64 GB/s; NVLink moves ~900 GB/s. Tensor parallelism"
      ui_soft "puts full weight tensors on that wire, so TP here is a latency trap."
      if [ "$MAX_TP" -gt 1 ]; then
        ui_bad "profile $PROFILE has tenant(s) with tensor_parallel=$MAX_TP. That is a MISCONFIGURATION on this host."
        ui_soft "Use ARCANE_LAYOUT=balanced (shard parallelism, tensor_parallel=1 throughout)."
        record "interconnect" fail "TP=$MAX_TP declared over detected PCIe"
        exit 3
      fi
      ui_warn "no TP tenants, so this is survivable — but fix the declaration."
      record "interconnect" fail "declared nvlink, detected pcie" ;;
    *) ui_kv "note" "declared '$INTERCONNECT', detected '$IC_DETECTED'"
       record "interconnect" ok "$IC_DETECTED" ;;
  esac
  if [ "$MAX_TP" -gt 1 ] && [ "$TP_VIABLE" != "yes" ]; then
    ui_bad "profile declares tensor_parallel=$MAX_TP but tensor_parallel_viable=false."
    record "interconnect" fail "TP declared on a non-TP profile"
    exit 3
  fi
fi

# ------------------------------------------------------------------------------
# 2 · VRAM preflight. Nothing launches until this passes.
#     `fits` is PER-GPU. A healthy aggregate never excuses one overfull card.
# ------------------------------------------------------------------------------
ui_section "vram preflight"
printf '  %s%-10s %-9s %-4s %-4s %-6s %5s %8s %9s  %s%s\n' "$C_INK" \
  "TENANT" "PRECISION" "GPU" "TP" "PORT" "UTIL" "VRAM" "STATE" "MODEL" "$C_RESET"
for row in "${TENANTS[@]}"; do
  t_name=$(field "$row" 2);  t_on=$(field "$row" 4);   t_remote=$(field "$row" 5)
  t_model=$(field "$row" 6); t_prec=$(field "$row" 7); t_port=$(field "$row" 8)
  t_util=$(field "$row" 9);  t_vram=$(field "$row" 10); t_tp=$(field "$row" 14)
  t_span=$(field "$row" 20)
  if [ "$t_remote" = "yes" ]; then t_state="remote"; t_tint="$C_TEAL"
  elif [ "$t_on" = "on" ];    then t_state="on";     t_tint="$C_MINT"
  else                             t_state="off";    t_tint="$C_INK"
  fi
  printf '  %s%-10s%s %-9s %-4s %-4s %-6s %5s %8s %s%9s%s  %s%s%s\n' \
    "$C_BOLD" "$t_name" "$C_RESET" "$t_prec" "${t_span:---}" "${t_tp:-1}" \
    "${t_port:---}" "${t_util:---}" "$t_vram" "$t_tint" "$t_state" "$C_RESET" \
    "$C_DIM" "$t_model" "$C_RESET"
done
ui_thin
if [ "${#GPUROWS[@]}" -gt 1 ]; then
  printf '  %s%-10s %10s %10s %10s   %s%s\n' "$C_INK" \
    "PER-GPU" "RESERVED" "USABLE" "HEADROOM" "TENANTS" "$C_RESET"
  for row in "${GPUROWS[@]}"; do
    g_id=$(field "$row" 2); g_alloc=$(field "$row" 3); g_usable=$(field "$row" 4)
    g_head=$(field "$row" 6); g_fits=$(field "$row" 7); g_ten=$(field "$row" 8)
    if [ "$g_fits" = "yes" ]; then g_tint="$C_MINT"; g_mark="●"; else g_tint="$C_ROSE"; g_mark="✕"; fi
    printf '  %s%s gpu%-6s%s %10s %10s %s%10s%s   %s%s%s\n' \
      "$g_tint" "$g_mark" "$g_id" "$C_RESET" "$g_alloc" "$g_usable" \
      "$g_tint" "$g_head" "$C_RESET" "$C_DIM" "$g_ten" "$C_RESET"
  done
  ui_thin
fi
printf '  %s%-10s%s %saggregate %s of %s GiB  ·  free %s  ·  fits is PER-GPU%s\n' \
  "$C_BOLD" "budget" "$C_RESET" "$C_DIM" "$VRAM_ALLOC" "$VRAM_TOTAL" "$VRAM_FREE" "$C_RESET"

if [ "$VRAM_FITS" != "yes" ]; then
  printf '\n'
  ui_bad "VRAM OVERCOMMIT — refusing to launch anything."
  ui_soft "$VRAM_REASON"
  printf '\n'
  record "vram preflight" fail "$VRAM_REASON"
  exit 2
fi
ui_good "fits: $VRAM_ALLOC / $VRAM_USABLE GiB usable (${VRAM_TOTAL} total − ${VRAM_RESERVE} reserved)"
record "vram preflight" ok "$VRAM_ALLOC/$VRAM_USABLE GiB"

if [ "${#WARNINGS[@]}" -gt 0 ]; then
  printf '\n'
  for w in "${WARNINGS[@]}"; do ui_warn "$w"; done
fi

# Cross-check against the actual card, when there is one.
if command -v nvidia-smi >/dev/null 2>&1; then
  actual=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1 || true)
  if [ -n "$actual" ]; then
    actual_gib=$(awk -v m="$actual" 'BEGIN{printf "%.1f", m/1024}')
    ui_kv "detected vram" "${actual_gib} GiB"
    awk -v a="$actual_gib" -v p="$VRAM_TOTAL" 'BEGIN{exit !(a < p - 4)}' \
      && ui_warn "card reports ${actual_gib} GiB but profile ${PROFILE} budgets ${VRAM_TOTAL} GiB — wrong ARCANE_PROFILE?" \
      || true
  fi
fi

# The wheel-bank gap already arrives as a warning from pipeline_paths; this only
# adds the sentence that says what to do about it.
if [ "$WHEEL" = "no" ]; then
  ui_soft "vLLM >= $VLLM_MIN must be built for $SM before the vLLM tenants can start."
fi

if [ "$MODE" = "dry-run" ]; then
  printf '\n'
  ui_section "dry run"
  ui_skip "plan resolved and within budget; nothing was launched."
  printf '\n'
  exit 0
fi

# ------------------------------------------------------------------------------
# --status: read-only. Report what is actually up, change nothing.
# ------------------------------------------------------------------------------
if [ "$MODE" = "status" ]; then
  ui_section "live status"
  for row in "${TENANTS[@]}"; do
    t_name=$(field "$row" 2); t_kind=$(field "$row" 3); t_on=$(field "$row" 4)
    t_remote=$(field "$row" 5); t_port=$(field "$row" 8); t_sock=$(field "$row" 17)
    if [ "$t_remote" = "yes" ]; then
      ui_skip "$t_name — remote ($GOV_URL)"
    elif [ "$t_on" != "on" ]; then
      ui_skip "$t_name — disabled"
    elif [ "$t_kind" = "vllm" ]; then
      if command -v docker >/dev/null 2>&1 \
         && [ "$(docker inspect -f '{{ .State.Running }}' "arcane-vllm-$t_name" 2>/dev/null || echo false)" = "true" ]; then
        ui_good "$t_name — container up on :$t_port"
      else
        ui_warn "$t_name — no running container (expected :$t_port)"
      fi
    elif [ "$t_kind" = "uds" ]; then
      if [ -n "$t_sock" ] && [ -S "$P_FLUXD/$t_sock" ]; then
        ui_good "$t_name — socket live on gpu$(field "$row" 19) at $P_FLUXD/$t_sock"
      else
        ui_warn "$t_name — no socket at $P_FLUXD/${t_sock:-<unset>}"
      fi
    else
      ui_na "$t_name — in-process, nothing to probe from here"
    fi
  done
  if [ -f "$P_FLUXD/moj_evaluator.pid" ] \
     && kill -0 "$(cat "$P_FLUXD/moj_evaluator.pid")" 2>/dev/null; then
    ui_good "moj evaluator — running (pid $(cat "$P_FLUXD/moj_evaluator.pid"))"
  else
    ui_warn "moj evaluator — not running"
  fi
  printf '\n'
  exit 0
fi

# ------------------------------------------------------------------------------
# 3 · Directories
# ------------------------------------------------------------------------------
ui_step "Preparing state directories"
if (cd "$FLUX_HOME" && "$PY" -c 'import pipeline_paths as p; p.ensure_dirs()'); then
  ui_good "$P_OUT"
  ui_soft "$P_FLUXD  ·  $P_LOGDIR"
  record "directories" ok "$P_OUT"
else
  ui_bad "could not create the state directories under $P_OUT"
  record "directories" fail "$P_OUT"
fi

# ------------------------------------------------------------------------------
# 4 · vLLM tenants
# ------------------------------------------------------------------------------
VLLM_IMAGE="${VLLM_IMAGE:-vllm/vllm-openai:latest}"
HF_CACHE="${HF_HOME:-$HOME/.cache/huggingface}"
HAVE_DOCKER=0
command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1 && HAVE_DOCKER=1

# Fingerprint the launch config so a rerun with identical settings is a no-op.
fingerprint() { printf '%s' "$*" | cksum | cut -d' ' -f1; }

launch_vllm() {
  local name="$1" model="$2" port="$3" util="$4" served="$5" maxlen="$6"
  local kvdtype="$7" tp="$8" quant="$9" role="${10}" span="${11}"
  local container="arcane-vllm-$name"
  local fp
  span="${span:-0}"
  fp=$(fingerprint "$VLLM_IMAGE|$model|$port|$util|$served|$maxlen|$kvdtype|$tp|$quant|$span")

  if [ "$HAVE_DOCKER" -eq 0 ]; then
    ui_na "$name — docker unavailable on this host; not launched"
    ui_soft "$model  ·  gpu $span  ·  :$port  ·  util $util  ·  tp $tp"
    record "tenant:$name" unavailable "docker unavailable"
    return 0
  fi

  local existing
  existing=$(docker inspect -f '{{ index .Config.Labels "arcane.fingerprint" }}' "$container" 2>/dev/null || true)
  local running
  running=$(docker inspect -f '{{ .State.Running }}' "$container" 2>/dev/null || echo false)
  if [ "$existing" = "$fp" ] && [ "$running" = "true" ] && [ -z "${ARCANE_FORCE_RECREATE:-}" ]; then
    ui_good "$name already running with an identical config — left alone"
    ui_soft "$container  ·  $model  ·  :$port"
    record "tenant:$name" ok "unchanged"
    return 0
  fi

  if docker ps -a --format '{{.Names}}' | grep -qx "$container"; then
    docker stop "$container" >/dev/null 2>&1 || true
    docker rm   "$container" >/dev/null 2>&1 || true
  fi

  local args
  args="--host 0.0.0.0 --port $port --model $model --served-model-name $served"
  args="$args --gpu-memory-utilization $util --tensor-parallel-size $tp"
  [ -n "$maxlen" ]  && args="$args --max-model-len $maxlen"
  [ -n "$kvdtype" ] && args="$args --kv-cache-dtype $kvdtype"
  [ -n "$quant" ]   && args="$args --quantization $quant"
  args="$args --trust-remote-code --enable-prefix-caching"
  [ -n "${ARCANE_ATTENTION_BACKEND:-}" ] && args="$args --attention-backend $ARCANE_ATTENTION_BACKEND"
  [ -z "${ARCANE_NO_ASYNC_SCHED:-}" ] && args="$args --async-scheduling"
  # The governor is a text-only critic here. Refusing multimodal input keeps it
  # from allocating a vision tower and the caches that go with it.
  [ "$name" = "governor" ] && args="$args --limit-mm-per-prompt {\"image\":0,\"video\":0}"

  # shellcheck disable=SC2086
  if docker run -d \
      --name "$container" \
      --label "arcane.fingerprint=$fp" \
      --label "arcane.profile=$PROFILE" \
      --label "arcane.tenant=$name" \
      --restart always --gpus "\"device=$span\"" --ipc host --network host \
      -v "${HF_CACHE}:/root/.cache/huggingface" \
      "$VLLM_IMAGE" \
      -m vllm.entrypoints.openai.api_server $args >/dev/null; then
    ui_good "$name launched on :$port (gpu $span)"
    ui_soft "$model  ·  util $util  ·  kv $kvdtype  ·  tp $tp  ·  $role"
    record "tenant:$name" ok ":$port"
  else
    ui_bad "$name failed to launch"
    record "tenant:$name" fail "$model"
  fi
}

ui_step "Provisioning vLLM tenants"
if [ "$HAVE_DOCKER" -eq 0 ]; then
  ui_na "docker is not available on this host — no containers will be started"
fi
for row in "${TENANTS[@]}"; do
  [ "$(field "$row" 3)" = "vllm" ] || continue
  t_name=$(field "$row" 2); t_on=$(field "$row" 4); t_remote=$(field "$row" 5)
  if [ "$t_remote" = "yes" ]; then
    ui_skip "$t_name — served remotely at $GOV_URL; no container launched"
    record "tenant:$t_name" skip "remote"
    continue
  fi
  if [ "$t_on" != "on" ]; then
    ui_skip "$t_name — disabled in this profile"
    record "tenant:$t_name" skip "disabled"
    continue
  fi
  launch_vllm "$t_name" "$(field "$row" 6)" "$(field "$row" 8)" "$(field "$row" 9)" \
              "$(field "$row" 11)" "$(field "$row" 12)" "$(field "$row" 13)" \
              "$(field "$row" 14)" "$(field "$row" 15)" "$(field "$row" 18)" \
              "$(field "$row" 20)"
done

# ------------------------------------------------------------------------------
# 5 · FLUX resident UDS worker
# ------------------------------------------------------------------------------
# One resident worker per generator card. On a multi-GPU profile the generators
# are disjoint atlas SHARDS, not a tensor-parallel model: each worker gets its
# own card through FLUX_WORKER_GPU/CUDA_VISIBLE_DEVICES and therefore sees itself
# as cuda:0 (worker.py ~L774), and each gets its own socket, which is exactly the
# per-GPU socket discovery internal/daemon/daemon.go already implements.
ui_step "Verifying the FLUX resident UDS engine(s)"
for row in "${TENANTS[@]}"; do
  [ "$(field "$row" 3)" = "uds" ] || continue
  t_name=$(field "$row" 2)
  case "$t_name" in kontext) continue ;; esac
  [ "$(field "$row" 4)" = "on" ] || continue
  t_sock=$(field "$row" 17); t_gpu=$(field "$row" 19)
  t_shard=$(field "$row" 22); t_shard_n=$(field "$row" 23)
  sock_path="$P_FLUXD/${t_sock:-flux-gpu0.sock}"
  shard_note=""
  [ -n "$t_shard_n" ] && shard_note="  ·  atlas shard ${t_shard}/${t_shard_n}"
  if [ -S "$sock_path" ]; then
    ui_good "$t_name — resident worker already listening on gpu${t_gpu:-0}"
    ui_soft "$sock_path$shard_note"
    record "flux:$t_name" ok "socket live"
  elif [ -x "$P_BIN" ]; then
    if FLUX_WORKER_GPU="${t_gpu:-0}" CUDA_VISIBLE_DEVICES="${t_gpu:-0}" \
       "$P_BIN" warm >/dev/null 2>&1; then
      ui_good "$t_name — worker warmed on gpu${t_gpu:-0}"
      ui_soft "$sock_path$shard_note"
      record "flux:$t_name" ok "warmed"
    else
      ui_warn "$t_name — \`$P_BIN warm\` returned non-zero on gpu${t_gpu:-0}"
      record "flux:$t_name" fail "warm failed"
    fi
  else
    ui_na "$t_name — no flux binary at $P_BIN (set FLUX_BIN, or run 'make flux')"
    record "flux:$t_name" unavailable "no binary"
  fi
done

# ------------------------------------------------------------------------------
# 6 · Kontext (the only toggle)
# ------------------------------------------------------------------------------
ui_step "Kontext edit pass"
K_GPU=0
for row in "${TENANTS[@]}"; do
  [ "$(field "$row" 2)" = "kontext" ] || continue
  K_GPU=$(field "$row" 19)
done
if [ "$KONTEXT" = "on" ]; then
  if [ -S "$P_KSOCK" ]; then
    ui_good "kontext worker already listening (gpu${K_GPU:-0})"
    ui_soft "$P_KSOCK"
    record "kontext" ok "socket live"
  else
    ui_warn "kontext is enabled for this profile but no worker on $P_KSOCK"
    ui_soft "start it with: FLUX_WORKER_GPU=${K_GPU:-0} $P_BIN warm --kontext"
    record "kontext" unavailable "no socket"
  fi
else
  ui_skip "disabled. Enable with ARCANE_KONTEXT=1 — check the budget first."
  record "kontext" skip "disabled"
fi

# ------------------------------------------------------------------------------
# 7 · Mixture of Judges evaluation loop
# ------------------------------------------------------------------------------
ui_step "Mixture of Judges evaluation loop"
MOJ="$FLUX_HOME/moj_evaluator.py"
if [ ! -f "$MOJ" ]; then
  ui_bad "moj_evaluator.py NOT FOUND at $MOJ"
  ui_soft "The evaluation loop is the point of this stack. Nothing was started for it."
  ui_soft "This stage is FAILED, not skipped — provisioning is incomplete without it."
  record "moj evaluator" fail "missing $MOJ"
else
  chmod +x "$MOJ" 2>/dev/null || true
  pkill -f "$MOJ" >/dev/null 2>&1 || true
  # shellcheck disable=SC2069
  nohup "$PY" -u "$MOJ" >>"$P_MOJLOG" 2>&1 &
  MOJ_PID=$!
  printf '%s\n' "$MOJ_PID" > "$P_FLUXD/moj_evaluator.pid"
  sleep 1
  if kill -0 "$MOJ_PID" 2>/dev/null; then
    ui_good "evaluator running (pid $MOJ_PID)"
    ui_soft "$P_MOJLOG"
    record "moj evaluator" ok "pid $MOJ_PID"
  else
    ui_bad "evaluator exited immediately — see $P_MOJLOG"
    record "moj evaluator" fail "exited at startup"
  fi
fi

# ------------------------------------------------------------------------------
# 8 · Studio surfaces
# ------------------------------------------------------------------------------
ui_step "Studio surfaces"
SURFACES="$FLUX_HOME/provision_surfaces.py"
if [ ! -f "$SURFACES" ]; then
  ui_na "provision_surfaces.py not present — surfaces were not verified"
  ui_soft "Reported as UNAVAILABLE, never as passed."
  record "surfaces" unavailable "missing $SURFACES"
else
  if (cd "$FLUX_HOME" && "$PY" "$SURFACES" --check --profile "$PROFILE"); then
    ui_good "surfaces verified for profile $PROFILE"
    record "surfaces" ok "$PROFILE"
  else
    rc=$?
    ui_bad "provision_surfaces.py --check exited $rc"
    record "surfaces" fail "exit $rc"
  fi
fi

# ------------------------------------------------------------------------------
# 9 · Summary
# ------------------------------------------------------------------------------
ui_header "provisioning summary" "$PROFILE · $GPU"
failures=0
i=0
while [ "$i" -lt "${#STAGE_NAME[@]}" ]; do
  n="${STAGE_NAME[$i]}"; s="${STAGE_STATE[$i]}"; d="${STAGE_DETAIL[$i]}"
  case "$s" in
    ok)          tint="$C_MINT"; glyph="●" ;;
    skip)        tint="$C_INK";  glyph="○" ;;
    unavailable) tint="$C_AMBER"; glyph="○" ;;
    *)           tint="$C_ROSE"; glyph="✕"; failures=$((failures + 1)) ;;
  esac
  printf '  %s%s%s %-20s %s%-12s%s %s%s%s\n' \
    "$tint" "$glyph" "$C_RESET" "$n" "$tint" "$(printf '%s' "$s" | tr '[:lower:]' '[:upper:]')" \
    "$C_RESET" "$C_DIM" "$d" "$C_RESET"
  i=$((i + 1))
done
ui_thin
if [ "${#ENDPOINTS[@]}" -gt 0 ]; then
  for row in "${ENDPOINTS[@]}"; do
    printf '  %s%-22s%s %s%s%s\n' "$C_TEAL" "$(field "$row" 2)" "$C_RESET" \
      "$C_DIM" "$(field "$row" 3)" "$C_RESET"
  done
  ui_thin
fi

if [ "$failures" -gt 0 ]; then
  printf '  %s%s✕ %d stage(s) failed%s\n\n' "$C_BOLD" "$C_ROSE" "$failures" "$C_RESET"
  exit 1
fi
printf '  %s%s● jury stack provisioned%s  %s%s reserved of %s GiB%s\n\n' \
  "$C_BOLD" "$C_MINT" "$C_RESET" "$C_DIM" "$VRAM_ALLOC" "$VRAM_TOTAL" "$C_RESET"
