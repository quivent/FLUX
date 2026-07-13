SHELL := /bin/zsh

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

VENV_PY := $(VENV)/bin/python

.PHONY: help setup check generate run flux go-build install studio accel bench warm serve jobs recipes muse history tree colors download clean-output

help:
	@echo "Targets:"
	@echo "  make setup      Create .venv and install dependencies"
	@echo "  make check      Verify Python, MPS, model files, and BF16 headers"
	@echo "  make generate   Generate one image with PROMPT='...'"
	@echo "  make flux       Build the Go CLI at ./flux"
	@echo "  make install    Install flux symlink to ~/.local/bin/flux"
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

setup:
	$(UV) venv $(VENV) --python $(PYTHON)
	$(UV) pip install --python $(VENV_PY) -r requirements.txt

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

flux go-build:
	go build -o flux ./cmd/flux

install: flux
	./flux install

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
