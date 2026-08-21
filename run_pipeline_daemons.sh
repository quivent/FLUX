#!/usr/bin/env bash
# ==============================================================================
# Sovereign FLUX · Pipeline Daemon Supervisor
# ==============================================================================
# Starts, stops and reports on the three long-running pipeline processes:
#
#   studio   the flux HTTP studio           ($FLUX_BIN serve studio)
#   jury     the visual jury evaluator      (jury_evaluator.py)
#   feeder   the perpetual prompt feeder    (perpetual_feeder.py)
#
# Every path comes from pipeline_paths.py. The previous revision of this script
# hardcoded /root/.local/bin/flux and /root/CLIs/flux/, then pkill'd by bare
# pattern — which would happily kill an unrelated checkout's daemons on a shared
# box. Restarts here are pidfile-scoped, TERM-then-KILL, and wait for the process
# to actually be gone before starting its replacement.
#
#   ./run_pipeline_daemons.sh              restart everything (default)
#   ./run_pipeline_daemons.sh --start      start only what is not running
#   ./run_pipeline_daemons.sh --stop       stop everything
#   ./run_pipeline_daemons.sh --status     report, change nothing
#   ./run_pipeline_daemons.sh --dry-run    print the plan
#   ./run_pipeline_daemons.sh --only jury  act on one daemon
#   ./run_pipeline_daemons.sh --help
#
# Environment:
#   FLUX_HOME, FLUX_OUT_DIR, FLUX_BIN, ARCANE_PYTHON
#   ARCANE_PROFILE           rtx-pro-6000 (default) | rtx-pro-6000-x4 | b200 | b300
#   ARCANE_LAYOUT            balanced | dense | tp   (multi-GPU profiles)
#   ARCANE_DAEMONS           space-separated subset, default "studio jury feeder"
#   FLUX_STUDIO_ADDR         e.g. 127.0.0.1:7860 — passed through as --addr
#   FLUX_STUDIO_ARGS         extra args for `flux serve studio`
#   STOP_GRACE               seconds to wait for a clean exit (default 10)
#   NO_COLOR / FLUX_NO_COLOR / FLUX_FORCE_COLOR
# ==============================================================================
set -euo pipefail

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
# House style — mirrors internal/ui/ui.go. Colour off when piped or nohup'd.
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

ui_rule() { printf '%s%s%s\n' "$C_INDIGO" "$RULE" "$C_RESET"; }
ui_thin() { printf '%s%s%s\n' "$C_LINE" "$RULE" "$C_RESET"; }
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

die() { ui_bad "$*"; exit 1; }

ACTION="restart"
ONLY=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --start)   ACTION="start" ;;
    --stop)    ACTION="stop" ;;
    --restart) ACTION="restart" ;;
    --status)  ACTION="status" ;;
    --dry-run) ACTION="dry-run" ;;
    --only)    shift; ONLY="${1:-}"; [ -n "$ONLY" ] || die "--only needs a daemon name" ;;
    -h|--help) awk 'NR>1 && /^#/ {sub(/^# ?/, ""); print; next} NR>1 {exit}' "$_self"; exit 0 ;;
    *) die "unknown argument: $1 (try --help)" ;;
  esac
  shift
done

# ------------------------------------------------------------------------------
# Interpreter. Probed by round-tripping a token, not by exit status: a broken
# shim can exit 0 while producing nothing.
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

ui_header "pipeline daemons" "$ACTION · $(basename "$FLUX_HOME")"

PY=$(pick_python || true)
[ -n "$PY" ] || die "no Python 3.11+ with stdlib tomllib on PATH. Set ARCANE_PYTHON=/path/to/python3."
[ -f "$FLUX_HOME/pipeline_paths.py" ] || die "pipeline_paths.py not found under $FLUX_HOME"

# ------------------------------------------------------------------------------
# Every path in one call. Tab-separated: no jq, and spaces cannot split a field.
# ------------------------------------------------------------------------------
PATHS=$(cd "$FLUX_HOME" && "$PY" - <<'PYPATHS'
import sys
import pipeline_paths as pp
cfg = {}
try:
    cfg = pp.load_continuum()
except Exception:            # a malformed toml must not stop us stopping daemons
    pass
sys.stdout.write("\t".join(str(x) for x in [
    pp.FLUX_HOME, pp.OUT_DIR, pp.FLUXD_DIR, pp.LOG_DIR, pp.FLUX_BIN,
    pp.STUDIO_LOG, pp.JURY_LOG, pp.FEEDER_LOG, pp.FLUXD_SOCK,
    cfg.get("governor_base_url", pp.GOVERNOR_BASE_URL),
    cfg.get("profile", pp.active_profile()),
]) + "\n")
PYPATHS
) || die "could not resolve pipeline paths (see the traceback above)"

IFS='	' read -r P_HOME P_OUT P_FLUXD P_LOGDIR P_BIN L_STUDIO L_JURY L_FEEDER \
    P_SOCK GOV_URL PROFILE <<EOF
$PATHS
EOF

export FLUX_OUT_DIR="${FLUX_OUT_DIR:-$P_OUT}"
export GOVERNOR_BASE_URL="${GOVERNOR_BASE_URL:-$GOV_URL}"
export ARCANE_PROFILE="${ARCANE_PROFILE:-$PROFILE}"
export PYTHONPATH="$FLUX_HOME${PYTHONPATH:+:$PYTHONPATH}"

# ------------------------------------------------------------------------------
# The daemon table. bash 3.2, so: parallel indexed arrays.
# ------------------------------------------------------------------------------
DAEMONS="${ARCANE_DAEMONS:-studio jury feeder}"

d_label() {
  case "$1" in
    studio) printf 'flux studio' ;;
    jury)   printf 'jury evaluator' ;;
    feeder) printf 'perpetual feeder' ;;
    *)      printf '%s' "$1" ;;
  esac
}
d_log() {
  case "$1" in
    studio) printf '%s' "$L_STUDIO" ;;
    jury)   printf '%s' "$L_JURY" ;;
    feeder) printf '%s' "$L_FEEDER" ;;
    *)      printf '%s/%s.log' "$P_FLUXD" "$1" ;;
  esac
}
d_pidfile() { printf '%s/%s.pid' "$P_FLUXD" "$1"; }
d_script() {
  case "$1" in
    jury)   printf '%s/jury_evaluator.py' "$FLUX_HOME" ;;
    feeder) printf '%s/perpetual_feeder.py' "$FLUX_HOME" ;;
    *)      printf '' ;;
  esac
}
# Pattern used only as a fallback when the pidfile is missing. Scoped to
# FLUX_HOME so a second checkout on the same host is never touched.
d_pattern() {
  case "$1" in
    studio) printf '%s serve studio' "$P_BIN" ;;
    *)      d_script "$1" ;;
  esac
}

is_running() {   # is_running <pidfile>  -> echoes pid, returns 0 when alive
  local pf="$1" pid
  [ -f "$pf" ] || return 1
  pid=$(cat "$pf" 2>/dev/null || true)
  case "$pid" in ''|*[!0-9]*) return 1 ;; esac
  kill -0 "$pid" 2>/dev/null || return 1
  printf '%s' "$pid"
}

stop_daemon() {
  local name="$1" pf pid grace waited pat
  pf=$(d_pidfile "$name")
  grace="${STOP_GRACE:-10}"
  pid=$(is_running "$pf" || true)

  if [ -z "$pid" ]; then
    pat=$(d_pattern "$name")
    if [ -n "$pat" ] && pgrep -f "$pat" >/dev/null 2>&1; then
      pid=$(pgrep -f "$pat" | head -1)
      ui_soft "no pidfile; adopted pid $pid by pattern scoped to $FLUX_HOME"
    fi
  fi

  if [ -z "$pid" ]; then
    ui_skip "$(d_label "$name") — not running"
    rm -f "$pf"
    return 0
  fi

  kill -TERM "$pid" 2>/dev/null || true
  waited=0
  while [ "$waited" -lt "$grace" ] && kill -0 "$pid" 2>/dev/null; do
    sleep 1
    waited=$((waited + 1))
  done
  if kill -0 "$pid" 2>/dev/null; then
    ui_warn "$(d_label "$name") ignored SIGTERM for ${grace}s — escalating to SIGKILL"
    kill -KILL "$pid" 2>/dev/null || true
    sleep 1
  fi
  if kill -0 "$pid" 2>/dev/null; then
    ui_bad "$(d_label "$name") (pid $pid) will not die"
    return 1
  fi
  ui_good "$(d_label "$name") stopped (pid $pid, ${waited}s)"
  rm -f "$pf"
  return 0
}

start_daemon() {
  local name="$1" pf log pid script
  pf=$(d_pidfile "$name"); log=$(d_log "$name")

  pid=$(is_running "$pf" || true)
  if [ -n "$pid" ]; then
    ui_skip "$(d_label "$name") — already running (pid $pid)"
    return 0
  fi

  mkdir -p "$(dirname "$log")" "$P_FLUXD"

  if [ "$name" = "studio" ]; then
    if [ ! -x "$P_BIN" ]; then
      ui_na "$(d_label "$name") — no flux binary at $P_BIN (set FLUX_BIN, or run 'make flux')"
      return 0
    fi
    set -- serve studio
    [ -n "${FLUX_STUDIO_ADDR:-}" ] && set -- "$@" --addr "$FLUX_STUDIO_ADDR"
    # shellcheck disable=SC2086
    [ -n "${FLUX_STUDIO_ARGS:-}" ] && set -- "$@" $FLUX_STUDIO_ARGS
    nohup "$P_BIN" "$@" >>"$log" 2>&1 &
    pid=$!
  else
    script=$(d_script "$name")
    if [ ! -f "$script" ]; then
      ui_bad "$(d_label "$name") — $script does not exist; not started"
      return 1
    fi
    nohup "$PY" -u "$script" >>"$log" 2>&1 &
    pid=$!
  fi

  printf '%s\n' "$pid" > "$pf"
  sleep 1
  if kill -0 "$pid" 2>/dev/null; then
    ui_good "$(d_label "$name") started (pid $pid)"
    ui_soft "$log"
    return 0
  fi
  ui_bad "$(d_label "$name") exited immediately — see $log"
  rm -f "$pf"
  return 1
}

status_daemon() {
  local name="$1" pf pid log
  pf=$(d_pidfile "$name"); log=$(d_log "$name")
  pid=$(is_running "$pf" || true)
  if [ -n "$pid" ]; then
    printf '  %s●%s %-18s %s%-9s%s %spid %-8s %s%s\n' \
      "$C_MINT" "$C_RESET" "$(d_label "$name")" "$C_MINT" "RUNNING" "$C_RESET" \
      "$C_DIM" "$pid" "$log" "$C_RESET"
  else
    printf '  %s○%s %-18s %s%-9s%s %s%s%s\n' \
      "$C_INK" "$C_RESET" "$(d_label "$name")" "$C_AMBER" "STOPPED" "$C_RESET" \
      "$C_DIM" "$log" "$C_RESET"
  fi
}

selected() {
  [ -z "$ONLY" ] && return 0
  [ "$ONLY" = "$1" ]
}

# ------------------------------------------------------------------------------
# Posture
# ------------------------------------------------------------------------------
ui_section "posture"
ui_kv "profile"  "$PROFILE"
ui_kv "flux home" "$P_HOME"
ui_kv "out dir"   "$P_OUT"
ui_kv "log dir"   "$P_LOGDIR"
ui_kv "state dir" "$P_FLUXD"
ui_kv "flux bin"  "$P_BIN$([ -x "$P_BIN" ] || printf ' %s(missing)%s' "$C_AMBER" "$C_RESET")"
ui_kv "governor"  "$GOVERNOR_BASE_URL"
ui_kv "daemons"   "${ONLY:-$DAEMONS}"

if [ "$ACTION" = "status" ]; then
  ui_section "daemon status"
  for d in $DAEMONS; do selected "$d" && status_daemon "$d"; done
  if [ -S "$P_SOCK" ]; then
    printf '  %s●%s %-18s %s%-9s%s %s%s%s\n' "$C_MINT" "$C_RESET" "flux worker uds" \
      "$C_MINT" "LIVE" "$C_RESET" "$C_DIM" "$P_SOCK" "$C_RESET"
  else
    printf '  %s○%s %-18s %s%-9s%s %s%s%s\n' "$C_INK" "$C_RESET" "flux worker uds" \
      "$C_AMBER" "ABSENT" "$C_RESET" "$C_DIM" "$P_SOCK" "$C_RESET"
  fi
  printf '\n'
  exit 0
fi

if [ "$ACTION" = "dry-run" ]; then
  ui_section "plan"
  for d in $DAEMONS; do
    selected "$d" || continue
    ui_kv "$d" "$(d_log "$d")"
    ui_soft "pidfile $(d_pidfile "$d")"
  done
  printf '\n'
  exit 0
fi

# Only creating directories once we know we are actually going to run something.
(cd "$FLUX_HOME" && "$PY" -c 'import pipeline_paths as p; p.ensure_dirs()') \
  || ui_warn "could not create every state directory under $P_OUT"

FAILURES=0

if [ "$ACTION" = "stop" ] || [ "$ACTION" = "restart" ]; then
  ui_step "Stopping"
  for d in $DAEMONS; do
    selected "$d" || continue
    stop_daemon "$d" || FAILURES=$((FAILURES + 1))
  done
fi

if [ "$ACTION" = "start" ] || [ "$ACTION" = "restart" ]; then
  ui_step "Starting"
  for d in $DAEMONS; do
    selected "$d" || continue
    start_daemon "$d" || FAILURES=$((FAILURES + 1))
  done
fi

# ------------------------------------------------------------------------------
# Summary
# ------------------------------------------------------------------------------
ui_header "daemon summary" "$PROFILE · $ACTION"
for d in $DAEMONS; do selected "$d" && status_daemon "$d"; done
ui_thin
if [ "$FAILURES" -gt 0 ]; then
  printf '  %s%s✕ %d daemon(s) did not reach the requested state%s\n\n' \
    "$C_BOLD" "$C_ROSE" "$FAILURES" "$C_RESET"
  exit 1
fi
printf '  %s%s● all requested daemons are in the requested state%s\n\n' \
  "$C_BOLD" "$C_MINT" "$C_RESET"
