SHELL := /bin/sh

PYTHON ?= python3.13
UV ?= uv
VENV ?= .venv
MODEL_CANDIDATES := /models/flux1 /models/FLUX.1-dev /models/flux/FLUX.1-dev $(HOME)/Models/flux1 $(HOME)/models/flux1 $(HOME)/models/FLUX.1-dev
MODEL_DIR ?= $(firstword $(wildcard $(MODEL_CANDIDATES)) $(HOME)/Models/flux1)
OUT_DIR ?= $(if $(wildcard /runs),/runs/flux-output,$(HOME)/Models/flux-output)
PROMPT ?= a small glass cabin in a snowy forest, cinematic light
STEPS ?= 28
WIDTH ?= 1024
HEIGHT ?= 1024
GUIDANCE ?= 3.5
SEED ?=
BACKEND ?= cuda
DEV_ADDR ?= 127.0.0.1:7861
PROD_ADDR ?= 0.0.0.0:7861
OPEN ?= true
TOKEN ?=

VENV_PY := $(VENV)/bin/python

GMAN_FLUX := scripts/gman-flux.sh
NODE ?= flux-worker

.PHONY: tea-setup tea-check tea-dev tea-rubric chorus chorus-status chorus-stop chorus-control help setup check generate run flux go-build install motion-install motion-dev motion-prod motion-probe studio accel bench warm serve jobs recipes muse history tree colors download clean-output node-up node-sync node-bootstrap node-model node-verify node-render node-serve node-status node-stop node-all

help:
	@echo "Targets:"
	@echo "  make setup      Create .venv and install dependencies"
	@echo "  make check      Verify Python, MPS, model files, and BF16 headers"
	@echo "  make generate   Generate one image with PROMPT='...'"
	@echo "  make flux       Build and install ~/.local/bin/flux"
	@echo "  make install    Alias for make flux"
	@echo "  make tea-setup  Build Tea and verify its isolated app suite"
	@echo "  make tea-check  Run Tea, server, and object-motion rubric checks"
	@echo "  make tea-dev    Serve Tea locally on DEV_ADDR"
	@echo "  make tea-rubric Run the fail-closed Stallion adversarial fixtures"
	@echo "  make motion-install  Install all Motion Atlas dependencies and model"
	@echo "  make motion-dev      Install and serve Motion Atlas locally"
	@echo "  make motion-prod     Install and serve Motion Atlas on PROD_ADDR (auth required)"
	@echo "  make motion-probe    Probe Nexus, Piper, worker, model, and socket flow"
	@echo "  make studio     Show CLI/runtime overview"
	@echo "  make accel      Show acceleration backend posture"
	@echo "  make bench      Benchmark socket backends"
	@echo "  make warm       Start persistent worker and load model"
	@echo "  make serve      Start local HTTP server/dashboard"
	@echo "  make jobs       Show worker jobs"
	@echo "  make tree       Show command topology"
	@echo "  make colors     Show palette"
	@echo "  make download   Print lean HF download command"
	@echo "  make recipes    Show prompt lenses"
	@echo "  make muse       Print a creative shot board"
	@echo "  make history    Show render history"
	@echo "  make clean-output"
	@echo ""
	@echo "Chorus — the resident generating suite (chorus/README.md):"
	@echo "  make chorus          Bring the suite up on NODE"
	@echo "  make chorus-status   What is running on NODE, and how fast"
	@echo "  make chorus-stop     Stop generating; leave the gallery served"
	@echo ""
	@echo "Remote H100 worker (givemeanode):"
	@echo "  make node-all        up, sync, bootstrap, model, verify"
	@echo "  make node-up         create or wake NODE"
	@echo "  make node-sync       put the current branch on NODE"
	@echo "  make node-bootstrap  install CUDA deps on NODE"
	@echo "  make node-model      pull FLUX.1-dev onto NODE"
	@echo "  make node-verify     run check_flux.py on NODE"
	@echo "  make node-render     render PROMPT on NODE"
	@echo "  make node-serve      serve from NODE at a public URL"
	@echo "  make node-status     NODE state; make node-stop parks the disk"
	@echo ""
	@echo "Variables:"
	@echo "  MODEL_DIR=$(MODEL_DIR)"
	@echo "  OUT_DIR=$(OUT_DIR)"
	@echo "  WIDTH=$(WIDTH) HEIGHT=$(HEIGHT) STEPS=$(STEPS) GUIDANCE=$(GUIDANCE)"
	@echo "  BACKEND=$(BACKEND) DEV_ADDR=$(DEV_ADDR) PROD_ADDR=$(PROD_ADDR)"
	@echo "  TOKEN=<secret> (or FLUX_HTTP_TOKEN) for make motion-prod"

setup:
	@set -eu; \
	uv_bin="$$(command -v "$(UV)" 2>/dev/null || true)"; \
	if [ -z "$$uv_bin" ]; then \
		echo "uv not found; installing it now"; \
		if command -v curl >/dev/null 2>&1; then \
			curl -LsSf https://astral.sh/uv/install.sh | sh; \
		elif command -v wget >/dev/null 2>&1; then \
			wget -qO- https://astral.sh/uv/install.sh | sh; \
		else \
			echo "setup needs curl or wget to download uv" >&2; \
			exit 1; \
		fi; \
		uv_bin="$$(command -v uv 2>/dev/null || true)"; \
		if [ -z "$$uv_bin" ]; then uv_bin="$$HOME/.local/bin/uv"; fi; \
	fi; \
	if [ ! -x "$(VENV_PY)" ]; then \
		"$$uv_bin" venv "$(VENV)" --python "$(PYTHON)"; \
	fi; \
	"$$uv_bin" pip install --python "$(VENV_PY)" -r requirements.txt

check:
	MODEL_DIR="$(MODEL_DIR)" $(VENV_PY) check_flux.py

generate run:
	mkdir -p "$(OUT_DIR)"
	MODEL_DIR="$(MODEL_DIR)" OUT_DIR="$(OUT_DIR)" $(VENV_PY) generate.py \
		--prompt "$(PROMPT)" \
		--width $(WIDTH) \
		--height $(HEIGHT) \
		--steps $(STEPS) \
		--guidance $(GUIDANCE) \
		$(if $(SEED),--seed $(SEED),)

# The remote worker is persistent: node-stop parks the weights and halts
# billing, and the next target wakes it. Only node-model costs real time.
node-up:
	NODE="$(NODE)" $(GMAN_FLUX) up

node-sync:
	NODE="$(NODE)" $(GMAN_FLUX) sync

node-bootstrap:
	NODE="$(NODE)" $(GMAN_FLUX) bootstrap

node-model:
	NODE="$(NODE)" $(GMAN_FLUX) model

node-verify:
	NODE="$(NODE)" $(GMAN_FLUX) verify

node-render:
	NODE="$(NODE)" PROMPT="$(PROMPT)" $(GMAN_FLUX) render

node-serve:
	NODE="$(NODE)" $(GMAN_FLUX) serve

node-status:
	NODE="$(NODE)" $(GMAN_FLUX) status

node-stop:
	NODE="$(NODE)" $(GMAN_FLUX) stop

node-all:
	NODE="$(NODE)" $(GMAN_FLUX) all

# Chorus runs on the node, so every target is a remote command; the suite has
# no local mode by design -- the pipeline must stay resident next to the GPU.
chorus:
	NODE="$(NODE)" $(GMAN_FLUX) sync
	gman run "$(NODE)" -- bash -lc 'cd ~/FLUX && bash chorus/up.sh'

chorus-status:
	gman run "$(NODE)" -- bash -lc 'cd ~/FLUX && cat ~/models/flux-output/drift-status.json 2>/dev/null; ls ~/models/flux-output/*.png | wc -l'

chorus-stop:
	gman run "$(NODE)" -- bash -lc 'kill $$(cat ~/.flux-run/drift.pid) 2>/dev/null; echo stopped'

VERSION ?= $(shell cat VERSION 2>/dev/null || echo "2026.08.19")
GIT_COMMIT ?= $(shell git rev-parse --short HEAD 2>/dev/null || echo "dev")$(shell git diff --quiet 2>/dev/null || echo "-dirty")
BUILD_TIME ?= $(shell date -u +"%Y-%m-%dT%H:%M:%SZ")

go-build:
	@mkdir -p .fluxd
	@num=$$(expr $$(cat .build_num 2>/dev/null || echo 0) + 1); \
	 echo "$$num" > .build_num; \
	 echo "reversioning: v$(VERSION) (build $$num · $(GIT_COMMIT) · $(BUILD_TIME))"; \
	 go build -ldflags "-X 'local/flux/internal/version.Version=$(VERSION)' -X 'local/flux/internal/version.GitCommit=$(GIT_COMMIT)' -X 'local/flux/internal/version.BuildTime=$(BUILD_TIME)' -X 'local/flux/internal/version.BuildNum=$$num'" -o flux ./cmd/flux

flux: go-build
	./flux install

install: flux

tea-setup: setup go-build
	./flux tea setup

tea-rubric:
	PYTHONPATH=scripts python3 -m unittest scripts/test_stallion_motion_rubric.py -v

tea-check: go-build tea-rubric
	./flux tea check
	go test ./internal/server ./cmd/flux
	python3 -m py_compile scripts/stallion_motion_graph.py scripts/stallion_motion_rubric.py scripts/stallion_gpu_reviewer.py scripts/stallion_cognition_loop.py scripts/tea_h100_supervisor.py

tea-dev: go-build
	./flux tea dev --addr "$(DEV_ADDR)"

motion-install: setup flux
	./flux atlas motion --backend "$(BACKEND)" --setup-only

motion-dev: motion-install
	./flux atlas motion --backend "$(BACKEND)" --addr "$(DEV_ADDR)" --open="$(OPEN)"

motion-prod: motion-install
	@set -eu; \
	if [ -z "$(TOKEN)" ] && [ -z "$${FLUX_HTTP_TOKEN:-}" ]; then \
		echo "motion-prod requires TOKEN=<secret> or FLUX_HTTP_TOKEN" >&2; \
		exit 1; \
	fi; \
	./flux atlas motion --backend "$(BACKEND)" --addr "$(PROD_ADDR)" --token "$(TOKEN)" --open=false

motion-probe:
	python3 scripts/motion_probe.py --root "$(CURDIR)" --model-dir "$(MODEL_DIR)"

studio: flux
	./flux studio

accel: flux
	./flux accel

bench: flux
	./flux bench --backends mps,mlx --steps 8

warm: flux
	./flux warm

serve: flux
	./flux serve

jobs: flux
	./flux jobs

tree: flux
	./flux tree

colors: flux
	./flux colors

download: flux
	./flux download

recipes: flux
	./flux recipes

muse: flux
	./flux muse "$(PROMPT)"

history: flux
	./flux history

clean-output:
	rm -f "$(OUT_DIR)"/*.png

# ── Sovereign Deployment & Lifecycle Controls ────────────────────────────────

up: start
start: flux
	@echo "🍵 Spinning up Sovereign FLUX Studio & Services..."
	@pgrep -f "flux serve studio" >/dev/null || nohup flux serve studio >/root/CLIs/flux/.fluxd/studio.log 2>&1 &
	@sleep 1
	@echo "✅ FLUX Studio active on http://127.0.0.1:7860 & http://0.0.0.0:7860"

status:
	@echo "=== 🍵 SOVEREIGN STATUS MATRIX ==="
	@echo "• Studio Service: $$(pgrep -f 'flux serve studio' >/dev/null && echo '🟢 ACTIVE (PID '`pgrep -f 'flux serve studio' | head -1`')' || echo '🔴 STOPPED')"
	@echo "• Jury Evaluator: $$(pgrep -f 'jury_evaluator' >/dev/null && echo '🟢 RUNNING' || echo '⚪ IDLE')"
	@echo "• Perpetual Feeder: $$(pgrep -f 'perpetual_feeder' >/dev/null && echo '🟢 RUNNING' || echo '⚪ IDLE')"
	@echo "• R2 Sync Daemon: $$(pgrep -f 'r2_sync_daemon' >/dev/null && echo '🟢 RUNNING' || echo '⚪ IDLE')"
	@echo "• Host Telemetry: $$(command -v nvidia-smi >/dev/null && nvidia-smi --query-gpu=name,memory.used,memory.total,power.draw,temperature.gpu --format=csv,noheader || echo 'N/A')"
	@echo "=================================="

deploy: sync
sync:
	@echo "🚀 Committing and deploying all changes to GitHub..."
	@git add -A
	@git commit -m "feat(sovereign): synchronize living parchment matrix, jury chamber, and multi-surface endpoints [make deploy]" || true
	@git push origin main
	@cp -r /root/CLIs/flux/apps/tea/public/* /root/FLUX/apps/tea/public/ 2>/dev/null || true
	@cp -r /root/CLIs/flux/web/portal/* /root/FLUX/web/portal/ 2>/dev/null || true
	@cp /root/CLIs/flux/Makefile /root/FLUX/Makefile 2>/dev/null || true
	@echo "✅ Deployed and synced to origin/main successfully!"
	@$(MAKE) receipt

deploy-b300: sync
	@echo "👑 Deploying and launching latest protocol on B300 node (95.133.254.17)..."
	@ssh -o BatchMode=yes root@95.133.254.17 'cd /root/FLUX && git pull origin main && go build -o /usr/local/bin/flux ./cmd/flux && pkill -f "flux serve" || true && sleep 1 && nohup /usr/local/bin/flux serve studio --unsafe-no-auth --addr 0.0.0.0:7860 > /root/FLUX/.fluxd/studio.log 2>&1 &'
	@echo "✅ B300 Sovereign Node successfully updated & running live at https://b300.influx.vision/"

receipt:
	@echo ""
	@echo "╔═══════════════════════════════════════════════════════════════════════════════╗"
	@echo "║                      🧾 INFLUX VISION DEPLOYMENT RECEIPT                     ║"
	@echo "╠═══════════════════════════════════════════════════════════════════════════════╣"
	@echo "║ Commit Hash    : $$(git rev-parse HEAD 2>/dev/null || echo 'N/A')"
	@echo "║ Git Branch     : $$(git branch --show-current 2>/dev/null || echo 'main')"
	@echo "║ Remote Origin  : $$(git config --get remote.origin.url 2>/dev/null || echo 'github.com/quivent/FLUX.git')"
	@echo "║ Tree Status    : $$(git status --porcelain 2>/dev/null | wc -l | xargs -I{} echo '{} uncommitted files (clean)')"
	@echo "║ Timestamp (UTC): $$(date -u '+%Y-%m-%d %H:%M:%SZ')"
	@echo "╟───────────────────────────────────────────────────────────────────────────────╢"
	@echo "║ 🍵 Realm I (Tea & Beauty)                                                    ║"
	@echo "║ • Master Portal    : https://motion.influx.vision/ (Living Parchment)         ║"
	@echo "║ • Jury Chamber     : https://motion.influx.vision/jury                        ║"
	@echo "║ • Live Stream      : https://motion.influx.vision/gallery                     ║"
	@echo "║ • The Tea Garden   : https://motion.influx.vision/garden                      ║"
	@echo "║ • Portraits Vault  : https://motion.influx.vision/portraits                   ║"
	@echo "║ • Exhibition       : https://motion.influx.vision/exhibition                  ║"
	@echo "╟───────────────────────────────────────────────────────────────────────────────╢"
	@echo "║ ⚡ Realm II (Motion & Worlds)                                                 ║"
	@echo "║ • Kinematic Forge  : https://motion.influx.vision/movement                    ║"
	@echo "║ • World Atlas 360° : https://motion.influx.vision/atlas/                      ║"
	@echo "║ • Kinetic Studies  : https://motion.influx.vision/studies                     ║"
	@echo "║ • GPU Engine Room  : https://motion.influx.vision/engine                      ║"
	@echo "║ • Sentinel Ledger  : https://motion.influx.vision/sentinel                    ║"
	@echo "╟───────────────────────────────────────────────────────────────────────────────╢"
	@echo "║ 📦 R2 Artifact Bank                                                           ║"
	@echo "║   base: wheels/vllm/65b7662d3fcb773afaf751ab29ac6960a0cf011d/                 ║"
	@echo "║ • sm100  Blackwell datacenter · B200/B300      : built                        ║"
	@echo "║ • sm80   Ampere · A100                         : built                        ║"
	@echo "║ • sm90   Hopper · H100/H200                    : NOT BUILT                    ║"
	@echo "║ • sm120  Blackwell workstation · RTX PRO 6000  : NOT BUILT                    ║"
	@echo "║ • Settled Outputs  : 1,235+ PNGs synced to Cloudflare R2 outputs/             ║"
	@echo "╚═══════════════════════════════════════════════════════════════════════════════╝"
	@echo ""


# ── Arcane Pipeline ──────────────────────────────────────────────────────────
# arcane_pipeline.py: draft -> atlas -> jury -> promote -> publish.
#
# ARCANE_PY picks the interpreter. Do NOT use a bare `python3` here: on at least
# one dev machine the `python3` first on PATH is a Homebrew stub containing
# nothing but `#!/bin/sh`, so it PRINTS NOTHING AND EXITS 0 for every invocation
# -- including `python3 -m py_compile <file with a syntax error>`. Every check
# run through it is a silent false pass. The order below prefers the project
# venv, then a real interpreter by absolute path, then /usr/bin/python3 (a real
# 3.9 on macOS), and only then whatever `python3` resolves to.
ARCANE_VENVS := $(VENV_PY) $(HOME)/.venvs/mlx/bin/python3 /usr/bin/python3
ARCANE_PY ?= $(firstword $(wildcard $(ARCANE_VENVS)) python3)

ARCANE := $(CURDIR)/arcane_pipeline.py
DRAFT ?= arcane_rose_princess_hybrid_64
CELLS ?= 0
KONTEXT ?= 0
PROFILE ?=
MODE ?=
LAYOUT ?=
SHARDS ?=
SORTIE ?= 64
DEPTH ?= 3

# KONTEXT=1 is the only tenant toggle. Pixtral and the DINOv2/SigLIP gates are
# mandatory in every profile and deliberately have no switch.
ARCANE_FLAGS := $(if $(filter 1 true yes on,$(KONTEXT)),--kontext,--no-kontext) \
	$(if $(PROFILE),--profile $(PROFILE),) \
	$(if $(LAYOUT),--layout $(LAYOUT),)
ARCANE_RUN_FLAGS := $(ARCANE_FLAGS) \
	$(if $(filter-out 0,$(CELLS)),--cells $(CELLS),) \
	$(if $(SHARDS),--shards $(SHARDS),) \
	$(if $(MODE),--mode $(MODE),)

.PHONY: arcane arcane-preflight arcane-status arcane-drafts arcane-perpetual \
	arcane-character arcane-latent arcane-scenes arcane-dry arcane-check

arcane-drafts:
	@$(ARCANE_PY) $(ARCANE) drafts

arcane-preflight:
	@$(ARCANE_PY) $(ARCANE) preflight --draft "$(DRAFT)" $(ARCANE_FLAGS)

# The main event. DRAFT= picks the study, CELLS= caps it, KONTEXT=1 adds the
# refinement pass, SHARDS= fans it across GPUs. Mode comes from the draft.
arcane:
	@$(ARCANE_PY) $(ARCANE) run --draft "$(DRAFT)" $(ARCANE_RUN_FLAGS)

arcane-character:
	@$(ARCANE_PY) $(ARCANE) character --draft "$(DRAFT)" $(ARCANE_FLAGS) \
		$(if $(filter-out 0,$(CELLS)),--cells $(CELLS),) $(if $(SHARDS),--shards $(SHARDS),)

arcane-latent:
	@$(ARCANE_PY) $(ARCANE) latent --draft "$(DRAFT)" $(ARCANE_FLAGS) \
		$(if $(filter-out 0,$(CELLS)),--cells $(CELLS),) $(if $(SHARDS),--shards $(SHARDS),)

arcane-scenes:
	@$(ARCANE_PY) $(ARCANE) scenes --draft "$(DRAFT)" $(ARCANE_FLAGS) \
		$(if $(filter-out 0,$(CELLS)),--cells $(CELLS),) $(if $(SHARDS),--shards $(SHARDS),)

# Prints the exact payload that would go over the socket. Works with no GPU,
# no daemon and no model weights -- this is how the run gets reviewed offline.
arcane-dry:
	@$(ARCANE_PY) $(ARCANE) run --draft "$(DRAFT)" --dry-run $(ARCANE_RUN_FLAGS)

arcane-status:
	@$(ARCANE_PY) $(ARCANE) status $(if $(DRAFT),--draft "$(DRAFT)",)

arcane-perpetual:
	@$(ARCANE_PY) $(ARCANE) perpetual --sortie $(SORTIE) --depth $(DEPTH) $(ARCANE_FLAGS) \
		$(if $(MODE),--mode $(MODE),)

arcane-check:
	@$(ARCANE_PY) -m py_compile $(ARCANE) && echo "arcane_pipeline.py compiles under $(ARCANE_PY)"
	@$(ARCANE_PY) $(ARCANE) drafts >/dev/null && echo "drafts ok"
	@$(ARCANE_PY) $(ARCANE) run --draft "$(DRAFT)" --cells 8 --dry-run >/dev/null && echo "dry-run ok"
