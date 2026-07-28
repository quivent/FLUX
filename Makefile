SHELL := /bin/sh

PYTHON ?= python3.13
UV ?= uv
VENV ?= .venv
MODEL_DIR ?= /Users/joshkornreich/Models/flux1
OUT_DIR ?= /Users/joshkornreich/Models/flux-output
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

.PHONY: help setup check generate run flux go-build install motion-install motion-dev motion-prod studio accel bench warm serve jobs recipes muse history tree colors download clean-output

help:
	@echo "Targets:"
	@echo "  make setup      Create .venv and install dependencies"
	@echo "  make check      Verify Python, MPS, model files, and BF16 headers"
	@echo "  make generate   Generate one image with PROMPT='...'"
	@echo "  make flux       Build and install ~/.local/bin/flux"
	@echo "  make install    Alias for make flux"
	@echo "  make motion-install  Install all Motion Atlas dependencies and model"
	@echo "  make motion-dev      Install and serve Motion Atlas locally"
	@echo "  make motion-prod     Install and serve Motion Atlas on PROD_ADDR (auth required)"
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

go-build:
	go build -o flux ./cmd/flux

flux: go-build
	./flux install

install: flux

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
