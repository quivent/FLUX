# ═══════════════════════════════════════════════════════════════════════════════
#  Koyomi Atelier — bring the platform up, steer it, take it down
#
#  Order matters: fluxd → gallery → triaged → control → worker.
#  Quick start: make setup && make up
#
#  POSIX sh only. Recipes avoid bashisms so /bin/sh is enough.
# ═══════════════════════════════════════════════════════════════════════════════

SHELL := /bin/sh
.DEFAULT_GOAL := help

# ─── where the platform lives ─────────────────────────────────────────────────
# Not a preference. Every module hardcodes this path (49 occurrences across 15
# files) and imports its siblings by bare name. See README.md, "Why the layout
# is flat". Changing ROOT without porting the code will not work.
ROOT      ?= /home/dev
SRC       := $(CURDIR)
PY        := $(ROOT)/venv/bin/python
PIP       := $(ROOT)/venv/bin/pip
NODE      ?= flux-prod

# ─── ports ────────────────────────────────────────────────────────────────────
FLUXD_PORT   := 8080
GALLERY_PORT := 8090
CONTROL_PORT := 8091
TRIAGE_PORT  := 8092

# ─── files ────────────────────────────────────────────────────────────────────
STOP      := $(ROOT)/STOP
WORKERLOG := $(ROOT)/perpetual.log

# ─── worker arguments (override on the command line) ──────────────────────────
INTERVAL      ?= 10
STEPS         ?= 32
WIDTH         ?= 448
HEIGHT        ?= 672
SEED          ?= 8801
MAX_CYCLES    ?= 100000
CONSULT_EVERY ?= 3

# ─── environment the GPU services need ────────────────────────────────────────
# PYTHONPATH is what makes the bare-name imports resolve. HF_HUB_OFFLINE stops
# diffusers reaching for the Hub; see README.md, "The HF cache gotcha".
GPU_ENV := HF_HOME=$(ROOT)/hf-cache HF_HUB_OFFLINE=1 PYTHONPATH=$(ROOT)
APP_ENV := PYTHONPATH=$(ROOT)

# ─── directories the platform writes into ─────────────────────────────────────
STATE_DIRS := runs out art cards thumbs taste governor concepts studio

# ─── colour (NO_COLOR=1 disables) ─────────────────────────────────────────────
ifndef NO_COLOR
  ESC    := \033
  RESET  := $(ESC)[0m
  BOLD   := $(ESC)[1m
  DIM    := $(ESC)[2m
  RED    := $(ESC)[31m
  GREEN  := $(ESC)[32m
  YELLOW := $(ESC)[33m
  BLUE   := $(ESC)[34m
  CYAN   := $(ESC)[36m
else
  RESET  :=
  BOLD   :=
  DIM    :=
  RED    :=
  GREEN  :=
  YELLOW :=
  BLUE   :=
  CYAN   :=
endif

define _hr
	printf '  $(DIM)────────────────────────────────────────────────────────────$(RESET)\n'
endef

define say
	printf '  $(CYAN)→$(RESET) %b\n' "$(1)"
endef

define ok
	printf '  $(GREEN)✓$(RESET) %b\n' "$(1)"
endef

define warn
	printf '  $(YELLOW)!$(RESET) %b\n' "$(1)"
endef

define err
	printf '  $(RED)✗$(RESET) %b\n' "$(1)"
endef

define banner
	printf '\n  $(BOLD)$(CYAN)%s$(RESET)  $(DIM)%s$(RESET)\n' '$(1)' '$(2)'; \
	$(call _hr)
endef

# start a daemon: $(1) script, $(2) env, $(3) logfile
define spawn
	if pgrep -f '^$(PY) $(ROOT)/$(1)$$' >/dev/null 2>&1; then \
		$(call warn,$(1) already running — leaving it alone); \
	else \
		$(call say,starting $(1)); \
		cd $(ROOT) && $(2) setsid nohup $(PY) $(ROOT)/$(1) >> $(3) 2>&1 & \
		sleep 1; \
	fi
endef

# wait for a health endpoint: $(1) port, $(2) label, $(3) tries
define waitfor
	i=0; \
	while [ $$i -lt $(3) ]; do \
		if curl -sf --max-time 3 http://127.0.0.1:$(1)/health >/dev/null 2>&1; then \
			$(call ok,$(2) ready on :$(1)); \
			break; \
		fi; \
		i=$$((i + 1)); \
		sleep 2; \
	done; \
	if [ $$i -ge $(3) ]; then \
		$(call err,$(2) did not come up on :$(1) after $$(($(3) * 2))s); \
		exit 1; \
	fi
endef

# report one service: $(1) script, $(2) port, $(3) label
define probe
	if pgrep -f '^$(PY) $(ROOT)/$(1)$$' >/dev/null 2>&1; then \
		pid=$$(pgrep -f '^$(PY) $(ROOT)/$(1)$$' | tr '\n' ' '); \
		if [ -n "$(2)" ] && curl -sf --max-time 3 http://127.0.0.1:$(2)/health >/dev/null 2>&1; then \
			printf '  $(GREEN)●$(RESET) %-12s $(DIM)pid %s$(RESET) $(GREEN)healthy$(RESET) $(DIM):%s$(RESET)\n' '$(3)' "$$pid" '$(2)'; \
		elif [ -n "$(2)" ]; then \
			printf '  $(YELLOW)●$(RESET) %-12s $(DIM)pid %s$(RESET) $(YELLOW)up, /health silent$(RESET) $(DIM):%s$(RESET)\n' '$(3)' "$$pid" '$(2)'; \
		else \
			printf '  $(GREEN)●$(RESET) %-12s $(DIM)pid %s$(RESET) $(GREEN)running$(RESET)\n' '$(3)' "$$pid"; \
		fi; \
	else \
		printf '  $(RED)○$(RESET) %-12s $(DIM)not running$(RESET)\n' '$(3)'; \
	fi
endef

# stop a daemon: $(1) script, $(2) label
define reap
	if pgrep -f '^$(PY) $(ROOT)/$(1)$$' >/dev/null 2>&1; then \
		$(call say,stopping $(2)); \
		pkill -f '^$(PY) $(ROOT)/$(1)$$' || true; \
	else \
		$(call warn,$(2) was not running); \
	fi
endef

.PHONY: help setup install dirs venv deps check \
        up down restart status logs logs-worker \
        fluxd gallery triage control worker governord \
        expose bench compile

# ═══════════════════════════════════════════════════════════════════════════════
#  Help
# ═══════════════════════════════════════════════════════════════════════════════

## help: list targets (default)
help:
	@printf '\n'
	@printf '  $(BOLD)$(CYAN)Koyomi Atelier$(RESET)  $(DIM)— make <target>$(RESET)\n'
	@printf '  $(DIM)Root:$(RESET) $(ROOT)   $(DIM)Node:$(RESET) $(NODE)\n\n'
	@awk ' \
	  /^## @ / { printf "  $(BOLD)$(BLUE)── %s ──$(RESET)\n", substr($$0, 6); next } \
	  /^## [a-z]/ { \
	    i = index($$0, ": "); \
	    if (i == 0) next; \
	    t = substr($$0, 4, i - 4); \
	    if (t == "help") next; \
	    printf "    $(GREEN)%-16s$(RESET) %s\n", t, substr($$0, i + 2); \
	  } \
	' $(MAKEFILE_LIST)
	@printf '\n'
	@printf '  $(DIM)Quick start:$(RESET)  make setup  →  make up  →  make expose\n'
	@printf '  $(DIM)Order:$(RESET)        fluxd → gallery → triage → control → worker\n'
	@printf '  $(DIM)Tip:$(RESET)          run $(GREEN)make <target> -n$(RESET) to inspect a recipe without running it\n\n'

# ═══════════════════════════════════════════════════════════════════════════════
## @ Setup
# ═══════════════════════════════════════════════════════════════════════════════

## setup: venv, dependencies, and the directories the platform writes into
setup: check dirs venv deps
	@$(call banner,setup complete,make up brings the stack online)
	@printf '  $(CYAN)→$(RESET) next: $(BOLD)make up$(RESET)\n\n'

## check: verify the deploy root, python, GPU and model cache
check:
	@$(call banner,preflight,$(ROOT))
	@if [ ! -d "$(ROOT)" ]; then $(call err,$(ROOT) does not exist); exit 1; fi
	@if [ "$(SRC)" != "$(ROOT)" ]; then \
		$(call warn,running from $(SRC) — the deploy root is $(ROOT)); \
		printf '    $(DIM)the code hardcodes $(ROOT); run $(BOLD)make install$(RESET)$(DIM) first$(RESET)\n'; \
	else \
		$(call ok,source tree is the deploy root); \
	fi
	@if command -v nvidia-smi >/dev/null 2>&1; then \
		nvidia-smi --query-gpu=name,memory.total,memory.used --format=csv,noheader \
		  | while read -r line; do $(call ok,GPU $$line); done; \
	else \
		$(call warn,nvidia-smi not found — the panel samples it for GPU state); \
	fi
	@if [ -d "$(ROOT)/hf-cache" ]; then \
		$(call ok,model cache $$(du -sh $(ROOT)/hf-cache 2>/dev/null | cut -f1) at $(ROOT)/hf-cache); \
	else \
		$(call warn,no $(ROOT)/hf-cache — FLUX.1-dev and FLUX.1-Kontext-dev must be cached); \
	fi
	@printf '\n'

## install: copy this source tree to the deploy root (the code hardcodes it)
install:
	@$(call banner,install,$(SRC) → $(ROOT))
	@if [ "$(SRC)" = "$(ROOT)" ]; then $(call ok,already in place); exit 0; fi
	@mkdir -p $(ROOT)/fonts
	@cp $(SRC)/*.py $(ROOT)/
	@cp $(SRC)/control.html $(ROOT)/
	@cp $(SRC)/fonts/*.ttf $(ROOT)/fonts/
	@$(call ok,15 modules + control.html + 2 fonts installed)
	@printf '\n'

## dirs: create the runtime directories (runs, out, art, taste, governor, …)
dirs:
	@$(call banner,directories,$(ROOT))
	@for d in $(STATE_DIRS); do \
		mkdir -p $(ROOT)/$$d; \
		printf '  $(DIM)·$(RESET) %s\n' "$(ROOT)/$$d"; \
	done
	@printf '\n'

## venv: create the virtualenv under the deploy root if it is missing
venv:
	@if [ -x "$(PY)" ]; then \
		$(call ok,venv present — $$($(PY) -V 2>&1)); \
	else \
		$(call say,creating venv at $(ROOT)/venv); \
		python3 -m venv $(ROOT)/venv; \
		$(PIP) install --upgrade pip; \
	fi

## deps: install requirements.txt into the venv
deps:
	@$(call banner,dependencies,requirements.txt)
	@$(PIP) install -r $(SRC)/requirements.txt
	@$(call ok,dependencies installed)

# ═══════════════════════════════════════════════════════════════════════════════
## @ Lifecycle
# ═══════════════════════════════════════════════════════════════════════════════

## up: bring the whole stack online, in order, with health waits
up:
	@$(call banner,koyomi atelier,fluxd → gallery → triage → control → worker)
	@$(MAKE) --no-print-directory fluxd
	@$(MAKE) --no-print-directory gallery
	@$(MAKE) --no-print-directory triage
	@$(MAKE) --no-print-directory control
	@$(MAKE) --no-print-directory worker
	@printf '\n'
	@$(call ok,stack up — $(BOLD)make status$(RESET) to confirm / $(BOLD)make expose$(RESET) to publish)
	@printf '\n'

## down: STOP the worker after its current cycle, then stop the daemons
down:
	@$(call banner,shutting down,the worker finishes its current cycle first)
	@if pgrep -f '^$(PY) $(ROOT)/perpetual.py' >/dev/null 2>&1; then \
		$(call say,writing $(STOP)); \
		touch $(STOP); \
		i=0; \
		while [ $$i -lt 60 ] && pgrep -f '^$(PY) $(ROOT)/perpetual.py' >/dev/null 2>&1; do \
			i=$$((i + 1)); \
			sleep 2; \
		done; \
		if pgrep -f '^$(PY) $(ROOT)/perpetual.py' >/dev/null 2>&1; then \
			$(call warn,worker still rendering after 120s — leaving it; re-run make down); \
		else \
			$(call ok,worker stopped cleanly); \
		fi; \
	else \
		$(call warn,worker was not running); \
	fi
	@$(call reap,governord.py,governord)
	@$(call reap,control.py,control)
	@$(call reap,triaged.py,triage)
	@$(call reap,gallery.py,gallery)
	@$(call reap,fluxd.py,fluxd)
	@printf '\n'
	@$(call ok,stack down — $(ROOT) untouched / files survive a stop)
	@printf '\n'

## restart: down, then up
restart:
	@$(MAKE) --no-print-directory down
	@$(MAKE) --no-print-directory up

## status: services, ports, worker, GPU, and the desired/applied revisions
status:
	@$(call banner,status,$(ROOT))
	@$(call probe,fluxd.py,$(FLUXD_PORT),fluxd)
	@$(call probe,gallery.py,$(GALLERY_PORT),gallery)
	@$(call probe,triaged.py,$(TRIAGE_PORT),triage)
	@$(call probe,control.py,$(CONTROL_PORT),control)
	@$(call probe,perpetual.py,,worker)
	@$(call probe,governord.py,,governord)
	@printf '\n'
	@if [ -f "$(STOP)" ]; then $(call warn,$(STOP) is present — the worker will halt); fi
	@if command -v nvidia-smi >/dev/null 2>&1; then \
		nvidia-smi --query-gpu=memory.used,memory.total,utilization.gpu \
		  --format=csv,noheader,nounits \
		  | while IFS=, read -r u t g; do \
		      printf '  $(DIM)GPU$(RESET)          %s / %s MiB   $(DIM)util$(RESET) %s%%\n' \
		        "$$(echo $$u | tr -d ' ')" "$$(echo $$t | tr -d ' ')" "$$(echo $$g | tr -d ' ')"; \
		    done; \
	fi
	@if curl -sf --max-time 4 http://127.0.0.1:$(CONTROL_PORT)/api/state >/dev/null 2>&1; then \
		curl -sf --max-time 4 http://127.0.0.1:$(CONTROL_PORT)/api/state \
		  | $(PY) -c 'import json,sys; s=json.load(sys.stdin); t=s["tunables"]; d=s["direction"]; \
print("  cycle        %s   champion %s" % (s.get("cycle"), (s.get("champion") or {}).get("key"))); \
print("  tunables     desired r%s / applied r%s   %s" % (t["desired_rev"], t["applied_rev"], "SYNCED" if t["synced"] else "PENDING")); \
print("  direction    desired r%s / applied r%s   %s" % (d["desired_rev"], d["applied_rev"], "SYNCED" if d["synced"] else "PENDING")); \
print("  taste        %s anchor(s), %s real, %s repulsor(s)" % (s["taste"]["anchors"], s["taste"]["real"], s["taste"]["repulsors"]))' \
		  2>/dev/null || $(call warn,could not parse /api/state); \
	else \
		$(call warn,control surface not answering on :$(CONTROL_PORT) — no revision state); \
	fi
	@printf '\n'

## logs: tail the worker log (N=200 to change the line count)
logs: logs-worker

logs-worker:
	@$(call banner,worker log,$(WORKERLOG))
	@if [ -f "$(WORKERLOG)" ]; then \
		tail -n $${N:-200} -f $(WORKERLOG); \
	else \
		$(call warn,no $(WORKERLOG) yet — the worker has not been started from here); \
	fi

# ═══════════════════════════════════════════════════════════════════════════════
## @ Services
# ═══════════════════════════════════════════════════════════════════════════════

## fluxd: 1. the inference daemon — FLUX + Kontext resident on the H100 (:8080)
fluxd:
	@$(call spawn,fluxd.py,$(GPU_ENV),$(ROOT)/fluxd.out)
	@$(call say,waiting for the pipeline to load — ~8s FLUX / ~11s with Kontext)
	@$(call waitfor,$(FLUXD_PORT),fluxd,60)

## gallery: 2. the public feed — generations newest-first, SSE, thumbs (:8090)
gallery:
	@$(call spawn,gallery.py,$(APP_ENV),$(ROOT)/gallery.out)
	@i=0; \
	while [ $$i -lt 20 ]; do \
		if curl -sf --max-time 3 http://127.0.0.1:$(GALLERY_PORT)/api/cards?limit=1 >/dev/null 2>&1; then \
			$(call ok,gallery ready on :$(GALLERY_PORT)); \
			break; \
		fi; \
		i=$$((i + 1)); \
		sleep 2; \
	done; \
	if [ $$i -ge 20 ]; then $(call err,gallery did not come up on :$(GALLERY_PORT)); exit 1; fi

## triage: 3. defect triage — structural + CLIP zero-shot (:8092)
triage:
	@$(call spawn,triaged.py,$(GPU_ENV),$(ROOT)/triaged.out)
	@$(call say,loading CLIP — fp16 / ~0.32 GiB beside FLUX)
	@$(call waitfor,$(TRIAGE_PORT),triage,45)

## control: 4. the control surface — UI, /ws, /events, all control endpoints (:8091)
control:
	@$(call spawn,control.py,$(APP_ENV),$(ROOT)/control.out)
	@i=0; \
	while [ $$i -lt 20 ]; do \
		if curl -sf --max-time 3 http://127.0.0.1:$(CONTROL_PORT)/api/state >/dev/null 2>&1; then \
			$(call ok,control ready on :$(CONTROL_PORT)); \
			break; \
		fi; \
		i=$$((i + 1)); \
		sleep 2; \
	done; \
	if [ $$i -ge 20 ]; then $(call err,control did not come up on :$(CONTROL_PORT)); exit 1; fi

## worker: 5. the perpetual loop — start it LAST, it calls everything above
worker:
	@if pgrep -f '^$(PY) $(ROOT)/perpetual.py' >/dev/null 2>&1; then \
		$(call warn,worker already running — leaving it alone); \
	else \
		if ! curl -sf --max-time 3 http://127.0.0.1:$(TRIAGE_PORT)/health >/dev/null 2>&1; then \
			$(call err,triage is not up on :$(TRIAGE_PORT)); \
			printf '    $(DIM)a worker started without it passes every render through unfiltered$(RESET)\n'; \
			exit 1; \
		fi; \
		rm -f $(STOP); \
		$(call say,starting worker — interval $(INTERVAL)s / $(STEPS) steps / $(WIDTH)x$(HEIGHT)); \
		printf '\n=== make worker: %s ===\n' "$$(date '+%Y-%m-%d %H:%M:%S')" >> $(WORKERLOG); \
		cd $(ROOT) && $(GPU_ENV) setsid nohup $(PY) $(ROOT)/perpetual.py \
			--interval $(INTERVAL) --steps $(STEPS) \
			--width $(WIDTH) --height $(HEIGHT) --seed $(SEED) \
			--max-cycles $(MAX_CYCLES) --consult-every $(CONSULT_EVERY) \
			>> $(WORKERLOG) 2>&1 & \
		sleep 2; \
		$(call ok,worker started — $(BOLD)make logs$(RESET) to watch it); \
	fi

## governord: optional — the governor's own clock, consults him on an interval
governord:
	@$(call spawn,governord.py,$(APP_ENV),$(ROOT)/governord.out)
	@$(call ok,governord started — expect 524s and 120s replies; see README)

# ═══════════════════════════════════════════════════════════════════════════════
## @ Operations
# ═══════════════════════════════════════════════════════════════════════════════

## expose: publish the control surface and the feed through the control plane
expose:
	@$(call banner,expose,endpoints are minted off-node and die with it)
	@if command -v gman >/dev/null 2>&1; then \
		$(call say,gman found — exposing :$(CONTROL_PORT) and :$(GALLERY_PORT)); \
		gman expose-port --node $(NODE) --port $(CONTROL_PORT); \
		gman expose-port --node $(NODE) --port $(GALLERY_PORT); \
	else \
		$(call warn,no gman CLI here — exposure runs through the MCP control plane); \
		printf '\n'; \
		printf '    $(BOLD)expose_port(node="$(NODE)", port=$(CONTROL_PORT))$(RESET)   $(DIM)the control surface$(RESET)\n'; \
		printf '    $(BOLD)expose_port(node="$(NODE)", port=$(GALLERY_PORT))$(RESET)   $(DIM)the public feed$(RESET)\n'; \
		printf '\n'; \
	fi
	@$(call warn,do NOT expose :$(FLUXD_PORT) or :$(TRIAGE_PORT) — loopback services with no auth)
	@$(call warn,an endpoint dies when the node stops and a wake never resurrects it)
	@printf '    $(DIM)re-mint after every stop; never hardcode an expose_port URL$(RESET)\n\n'

## bench: batch-size benchmark (stop the worker first — it contends for the GPU)
bench:
	@$(call banner,bench,448x672 @ 32 steps — batch 1 / 2 / 4)
	@if pgrep -f '^$(PY) $(ROOT)/perpetual.py' >/dev/null 2>&1; then \
		$(call err,the worker is running — run make down first or the numbers are noise); \
		exit 1; \
	fi
	@cd $(ROOT) && $(GPU_ENV) $(PY) $(ROOT)/bench_batch.py

## compile: byte-compile every module — the cheapest possible smoke test
compile:
	@$(call banner,compile,py_compile over the tree)
	@cd $(SRC) && python3 -m py_compile *.py
	@$(call ok,all modules compile)
	@printf '\n'
