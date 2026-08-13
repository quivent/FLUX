#!/bin/bash
# Bring up a properly tuned vLLM judge on an 80GB H100.
#
# Every setting here was paid for. The governor's own manifesto mandates
# speculative decoding with a draft model, certified on an H200 NVL 141GB. On
# an 80GB H100 that config fails two ways, both reproducible:
#
#   - at gpu-memory-utilization >= 0.87 the draft OOMs during CUDA graph
#     capture, after grabbing ~36GB;
#   - the draft's sliding-window layers crash FlashInfer on SM90 outright
#     (NotImplementedError: FlashInfer backend on SM90 currently crashes with
#     sliding-window attention).
#
# So: no speculative config, and TRITON_ATTN rather than letting vLLM pick
# FlashInfer.
#
# The other failure this fixes is throughput, not memory. Tonight the judge
# returned HTTP 524 for forty minutes straight because a single engine served
# one request at a time with speculative decoding, and a vision call over a
# contact sheet exceeds the gateway timeout every time. max-num-seqs is the
# setting that stops a panel of seats from starving each other.
set -eu

MODEL="${MODEL:-RedHatAI/gemma-4-31B-it-FP8-dynamic}"
PORT="${PORT:-8000}"
CTX="${CTX:-32768}"
UTIL="${UTIL:-0.90}"
SEQS="${SEQS:-16}"
RUN="${RUN:-$HOME/.vllm-run}"
mkdir -p "$RUN"

# Weights on the persistent volume, never /scratch: scratch is destroyed at
# stop and never snapshotted, and re-pulling 31GB on every wake is the kind of
# cost that quietly becomes the reason nobody restarts anything.
export HF_HOME="${HF_HOME:-$HOME/hf}"
mkdir -p "$HF_HOME"

# SM90 is Hopper. FlashInfer is the default and it is the wrong default here.
export VLLM_ATTENTION_BACKEND="${VLLM_ATTENTION_BACKEND:-TRITON_ATTN}"

if [ -f "$RUN/vllm.pid" ] && kill -0 "$(cat "$RUN/vllm.pid")" 2>/dev/null; then
	echo "already running (pid $(cat "$RUN/vllm.pid"))"
	exit 0
fi

echo "== gpu"
nvidia-smi --query-gpu=name,memory.total,memory.free --format=csv,noheader

echo "== launching $MODEL"
nohup vllm serve "$MODEL" \
	--host 0.0.0.0 --port "$PORT" \
	--served-model-name judge \
	--max-model-len "$CTX" \
	--kv-cache-dtype fp8 \
	--gpu-memory-utilization "$UTIL" \
	--max-num-seqs "$SEQS" \
	--disable-log-requests \
	> "$HOME/vllm.log" 2>&1 &
echo $! > "$RUN/vllm.pid"
echo "pid $(cat "$RUN/vllm.pid"), log ~/vllm.log"

echo "== waiting for the engine (a cold 31B pull is minutes, not seconds)"
for _ in $(seq 1 240); do
	if curl -sS -m 3 "http://127.0.0.1:$PORT/v1/models" >/dev/null 2>&1; then
		echo "up:"
		curl -sS "http://127.0.0.1:$PORT/v1/models" | head -c 300
		echo
		exit 0
	fi
	# A crashed engine must not look like a slow one. This is the failure that
	# reads as "still loading" for an hour.
	if ! kill -0 "$(cat "$RUN/vllm.pid")" 2>/dev/null; then
		echo "ENGINE DIED — last lines:" >&2
		tail -25 "$HOME/vllm.log" >&2
		exit 1
	fi
	sleep 5
done
echo "timed out waiting; tail ~/vllm.log" >&2
exit 1
