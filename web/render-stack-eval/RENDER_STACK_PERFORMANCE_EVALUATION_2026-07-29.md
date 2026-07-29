# Render Stack Performance Evaluation — motion-atlas (Jinja2/FastAPI vs Go vs Rust)

**Date:** 2026-07-29
**Trigger:** Same question as `~/Oscillihue/docs/RENDER_STACK_PERFORMANCE_EVALUATION_2026-07-29.md`, applied to FLUX's `web/motion-atlas` (index/optics/queue/registry/governor/visionary) instead of Oscillihue's Jinja-templated pages.
**Status:** Three full standalone implementations built and load-tested. Directional only — nothing in FLUX's production server (`internal/server/server.go`) was changed.

## TL;DR

- **Static serving:** Go wins decisively — 74,944 req/s vs Rust 48,443 vs Jinja 2,018 (keep-alive, `/motion-atlas/`).
- **SSE, low-to-mid concurrency (1–2,000 connections):** Jinja/FastAPI wins clearly.
- **SSE, top concurrency (5,000 connections):** Go takes the median (p50 26.4ms vs Jinja 37.7ms), but Jinja keeps the better tail (p99 48.3ms vs Go 54.9ms). Rust falls behind at every SSE concurrency level tested.
- **Recommendation:** three-way split, not two — **Go serves all static pages/assets; Jinja/FastAPI serves SSE under normal load; Go takes over SSE only if sustained concurrency is expected to run at ~5,000+ connections.** Rust has no clear role here (unlike Oscillihue, where it won page rendering) because motion-atlas has no templating step for Rust/Askama to accelerate — these pages are plain static HTML/JS/CSS.

## Why this differs from the Oscillihue result

Oscillihue's pages are Jinja2-*rendered* (variable interpolation, escaping, template inheritance) — Rust/Askama's win there is a templating-engine win. motion-atlas's pages have zero server-side variables; serving them is pure file I/O. That erases Rust's advantage entirely and hands it to Go, whose `net/http.FileServer` has a cheaper path per request here (consistent with the same finding in Oscillihue's pass 2, §3.1: "Go's win under keep-alive is likely `net/http.FileServer`'s internal sendfile-style zero-copy path").

## Setup

Real implementations under `~/FLUX/web/render-stack-eval/`, each serving the actual `web/motion-atlas/` directory directly (no copies) under one shared `/motion-atlas/` prefix — matching production's own routing (`internal/server/server.go`'s `/motion-atlas` + `/motion-atlas/` handlers), since pages and their CSS/JS are siblings referencing each other with relative paths.

| Service | Path | Port |
|---|---|---|
| Jinja2/FastAPI | `jinja_fastapi/main.py` | 9201 |
| Go | `go_html_template/main.go` | 9202 |
| Rust (Axum) | `rust_axum/src/main.rs` | 9203 |

`make dev` runs all three in the background; `make stop` kills them. `sse_load_test.py` is the concurrency sweep tool (handles chunked transfer-encoding, which all three use for SSE).

## Results

**Static throughput**, `ab -n 3000 -c 20 -k` against `/motion-atlas/`:

| Stack | req/s |
|---|---|
| Go | 74,944 |
| Rust | 48,443 |
| Jinja | 2,018 |

**SSE `/events` latency by concurrency (ms)** — 0 failed connections at every level, every stack:

| n | Jinja p50/p95/p99 | Go p50/p95/p99 | Rust p50/p95/p99 |
|---|---|---|---|
| 1 | 0.2 / 0.2 / 0.2 | 0.1 / 0.1 / 0.1 | 0.1 / 0.1 / 0.1 |
| 50 | 0.1 / 0.3 / 0.3 | 0.5 / 0.8 / 0.9 | 0.6 / 0.8 / 0.8 |
| 500 | 0.1 / 0.4 / 0.5 | 3.2 / 4.7 / 4.7 | 8.8 / 13.4 / 13.6 |
| 2,000 | 0.1 / 1.7 / 1.9 | 13.2 / 21.7 / 21.9 | 39.8 / 52.0 / 52.8 |
| 5,000 | **37.7** / 47.5 / **48.3** | **26.4** / 53.9 / 54.9 | 97.8 / 129.6 / 135.5 |

Bold = best in that column at that row. Note the crossover only at n=5,000: Go's median overtakes Jinja's, but Jinja keeps the better worst-case (p99), meaning Go is more consistently "pretty fast," Jinja is more consistently "not slow anywhere."

## Recommendation

**Three-way split, load-dependent for SSE:**

1. **Go serves every static page and asset**, always — 1.5–37× faster than the alternatives here with zero downside.
2. **Jinja/FastAPI serves SSE by default** — best latency at every concurrency level actually likely in normal operation (queue/telemetry/asset watchers, typically tens to low-thousands of concurrent viewers, not 5,000).
3. **Go takes over SSE specifically for the top concurrency tier** (~5,000+ sustained connections) where its median latency overtakes Jinja's — e.g. if motion-atlas's `/api/jobs/events`-style fanout is ever expected to serve that many simultaneous dashboards. Below that tier, switching to Go buys nothing and gives up Jinja's better tail latency.
4. **Rust has no role in this split** — it lost both categories against real motion-atlas content, unlike its win on Oscillihue's templated pages.

This mirrors Oscillihue's own conclusion structure (split by what each stack is actually good at) but the specific winners differ because the workload differs: no templating here means no templating win for Rust, and Go's static-file path turns out to double as a viable high-concurrency SSE path once connection counts get large enough to matter.

## Caveats

Same caveats as the Oscillihue evaluation apply: single machine, loopback, no real network latency, no auth/error-path testing, implementations are comparison harnesses only (not wired into FLUX's production server), and a threshold-based Go/Jinja SSE switch would need real routing logic (e.g. connection-count-aware proxy or load balancer) to implement for real — nothing here does that automatically.
