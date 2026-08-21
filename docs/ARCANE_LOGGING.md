# `arcane_log` — one page

The Python side of `internal/ui/ui.go`. Same glyphs, same palette, same column
widths, so the Go CLI and the Python daemons read as one system. **stdlib only.**

```python
from arcane_log import get_logger
log = get_logger("jury")          # that's the whole setup
```

Every call emits **twice**: a human line on stdout, and one JSON object on a
line in `.../logs/arcane-<component>.jsonl`. The JSONL is what the studio web
surfaces tail. Both sinks are safe from multiple threads *and* multiple
processes — five daemons can share one nohup log and one JSONL without tearing
a line.

See the whole thing rendered: `python3 arcane_log.py` (and `| cat` for the
plain-text path).

---

## Structure

```python
log.header("ARCANE · JURY", "moj 3.0.0 · blackwell-96")   # violet title + rule
log.rule()                          # plain rule
log.rule("warm-up")                 # captioned rule
log.kv("profile", "blackwell-96", state="active")
log.kv(tier="full", backend="siglip+dinov2", device="cuda:0")   # one row each
log.table(["tenant", "gib"], rows, title="ROSTER", aligns=["l", "r"])
log.panel("TITLE", "body text", meta="last 60m", color="gold")
log.tree("ARCANE PIPELINE", {"ingest": [("feeder.py", "prompt synthesis")]})
```

`table` right-aligns with `aligns=["l","l","r"]` — stack the decimal points or a
GiB column is noise. It shrinks to the terminal rather than soft-wrapping.

## Events

```python
log.step("resolving continuum profile")      # gold ⟐
log.ok("siglip resident", device="cuda:0", gib=0.81)
log.info("3 verdicts rendered", scored=2)
log.warn("pixtral still down", since="14m")
log.error("r2 sync failed", bucket="aons-beauty")
log.soft("quorum is 2 of 3")                 # dim aside
log.degraded("pixtral-critic", "endpoint :8002 refused after 3 attempts")
log.event("sensory_gate", passed=True, backend="clip")   # JSONL only
```

**Use `degraded()`.** It is not a fancier `warn()`. It is the line that keeps a
half-broken run from being read as a healthy one, it is styled to survive being
skimmed at 3am, and it writes `degraded: true` into the JSONL so the studio can
count degradations without parsing prose. Any keyword argument becomes a
structured field and an inline `key=value`.

## Long-running work

```python
with log.timer("warm sensory gates"):
    warm()

for i, cell in enumerate(cells):
    render(cell)
    log.progress(i + 1, len(cells), label="atlas 0041",
                 detail="tile 12/16", cache_hit=0.93)
log.progress_done("65,536 cells · 3d 04h · 4.21s/cell")
```

Call `progress()` as often as you like — it throttles itself. On a TTY it
repaints one line at 20fps. Redirected to a file it emits a **whole line** at
every crossed decile or every `ARCANE_PROGRESS_INTERVAL` seconds (default 30),
so a 65,536-cell job produces ~12 lines, not 65,536. `rate` overrides the
measured items/second; `cache_hit` takes 0..1 or 0..100.

## Domain renderers

```python
log.verdict(receipt)        # full jury scorecard, moj_evaluator receipt shape
log.gates(gate_result)      # sensory_gates.gate_scores() dict, verbatim
log.fortiche(conformance)   # arcane_aesthetic.conformance() dict, verbatim
log.vram(budget)            # VRAM budget + capacity bar
log.roster(tenants)         # model roster table
```

Pass the dict you already have. Every field is read with `.get()`, so a
half-populated dict renders what it has instead of raising.

**`verdict(receipt)`** — crown for `masterpiece`, sparkle for `spectacle`, a
per-judge score bar with the model name and its critique, the composite, and a
percentile position rail. A receipt with `tier: "unscored"` (or
`raw_composite: None`) renders on a completely different path: dimmed gutter,
no composite, no tier badge, no colour on any bar, `✕ NOT SCORED` and the
`unscored_reason` printed in full. **Do not work around this.** If a receipt
renders as unscored and you wanted a number, the bug is upstream.

**`gates(result)`** — pass the whole `gate_scores()` dict. It knows the
contract: `passed == (not failures)`, so the standing `"DEGRADED: DINOv2 is
mandatory…"` line in `reasons` is rendered as an amber `▲` tier banner, never as
a rose `↳` rejection. `tier` gets its own chip (`[full]` mint, `[degraded]`
amber, `[EMERGENCY]` red with an INCIDENT line). A metric whose
`measured[key] == "unavailable"` renders as `────  —`, never as a zero bar.
`calibration` is surfaced and turns amber on `provisional`/`heuristic`.

**`vram(budget)`** — keys: `profile`, `gpu`, `total_gib`, `reserve_gib`,
`usable_gib`, `allocated_gib`, `free_gib`, `headroom_gib`, `fits`, `tenants: [{name,
model, precision, vram_gib, note}]`, and optional `remedies: [str]`. Anything
derivable is derived. Headroom under 2 GiB turns amber; `fits: false` gets a red
overflow bar, an over-by figure and a `✕` rule.

**`fortiche(conf)`** — `impasto`, `planarity`, `chiaroscuro`, `palette_zaun`,
`palette_piltover`, `anti_cgi`, `fortiche_score`, `verdict`, `realm`,
`threshold`. Accepts 0..1 or 0..100 and normalises on the max it sees. The two
palette bars paint in Zaun `#00ff88` and Piltover `#00d2ff` (truecolor, falling
back to xterm-256, then to no colour).

---

## The JSONL schema

One object per line. `v` is the schema version — additive changes only.

```json
{"v":1,"ts":"2026-08-20T06:08:22.855Z","epoch":1787206102.855522,
 "run_id":"arc-20260820-020822-b313","component":"demo","level":"ok",
 "kind":"verdict","message":"job 9f2c1e04 masterpiece",
 "fields":{"job_id":"9f2c1e04","tier":"masterpiece","curved_score":96.4,"...":"..."},
 "pid":45843,"thread":"MainThread"}
```

`level` ∈ `debug|soft|info|ok|warn|error`. `kind` is the renderer that produced
it (`header`, `kv`, `table`, `panel`, `tree`, `step`, `ok`, `info`, `warn`,
`error`, `soft`, `degraded`, `progress`, `progress_done`, `timer`, `verdict`,
`gates`, `fortiche`, `vram`, `roster`) or whatever string you pass to
`log.event(kind, …)`.

**Sink location**, first that works: the `jsonl=` argument → `$ARCANE_LOG_DIR`
→ `pipeline_paths.OUT_DIR/logs` → `$FLUX_HOME/outputs/logs` →
`flux_paths.default_out_dir()/logs` → the temp dir. Candidates only create
`logs/`, never a missing parent, so a dev laptop lands in temp instead of
conjuring `~/Models/flux-output/`.

## Environment

| variable | effect |
| --- | --- |
| `NO_COLOR`, `FLUX_NO_COLOR` | force plain text |
| `FORCE_COLOR`, `FLUX_FORCE_COLOR`, `CLICOLOR_FORCE` | force colour when piped |
| `COLORTERM=truecolor` | 24-bit realm palettes instead of xterm-256 |
| `ARCANE_LOG_ASCII=1` | ASCII glyphs — output is byte-clean 7-bit |
| `ARCANE_LOG_WIDTH=100` | pin the width (default: terminal, clamped 72–120) |
| `ARCANE_LOG_STAMP=1` | prefix every line with `HH:MM:SS component` |
| `ARCANE_LOG_DIR` | JSONL directory |
| `ARCANE_RUN_ID` | share one run id across every daemon in a launch |
| `ARCANE_PROGRESS_INTERVAL` | seconds between redirected progress lines (30) |

Not a TTY ⇒ no escape codes, no carriage returns, no cursor tricks. That is the
path `run_pipeline_daemons.sh` actually takes, and it is verified: the demo reel
piped through `cat` contains zero `\x1b` and zero `\r`.

For the daemons, put this in the supervisor so one launch stitches together in
the studio and the log files carry timestamps:

```bash
export ARCANE_RUN_ID="arc-$(date +%Y%m%d-%H%M%S)"
export ARCANE_LOG_STAMP=1
```

## Adopting it without a hard dependency

```python
try:
    from arcane_log import get_logger
    log = get_logger("feeder")
except Exception:            # module not there yet, or a broken venv
    log = _my_plain_fallback()
```

`get_logger` is cached per `(component, jsonl, level)` — importing it from four
modules in one daemon gets you one logger, one descriptor and one lock.
