#!/usr/bin/env bash
# Render supervisord.conf from the environment.
#
# The shipped container inlined model ids and GPU fractions directly into the
# supervisor command lines, so fixing a typo meant rebuilding and re-pushing a
# 10.5 GiB tarball. Here the conf is generated at boot, so the same image runs
# any posture.
set -euo pipefail

conf=/etc/supervisor/conf.d/supervisord.conf
mkdir -p "$(dirname "$conf")"

cat > "$conf" <<EOF
[supervisord]
nodaemon=false
logfile=/var/log/supervisord.log
pidfile=/var/run/supervisord.pid

; Without these three sections supervisorctl has no socket to talk to, so
; neither "supervisorctl status" nor the entrypoint's shutdown trap works.
; The shipped container omitted them.
[unix_http_server]
file=/var/run/supervisor.sock
chmod=0700

[rpcinterface:supervisor]
supervisor.rpcinterface_factory = supervisor.rpcinterface:make_main_rpcinterface

[supervisorctl]
serverurl=unix:///var/run/supervisor.sock

[program:flux-tea]
command=/usr/local/bin/flux tea serve --addr ${BEAUTY_TEA_ADDR}
directory=/root/FLUX
autostart=true
autorestart=true
stdout_logfile=/var/log/flux-tea.log
stderr_logfile=/var/log/flux-tea.err
priority=10

[program:governor-gateway]
command=/usr/local/bin/gemstone governor gateway serve --listen 127.0.0.1:8000 --upstream http://127.0.0.1:9000/v1
directory=/root
environment=GEMSTONE_AGENTIC_MEMORY="1"
autostart=true
autorestart=true
stdout_logfile=/var/log/governor-gateway.log
stderr_logfile=/var/log/governor-gateway.err
priority=20

[program:governor-vllm]
command=python3 -m vllm.entrypoints.openai.api_server
  --host 127.0.0.1 --port 9000
  --model ${BEAUTY_GOVERNOR_MODEL}
  --served-model-name governor
  --max-model-len ${BEAUTY_GOVERNOR_CTX}
  --gpu-memory-utilization ${BEAUTY_GOVERNOR_UTIL}
  --max-num-seqs ${BEAUTY_MAX_NUM_SEQS}
  --kv-cache-dtype auto --trust-remote-code
  --enable-prefix-caching --max-num-batched-tokens 8192
  --enable-auto-tool-choice --tool-call-parser gemma4
EOF

if [ -n "${BEAUTY_GOVERNOR_DRAFTER:-}" ] && [ "${BEAUTY_SPEC_TOKENS:-0}" != "0" ]; then
    # Single quotes are required: supervisord strips bare double quotes from
    # command=, which turns the JSON into {method:mtp,...} and vLLM rejects it.
    printf "  --speculative-config '"'{"method":"mtp","model":"%s","num_speculative_tokens":%s}'"'\n" \
        "$BEAUTY_GOVERNOR_DRAFTER" "$BEAUTY_SPEC_TOKENS" >> "$conf"
fi

cat >> "$conf" <<EOF
directory=/root
autostart=true
autorestart=true
stdout_logfile=/var/log/governor-vllm.log
stderr_logfile=/var/log/governor-vllm.err
priority=30
EOF

# Aux models are opt-in.
#
# Two things make co-resident vLLM servers work, and the shipped config got
# both wrong:
#
#   1. --gpu-memory-utilization is NOT an additive share. Each server reads it
#      as an absolute fraction of the whole card and then subtracts memory other
#      processes already hold. So the Nth server needs the CUMULATIVE fraction,
#      not its own slice, or it computes a negative KV budget and dies with
#      "No available memory for the cache blocks".
#
#   2. They must not profile concurrently. supervisord starts every program at
#      once, so two engines measure free memory while the other is still
#      allocating and both mis-size. Each aux server waits for the governor's
#      /health before starting.
cumulative="${BEAUTY_GOVERNOR_UTIL}"

add_aux() {
    local name="$1" model="$2" util="$3" port="$4" extra="${5:-}"
    [ -n "$model" ] || return 0
    [ "$util" != "0.0" ] && [ "$util" != "0" ] || return 0

    cumulative="$(awk -v a="$cumulative" -v b="$util" 'BEGIN{printf "%.4f", a+b}')"

    cat >> "$conf" <<EOF

[program:${name}]
command=bash -c 'until curl -sf --max-time 3 http://127.0.0.1:9000/health >/dev/null 2>&1; do sleep 5; done; exec python3 -m vllm.entrypoints.openai.api_server --host 127.0.0.1 --port ${port} --model ${model} --served-model-name ${name} --gpu-memory-utilization ${cumulative} --trust-remote-code ${extra}'
directory=/root
autostart=true
autorestart=true
startsecs=30
stdout_logfile=/var/log/${name}.log
stderr_logfile=/var/log/${name}.err
priority=40
EOF
}

add_aux coder  "${BEAUTY_CODER_MODEL:-}"  "${BEAUTY_CODER_UTIL:-0.0}"  8001 "--max-model-len 16384"
add_aux vision "${BEAUTY_VISION_MODEL:-}" "${BEAUTY_VISION_UTIL:-0.0}" 8002 "--max-model-len 8192"

echo "rendered $conf"
