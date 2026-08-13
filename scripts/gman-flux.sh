#!/bin/sh
# Drive a FLUX worker on a givemeanode H100 from the laptop.
#
# The node is persistent: `stop` parks the disk with the weights intact and
# billing halted, and any later command wakes it. That makes the worker
# reshapable on demand -- change the repo, re-run `sync`, and the same box
# serves the new code without re-downloading 40 GB of weights.
#
#   ./scripts/gman-flux.sh up          create or wake the node
#   ./scripts/gman-flux.sh authorize   give the node read-only access to the repo
#   ./scripts/gman-flux.sh sync        put this repo's branch on the node
#   ./scripts/gman-flux.sh bootstrap   install deps, make directories
#   ./scripts/gman-flux.sh model       pull FLUX.1-dev via the HF connection
#   ./scripts/gman-flux.sh verify      check_flux.py against the real weights
#   ./scripts/gman-flux.sh render      one image, PROMPT=... to change it
#   ./scripts/gman-flux.sh serve       flux serve, exposed at a public URL
#   ./scripts/gman-flux.sh all         up -> sync -> bootstrap -> model -> verify
#   ./scripts/gman-flux.sh status      node state and spend
#   ./scripts/gman-flux.sh stop        park the disk, stop paying
set -eu

NODE="${NODE:-flux-worker}"
CHIP="${CHIP:-h100}"
# The image/video generation image: torch 2.13 + torchvision on cu129, plus
# ffmpeg and libgl1. cuda-13.3's cu130 wheels have thinner ecosystem coverage.
IMAGE="${IMAGE:-pytorch-2.13-cuda12.9}"
SCRATCH_GIB="${SCRATCH_GIB:-100}"
REPO_URL="${REPO_URL:-git@github.com:quivent/FLUX.git}"
REPO_SLUG="${REPO_SLUG:-quivent/FLUX}"
BRANCH="${BRANCH:-$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo main)}"
REPO_DIR="${REPO_DIR:-\$HOME/FLUX}"
HF_CONNECTION="${HF_CONNECTION:-huggingface}"
HF_REPO="${HF_REPO:-black-forest-labs/FLUX.1-dev}"
MODEL_ROOT="${MODEL_ROOT:-\$HOME/models}"
PORT="${PORT:-7861}"
PROMPT="${PROMPT:-a small glass cabin in a snowy forest, cinematic light}"

on_node() { gman run "$NODE" -- bash -lc "$1"; }
on_node_detached() { gman run "$NODE" -d -- bash -lc "$1"; }

cmd_up() {
	if gman node get "$NODE" >/dev/null 2>&1; then
		echo "node $NODE exists; waking it"
		on_node 'echo awake'
	else
		gman node create --name "$NODE" --chip "$CHIP" --image "$IMAGE" \
			--scratch-gib "$SCRATCH_GIB" --max-wait 2h --wait -y
	fi
}

# FLUX is a private repo, so the node needs its own credential. It generates
# a keypair and keeps the private half on its encrypted disk -- nothing is
# copied from the laptop, and the key we register is read-only and scoped to
# this one repo, so a compromised node cannot write to anything. The node is
# persistent, so this is a one-time cost that survives stops.
cmd_authorize() {
	pubkey=$(gman run "$NODE" -- bash -lc '
		set -eu
		[ -f ~/.ssh/id_ed25519 ] || ssh-keygen -q -t ed25519 -N "" -C "gman-'"$NODE"'" -f ~/.ssh/id_ed25519
		ssh-keyscan -t ed25519 github.com >> ~/.ssh/known_hosts 2>/dev/null
		sort -u -o ~/.ssh/known_hosts ~/.ssh/known_hosts
		cat ~/.ssh/id_ed25519.pub' | tail -1)
	echo "node key: $pubkey"
	if gh repo deploy-key list --repo "$REPO_SLUG" 2>/dev/null | grep -q "gman-$NODE"; then
		echo "deploy key gman-$NODE already registered"
	else
		printf '%s\n' "$pubkey" > "${TMPDIR:-/tmp}/gman-$NODE.pub"
		gh repo deploy-key add "${TMPDIR:-/tmp}/gman-$NODE.pub" \
			--repo "$REPO_SLUG" --title "gman-$NODE"
		rm -f "${TMPDIR:-/tmp}/gman-$NODE.pub"
	fi
}

# Cloning from the remote rather than copying from the laptop: the node has
# the bandwidth, and it forces the branch to actually be pushed, so what runs
# on the GPU is what is in origin -- never a laptop-only diff.
cmd_sync() {
	on_node "set -eu
		if [ -d $REPO_DIR/.git ]; then
			cd $REPO_DIR && git fetch --depth 1 origin '$BRANCH' && git checkout -B '$BRANCH' FETCH_HEAD
		else
			git clone --depth 1 --branch '$BRANCH' '$REPO_URL' $REPO_DIR
		fi
		cd $REPO_DIR && git --no-pager log -1 --oneline"
}

cmd_bootstrap() { on_node "chmod +x $REPO_DIR/deploy/givemeanode/bootstrap.sh && REPO_DIR=$REPO_DIR $REPO_DIR/deploy/givemeanode/bootstrap.sh"; }

# ~54 GB of BF16 weights. The import unpacks a repo's contents FLAT into dest,
# so dest is the model directory itself: importing into $MODEL_ROOT would strew
# model_index.json and transformer/ across the models root and leave
# flux_paths.default_model_dir with nothing it recognises.
cmd_model() {
	gman import "$NODE" --connection "$HF_CONNECTION" --source "$HF_REPO" \
		--dest "$MODEL_ROOT/FLUX.1-dev" --wait
}

cmd_verify() { on_node "cd $REPO_DIR && python3 check_flux.py"; }

cmd_render() {
	on_node "set -eu
		cd $REPO_DIR
		mkdir -p \$HOME/models/flux-output
		OUT_DIR=\$HOME/models/flux-output python3 generate.py --prompt \"$PROMPT\"
		ls -t \$HOME/models/flux-output | head -3"
}

cmd_serve() {
	on_node_detached "cd $REPO_DIR && make serve PROD_ADDR=0.0.0.0:$PORT"
	gman api POST "/preview/nodes/$NODE/endpoints" || \
		echo "expose port $PORT from the MCP surface (expose_port); the CLI has no endpoint verb"
}

cmd_status() {
	gman node get "$NODE"
	gman ps "$NODE" 2>/dev/null | head -10 || true
}

cmd_stop() { gman node stop "$NODE" -y; }

cmd_all() { cmd_up; cmd_authorize; cmd_sync; cmd_bootstrap; cmd_model; cmd_verify; }

case "${1:-}" in
	up) cmd_up ;;
	authorize) cmd_authorize ;;
	sync) cmd_sync ;;
	bootstrap) cmd_bootstrap ;;
	model) cmd_model ;;
	verify) cmd_verify ;;
	render) cmd_render ;;
	serve) cmd_serve ;;
	status) cmd_status ;;
	stop) cmd_stop ;;
	all) cmd_all ;;
	*) sed -n '2,25p' "$0"; exit 2 ;;
esac
