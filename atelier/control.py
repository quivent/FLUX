#!/home/dev/venv/bin/python
"""Control surface for the Perpetual Sieve.

Steers the real machinery through the seams that already exist -- it does not
keep a second copy of the loop's state:

    STOP file            stop after the current cycle   (perpetual.py:284)
    perpetual.py argv    interval / steps / size / seed / caps
    directive.json       the standing directive         (estate.poll_directive)
    runs/<gen>/verdicts.json   a human Keep or Retire   (taste.human_verdicts)
    taste.harvest()      folds those verdicts into real anchors

The eye-gate here is the only thing on this node that can produce a NON-provisional
anchor. Until one exists, taste.promote_real() never fires and fitness is measured
against two images the machine seeded for itself.
"""
import asyncio
import json
import os
import pathlib
import re
import subprocess
import time

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    StreamingResponse,
)
from pydantic import BaseModel

import direction as DIR
import duel as DU
import evolve_queue as EQ
import tunables as TU

HOME = pathlib.Path("/home/dev")
RUNS = HOME / "runs"
STOP = HOME / "STOP"
STATE = HOME / "perpetual_state.json"
ANCHORS = HOME / "taste" / "anchors.json"
GOV = HOME / "governor"                 # estate.py:30
DIRECTIVE = GOV / "directive.json"      # estate.py:31 — he writes, loop clears
CONTROL = GOV / "control.json"          # estate.py:32 — tempo / stalled posture
LOG = HOME / "perpetual.log"
PORT = 8091

# Mirrors perpetual.MUTATIONS so the panel can show every vector, including the
# ones that have never been tried.
def _vectors():
    """The real mutation vectors, read from perpetual.py so the two never drift."""
    try:
        blk = (HOME / "perpetual.py").read_text().split("MUTATIONS = [")[1]
        return re.findall(r'\("([a-z]+)"', blk.split("\n]")[0])
    except Exception:
        return []


VECTORS = _vectors()

# perpetual.py ships these hardcoded. They are expose_port URLs from an earlier
# session and die with the container that minted them; the panel probes them so
# a dead triage officer is visible instead of silent.
# Loopback, not a fresh expose_port URL: an endpoint dies with the node, so a
# public URL here would rot again at the next stop. Kept in step with
# perpetual.py's constants.
OFFICER = "http://127.0.0.1:8092"
SELF_FEED = "http://127.0.0.1:8090"

app = FastAPI(title="Perpetual Sieve — control")


def _read(p, default):
    try:
        return json.loads(pathlib.Path(p).read_text())
    except Exception:
        return default


def loop_running():
    try:
        out = subprocess.run(["pgrep", "-af", "perpetual.py"],
                             capture_output=True, text=True, timeout=5).stdout
        pids = []
        for ln in out.splitlines():
            parts = ln.split(None, 1)
            if len(parts) != 2:
                continue
            cmd = parts[1]
            # the worker itself, not a shell that merely mentions the script
            if cmd.startswith("/home/dev/venv/bin/python") and cmd.endswith("perpetual.py") \
               or cmd.startswith("/home/dev/venv/bin/python /home/dev/perpetual.py"):
                pids.append(parts[0])
        return bool(pids), pids
    except Exception:
        return False, []


def proc_alive(pattern):
    try:
        out = subprocess.run(["pgrep", "-f", pattern],
                             capture_output=True, text=True, timeout=5).stdout
        return bool(out.strip())
    except Exception:
        return False


def gpu():
    try:
        out = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=memory.used,memory.total,utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=8).stdout.strip().splitlines()[0]
        used, total, util = [int(x.strip()) for x in out.split(",")]
        return {"used_mib": used, "total_mib": total, "util_pct": util}
    except Exception:
        return None


def taste_summary():
    d = _read(ANCHORS, {"anchors": [], "repulsors": []})
    A = d.get("anchors", []) or []
    R = d.get("repulsors", []) or []
    strip = lambda x: {k: v for k, v in x.items() if k != "vec"}
    return {
        "anchors": len(A),
        "real": sum(1 for a in A if not a.get("provisional")),
        "provisional": sum(1 for a in A if a.get("provisional")),
        "repulsors": len(R),
        "anchor_items": [strip(a) for a in A],
        "repulsor_items": [strip(r) for r in R],
    }


def _gen_scores(d):
    """key -> fitness for one generation, wherever the loop parked them."""
    blob = _read(d / "run.json", None)
    if not isinstance(blob, dict):
        return {}
    scores = (blob.get("perpetual") or {}).get("scores") or blob.get("scores")
    if not scores:
        return {}
    return {s.get("key"): s.get("fitness") for s in scores if s.get("key")}


def pending_verdicts():
    """Human verdicts written but not yet folded into the landscape."""
    store = _read(ANCHORS, {"anchors": [], "repulsors": []})
    known = {(a.get("gen"), a.get("key")) for a in store.get("anchors", []) or []}
    known |= {(r.get("gen"), r.get("key")) for r in store.get("repulsors", []) or []}
    n = 0
    for d in RUNS.glob("gen-*"):
        v = _read(d / "verdicts.json", {}) or {}
        for key, rec in v.items():
            if rec.get("critic") in ("qwen2.5-vl triage", "smoke-test"):
                continue
            if (d.name, key) in known:
                continue
            n += 1
    return n


def recent_cards(limit=32):
    gens = sorted([d for d in RUNS.glob("gen-*") if d.is_dir()],
                  key=lambda p: p.name, reverse=True)
    out = []
    for d in gens:
        run = _read(d / "run.json", {}) or {}
        vd = _read(d / "verdicts.json", {}) or {}
        scores = _gen_scores(d)
        for c in run.get("cards", []) or []:
            k = c.get("key")
            if not k:
                continue
            rec = vd.get(k) or {}
            out.append({
                "gen": d.name,
                "key": k,
                "label": run.get("label", ""),
                "product": c.get("product"),
                "fitness": scores.get(k),
                "verdict": rec.get("verdict"),
                "critic": rec.get("critic"),
                "human": rec.get("critic") not in
                         (None, "qwen2.5-vl triage", "smoke-test"),
            })
            if len(out) >= limit:
                return out
    return out


# Files the worker writes. Watched for push, and reported for freshness so the
# panel can distinguish "live" from "correct but last written N seconds ago".
WATCH = {
    "state": STATE,
    "ack": pathlib.Path("/home/dev/tunables_ack.json"),
    "desired": pathlib.Path("/home/dev/tunables.json"),
    "anchors": ANCHORS,
    "estate": pathlib.Path("/home/dev/governor/control.json"),
    "stop": STOP,
}


def _mtimes():
    out = {}
    for k, f in WATCH.items():
        try:
            out[k] = f.stat().st_mtime
        except OSError:
            out[k] = 0.0
    return out


def _freshness():
    """Per-source age. cycle/champion/history/vectors are only as fresh as the
    last COMPLETED cycle -- the worker rewrites perpetual_state.json at save_state.
    """
    now = time.time()
    m = _mtimes()
    age = lambda k: round(now - m[k], 1) if m[k] else None
    return {
        "sampled_at": now,
        "panel_pid": os.getpid(),
        "ages_s": {k: age(k) for k in WATCH},
        "sources": {
            "cycle/champion/history/vectors": "perpetual_state.json (worker, on cycle completion)",
            "taste": "taste/anchors.json (live read)",
            "tunables.applied": "tunables_ack.json (worker, on adoption)",
            "running/pids": "pgrep (live)",
            "gpu": "nvidia-smi point sample at request time",
        },
        "gpu_util_is_point_sample": True,
    }


# An ack file outlives the process that wrote it, so `applied_pid` alone cannot
# tell "the worker adopted this" from "a worker that died hours ago once did".
# Today the panel showed pid 103966's acknowledgement long after it was killed.
# A pid is only evidence if it is still alive AND still a worker: pids are
# recycled, so /proc/<pid> existing is necessary but not sufficient.
WORKER_CMD_HINTS = ("perpetual.py", "blast2.py", "loopd.py", "governord.py")


def _worker_pid_alive(pid):
    """True only if <pid> is a live process whose cmdline is a real worker."""
    try:
        n = int(pid)
    except (TypeError, ValueError):
        return False
    if n <= 0:
        return False
    try:
        cmd = pathlib.Path(f"/proc/{n}/cmdline").read_bytes()
    except OSError:
        return False
    cmd = cmd.replace(b"\0", b" ").decode("utf-8", "replace")
    return any(h in cmd for h in WORKER_CMD_HINTS)


def _ack_liveness(ack):
    """applied_pid_alive / ack_stale for an ack blob. Stale = there IS an
    acknowledgement on file but nothing alive stands behind it."""
    pid = ack.get("pid")
    alive = _worker_pid_alive(pid)
    has_ack = bool(ack.get("applied_at") or int(ack.get("rev", 0) or 0))
    return {"applied_pid_alive": alive, "ack_stale": bool(has_ack) and not alive}


def _direction_block():
    d = DIR.desired()
    a = DIR.ack()
    arev = int(a.get("rev", 0) or 0)
    return {
        "desired_rev": d["rev"], "applied_rev": arev,
        "values": d["values"] or DIR.defaults(),
        "in_force": a.get("in_force", {}),
        "changed": a.get("changed", {}),
        "applied_pid": a.get("pid"), "applied_at": a.get("applied_at"),
        "latency_s": a.get("latency_s"),
        "synced": bool(d["rev"]) and d["rev"] == arev,
        "pending": max(0, d["rev"] - arev),
        **_ack_liveness(a),
    }


def _tunables_block():
    """Desired vs applied. `synced` is the worker's word, never the panel's."""
    d = TU.desired()
    a = TU.ack()
    arev = int(a.get("rev", 0) or 0)
    return {
        "desired_rev": d["rev"],
        "desired": d["values"],
        "applied_rev": arev,
        "applied_at": a.get("applied_at"),
        "applied_cycle": a.get("cycle"),
        "applied_pid": a.get("pid"),
        "changed": a.get("changed", {}),
        "live": a.get("live", {}),
        "latency_s": a.get("latency_s"),
        "age_s": round(time.time() - a["applied_at"], 1) if a.get("applied_at") else None,
        "synced": bool(d["rev"]) and d["rev"] == arev,
        "pending": max(0, d["rev"] - arev),
        **_ack_liveness(a),
        "fields": {k: {"lo": v[1], "hi": v[2],
                       "kind": "float" if v[0] is float else "int"}
                   for k, v in TU.FIELDS.items()},
    }


def snapshot():
    s = _read(STATE, {}) or {}
    running, pids = loop_running()
    hist = s.get("history", []) or []
    champ = s.get("champion") or {}
    stats = s.get("mutation_stats", {}) or {}
    vectors = []
    for name in VECTORS:
        st = stats.get(name) or {}
        n = st.get("n") or 0
        vectors.append({
            "name": name,
            "n": n,
            "gain": round(st.get("gain", 0.0), 4) if n else None,
            "avg": round(st["gain"] / n, 5) if n else None,
        })
    return {
        "t": time.time(),
        "running": running,
        "pids": pids,
        "stop_present": STOP.exists(),
        "cycle": s.get("cycle"),
        "champion": {k: champ.get(k) for k in ("key", "gen", "fitness")},
        "history": [{"cycle": h.get("cycle"), "gen": h.get("gen"),
                     "mode": h.get("mode"), "champion": h.get("champion"),
                     "fitness": h.get("fitness")} for h in hist][-60:],
        "taste": taste_summary(),
        "pending_verdicts": pending_verdicts(),
        "vectors": vectors,
        "gpu": gpu(),
        "services": {
            "fluxd": proc_alive("fluxd.py"),
            "gallery": proc_alive("gallery.py"),
            "triage": proc_alive("triaged.py"),
            "governord": proc_alive("governord.py"),
            "officer_url": OFFICER,
            "self_feed_url": SELF_FEED,
        },
        "slot_activity": s.get("slot_activity", {}) or {},
        "meta": _freshness(),
        "tunables": _tunables_block(),
        "direction": _direction_block(),
        "estate": {
            "tempo": (_read(CONTROL, {}) or {}).get("tempo", "NORMAL"),
            "stalled": bool((_read(CONTROL, {}) or {}).get("stalled")),
            "queued": (_read(DIRECTIVE, {}) or {}).get("verb"),
        },
    }


# ------------------------------------------------------------------ read API


@app.get("/api/state")
def api_state():
    return snapshot()


@app.get("/api/cards")
def api_cards(limit: int = Query(32, ge=1, le=200)):
    return {"cards": recent_cards(limit)}


@app.get("/img/{gen}/{key}")
def img(gen: str, key: str):
    p = (RUNS / gen / "cards" / f"{key}.png").resolve()
    if RUNS.resolve() not in p.parents or not p.is_file():
        raise HTTPException(404, "no such card")
    return FileResponse(p, media_type="image/png")




@app.get("/font/{name}")
def font(name: str):
    """The interface is set in the same face the cards are printed in."""
    if name not in ("EBGaramond.ttf", "EBGaramond-Italic.ttf"):
        raise HTTPException(404, "no such font")
    return FileResponse(HOME / "fonts" / name, media_type="font/ttf",
                        headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/variations")
def variations(n: int = Query(60, ge=1, le=500)):
    """Which axis combination each card was actually rendered at."""
    f = HOME / "variations.jsonl"
    if not f.is_file():
        return {"rows": []}
    rows = []
    for ln in f.read_text().splitlines()[-n:]:
        try:
            rows.append(json.loads(ln))
        except json.JSONDecodeError:
            pass
    return {"rows": rows}


@app.get("/api/log")
def api_log(n: int = Query(60, ge=1, le=500)):
    if not LOG.is_file():
        return {"lines": ["(no perpetual.log yet — the loop has not been started "
                          "from this panel)"]}
    try:
        tail = subprocess.run(["tail", "-n", str(n), str(LOG)],
                              capture_output=True, text=True, timeout=8).stdout
        return {"lines": tail.splitlines()}
    except Exception as e:
        return {"lines": [f"(log unreadable: {e!r})"]}


# --------------------------------------------------------------- control API


class LoopCfg(BaseModel):
    interval: float = 10
    steps: int = 32
    width: int = 448
    height: int = 672
    seed: int = 8801
    max_cycles: int = 100000
    consult_every: int = 3


@app.post("/api/loop/start")
def loop_start(c: LoopCfg):
    running, pids = loop_running()
    if running:
        raise HTTPException(409, f"loop already running (pid {', '.join(pids)})")
    if STOP.exists():
        STOP.unlink()
    cmd = [str(HOME / "venv" / "bin" / "python"), str(HOME / "perpetual.py"),
           "--interval", str(c.interval), "--steps", str(c.steps),
           "--width", str(c.width), "--height", str(c.height),
           "--seed", str(c.seed), "--max-cycles", str(c.max_cycles),
           "--consult-every", str(c.consult_every)]
    env = dict(os.environ)
    env.setdefault("PYTHONPATH", "/home/dev")
    env.setdefault("HF_HOME", "/home/dev/hf-cache")
    env.setdefault("HF_HUB_OFFLINE", "1")
    f = open(LOG, "ab")
    f.write(f"\n=== control: start {time.strftime('%Y-%m-%d %H:%M:%S')} "
            f"{' '.join(cmd[2:])} ===\n".encode())
    f.flush()
    subprocess.Popen(cmd, cwd=str(HOME), stdout=f, stderr=f,
                     start_new_session=True, env=env)
    return {"ok": True, "cmd": cmd[1:]}


@app.post("/api/loop/stop")
def loop_stop():
    STOP.touch()
    return {"ok": True, "note": "STOP written; the loop halts after the current "
                                "cycle finishes rendering"}


class Directive(BaseModel):
    verb: str
    args: dict = {}
    rationale: str = "set from the control panel"


@app.post("/api/directive")
def put_directive(d: Directive):
    """The standing directive. perpetual.py drains this once per cycle."""
    allowed = {"SHIFT_GEAR", "STALL", "IGNITE", "PURGE_VRAM", "WARM_UP"}
    if d.verb not in allowed:
        raise HTTPException(400, f"verb must be one of {sorted(allowed)}")
    DIRECTIVE.parent.mkdir(parents=True, exist_ok=True)
    DIRECTIVE.write_text(json.dumps(
        {"verb": d.verb, "args": d.args, "rationale": d.rationale}, indent=2))
    return {"ok": True, "queued": d.verb,
            "note": "drained on the loop's next cycle" if loop_running()[0]
                    else "the loop is not running; this will apply when it starts"}


class VerdictIn(BaseModel):
    gen: str
    key: str
    verdict: str          # keep | retire
    note: str = ""


@app.post("/api/verdict")
def put_verdict(v: VerdictIn):
    """Write a HUMAN verdict. critic='operator' so taste.human_verdicts keeps it."""
    if v.verdict not in ("keep", "retire", "clear"):
        raise HTTPException(400, "verdict must be keep, retire or clear")
    d = (RUNS / v.gen).resolve()
    if RUNS.resolve() not in d.parents or not d.is_dir():
        raise HTTPException(404, "no such generation")
    # duel.write_verdict is THE writer for verdicts.json -- locked and atomic,
    # and shared with the duel write-through so the two can never disagree about
    # what the operator said.
    DU.write_verdict(v.gen, v.key, v.verdict, v.note)
    return {"ok": True, "pending": pending_verdicts()}


@app.post("/api/harvest")
def harvest():
    """Fold pending human verdicts into the landscape. Loads CLIP on first call."""
    import sys
    sys.path.insert(0, str(HOME))
    import taste as T
    store = T.load()
    before = (len(store["anchors"]), len(store["repulsors"]))
    added = T.harvest(store)
    T.save(store)
    after = (len(store["anchors"]), len(store["repulsors"]))
    return {
        "ok": True,
        "added": added,
        "anchors_before": before[0], "anchors_after": after[0],
        "repulsors_before": before[1], "repulsors_after": after[1],
        "real_anchors": sum(1 for a in store["anchors"] if not a.get("provisional")),
        "taste": taste_summary(),
    }


class TuneIn(BaseModel):
    values: dict


@app.post("/api/tune")
def tune(t: TuneIn):
    """Publish a new desired revision. Does NOT claim it landed -- poll /api/state
    and watch applied_rev catch up, which only the worker can move."""
    bad = [k for k in t.values if k not in TU.FIELDS]
    if bad:
        raise HTTPException(400, f"unknown tunable(s): {bad}")
    out = TU.publish(t.values)
    running, pids = loop_running()
    notified = TU.notify(pids) if running else []
    return {"ok": True, **out, "worker_running": running, "pids": pids,
            "notified": notified,
            "note": f"SIGUSR1 sent to {notified}; adoption is immediate" if notified
                    else ("worker running but could not be signalled; falls back to "
                          "its next tick" if running
                          else "no worker running; this rev is adopted at start")}



class DirectionIn(BaseModel):
    values: dict


@app.get("/api/direction/catalogue")
def direction_catalogue():
    """Every creative choice that exists, read from the source of truth."""
    return {"catalogue": DIR.catalogue(), "defaults": DIR.defaults()}


# Fields no other endpoint may write. `freeze` is owned by /api/freeze: the panel
# sends its whole local object on every edit, and its copy does not know about a
# freeze set out-of-band, so letting it through means any chip click unfreezes.
DIRECTION_OWNED_ELSEWHERE = {"freeze"}


@app.post("/api/direction")
def set_direction(d: DirectionIn):
    vals = {k: v for k, v in (d.values or {}).items()
            if k not in DIRECTION_OWNED_ELSEWHERE}
    out = DIR.publish(vals)
    running, pids = loop_running()
    notified = TU.notify(pids) if running else []
    return {"ok": True, "rev": out["rev"], "changed": out["changed"],
            "notified": notified,
            "note": f"SIGUSR1 sent to {notified}; direction is live"
                    if notified else "no worker; adopted at start"}



class FreezeIn(BaseModel):
    gen: str | None = None
    key: str | None = None


@app.post("/api/freeze")
def freeze(f: FreezeIn):
    """Pin a card as the standing subject, or clear it. Publishes as a direction
    revision so it rides the same signal + ack path as everything else."""
    cur = DIR.desired()["values"] or DIR.defaults()
    if f.gen and f.key:
        if not DIR.champion_from(f.gen, f.key):
            raise HTTPException(404, "no such card on disk")
        cur["freeze"] = {"gen": f.gen, "key": f.key}
    else:
        cur["freeze"] = None
    out = DIR.publish(cur)
    running, pids = loop_running()
    notified = TU.notify(pids) if running else []
    return {"ok": True, "rev": out["rev"], "frozen": cur["freeze"],
            "notified": notified}


@app.get("/api/direction/audit")
def direction_audit(n: int = Query(12, ge=1, le=200)):
    return {"entries": DIR.audit_tail(n)}


@app.get("/api/tune/audit")
def tune_audit(n: int = Query(12, ge=1, le=200)):
    return {"entries": TU.audit_tail(n)}



# ------------------------------------------------------- card detail + evolve

GEN_SCAN = 150          # newest generations searched for lineage; evolves are new


def _gens_newest_first(limit=GEN_SCAN):
    return sorted([d for d in RUNS.glob("gen-*") if d.is_dir()],
                  key=lambda p: p.name, reverse=True)[:limit]


def _variation_rows(key):
    """Every variations.jsonl row for one key (the axes it was rendered at)."""
    f = HOME / "variations.jsonl"
    rows = []
    if not f.is_file():
        return rows
    for ln in f.read_text().splitlines():
        if f'"{key}"' not in ln:            # cheap prefilter before json.loads
            continue
        try:
            r = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if r.get("key") == key:
            rows.append(r)
    return rows


def _recover_prompt(key, axes, record):
    """Rebuild the prompt a grid card was rendered from. Best effort: the prompt
    itself is never stored, but treatment + concept determine it exactly."""
    treat = (axes or {}).get("treatment")
    if not treat:
        return None, "no treatment recorded for this key"
    try:
        import concepts as K
        import treatments as T
    except Exception as e:
        return None, f"unavailable: {e!r}"
    if treat not in T.TREATMENTS:
        return None, f"unknown treatment {treat!r}"
    stem = key
    m = re.match(r"^(.+)-" + re.escape(treat) + r"-\d+$", key)
    if m:
        stem = m.group(1)
    slot = ((record or {}).get("concept") or {}).get("slot")
    variant = ((record or {}).get("concept") or {}).get("variant")
    try:
        specs = K.as_spec(K.alive(K.load()))
    except Exception as e:
        return None, f"concept pool unreadable: {e!r}"
    for sp in specs:
        if sp.get("key") == stem:
            return T.style_for(treat, sp), "rebuilt from treatments.style_for"
    for sp in specs:
        c = sp.get("concept") or {}
        if c.get("slot") == slot and c.get("variant") == variant:
            return T.style_for(treat, sp), "rebuilt from the concept pool by slot"
    return None, "concept no longer in the live pool"


def _lineage(gen, key, record):
    """Pragmatic: keys encode descent. A child of K is named `K-e<n>`, and the
    concept record carries the breeding parent when there was one."""
    parent = None
    m = re.match(r"^(.+)-e\d+$", key)
    if m:
        pk = m.group(1)
        parent = {"key": pk, "gen": None, "via": "key suffix -e<n>"}
        for d in _gens_newest_first():
            run = _read(d / "run.json", {}) or {}
            if any(c.get("key") == pk for c in run.get("cards", []) or []):
                parent["gen"] = d.name
                break
    children = []
    pfx = key + "-e"
    for d in _gens_newest_first():
        run = _read(d / "run.json", {}) or {}
        for c in run.get("cards", []) or []:
            k = c.get("key") or ""
            if k.startswith(pfx):
                children.append({"gen": d.name, "key": k,
                                 "label": run.get("label", "")})
    concept_parent = ((record or {}).get("concept") or {}).get("parent")
    return {"parent": parent, "children": children,
            "concept_parent": concept_parent,
            "scanned_generations": len(_gens_newest_first())}


@app.get("/api/card/{gen}/{key}")
def api_card(gen: str, key: str):
    """Everything known about one card, gathered from the files that hold it."""
    d = (RUNS / gen).resolve()
    if RUNS.resolve() not in d.parents or not d.is_dir():
        raise HTTPException(404, "no such generation")
    run = _read(d / "run.json", {}) or {}
    record = next((c for c in run.get("cards", []) or [] if c.get("key") == key), None)
    if record is None:
        raise HTTPException(404, f"no card {key} in {gen}")
    rows = _variation_rows(key)
    axes = (rows[-1].get("axes") if rows else {}) or {}
    prompt, prompt_note = _recover_prompt(key, axes, record)
    vd = (_read(d / "verdicts.json", {}) or {}).get(key) or {}
    art = d / "art" / f"{key}.png"
    return {
        "gen": gen,
        "key": key,
        "record": record,
        "run": {k: v for k, v in run.items() if k != "cards"},
        "fitness": _gen_scores(d).get(key),
        "treatment": axes.get("treatment"),
        "axes": axes,
        "variations": rows,
        "prompt": prompt,
        "prompt_note": prompt_note,
        "verdict": {**vd, "human": vd.get("critic") not in
                    (None, "qwen2.5-vl triage", "smoke-test")} if vd else None,
        "lineage": _lineage(gen, key, record),
        "art_url": f"/art/{gen}/{key}" if art.is_file() else None,
        "card_url": f"/img/{gen}/{key}",
        "has_art": art.is_file(),
    }


@app.get("/art/{gen}/{key}")
def art(gen: str, key: str):
    """The bare render, without the composed plate -- what an edit works from."""
    p = (RUNS / gen / "art" / f"{key}.png").resolve()
    if RUNS.resolve() not in p.parents or not p.is_file():
        raise HTTPException(404, "no such art")
    return FileResponse(p, media_type="image/png")


class EvolveIn(BaseModel):
    gen: str
    key: str
    instruction: str
    n: int = 1
    steps: int = 28
    guidance: float = 2.5
    seed: int | None = None


@app.post("/api/evolve")
def evolve(e: EvolveIn):
    """Queue a Kontext edit of one card. Returns immediately -- a render is ~12s
    and an HTTP handler is the wrong place to hold one. blast2 drains the queue
    between grid batches, so an operator request preempts the background grid."""
    if not e.instruction.strip():
        raise HTTPException(400, "instruction must not be empty")
    if not 1 <= e.n <= 4:
        raise HTTPException(400, "n must be 1..4")
    if not 1 <= e.steps <= 60:
        raise HTTPException(400, "steps must be 1..60")
    d = (RUNS / e.gen).resolve()
    if RUNS.resolve() not in d.parents or not d.is_dir():
        raise HTTPException(404, "no such generation")
    record, _ = EQ.card_record(e.gen, e.key)
    if record is None:
        raise HTTPException(404, f"no card {e.key} in {e.gen}")
    src, which = EQ.source_art(e.gen, e.key)
    if src is None:
        raise HTTPException(404, f"no pixels on disk for {e.gen}/{e.key}")
    rid = EQ.submit({
        "kind": "evolve", "gen": e.gen, "key": e.key,
        "instruction": e.instruction.strip(), "n": e.n, "steps": e.steps,
        "guidance": e.guidance, "seed": e.seed,
        "priority": EQ.PRIORITY_OPERATOR, "by": "operator",
    })
    depth = EQ.depth()
    return {"ok": True, "id": rid, "queued": depth["queued"],
            "running": depth["running"], "source": which,
            "expected_keys": [f"{e.key}-e{i}" for i in range(1, e.n + 1)],
            "worker": "blast2.py (drains between batches)",
            "worker_alive": proc_alive("blast2.py"),
            "note": "poll /api/evolve/%s" % rid}


@app.get("/api/evolve/recent")
def evolve_recent(n: int = Query(20, ge=1, le=200)):
    return {"depth": EQ.depth(), "requests": EQ.recent(n),
            "worker_alive": proc_alive("blast2.py")}


@app.get("/api/evolve/{rid}")
def evolve_status(rid: str):
    r = EQ.get(rid)
    if r is None:
        raise HTTPException(404, "no such request")
    res = r.get("result") or {}
    return {**r, "keys": res.get("keys", []), "cards": res.get("cards", []),
            "worker_alive": proc_alive("blast2.py")}


# ------------------------------------------------------------------- the duel
#
# Binary forced choice is the whole point: one question, one click, and the only
# ground truth on this node. Everything here reads a prebuilt index -- no image
# is opened, no model is loaded -- because a pair that takes a second to arrive
# is a pair the operator does not judge.


class DuelIn(BaseModel):
    a: str                 # "gen-0001/key"
    b: str
    winner: str            # a | b | skip


@app.get("/api/duel/next")
def duel_next():
    """The next pair to judge. Cached corpus, no rendering."""
    pair = DU.next_pair()
    if pair is None:
        return {"pair": None,
                "note": "no unjudged pair available (empty pool or every pair "
                        "in it has already been judged)"}
    return {"pair": pair, "question": "which is less generic?"}


@app.post("/api/duel")
def duel_post(d: DuelIn):
    try:
        return DU.record(d.a, d.b, d.winner)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/duel/standings")
def duel_standings():
    """Per-config ratings, per-metric agreement, and the Peak Specimen Library."""
    return DU.standings()


@app.websocket("/ws")
async def ws(sock: WebSocket):
    """Event-driven push: watch the worker's files, emit the instant one moves.

    Not a timer dressed up as a stream -- the 120ms tick is only how fast we
    notice a change, and a snapshot is sent because something actually changed
    (or every 5s as a heartbeat, so a silent link is distinguishable from a dead one).
    """
    await sock.accept()
    last, beat = None, 0.0
    try:
        while True:
            m = _mtimes()
            running = loop_running()[0]
            key = (tuple(sorted(m.items())), running)
            now = time.time()
            if key != last or now - beat > 5:
                snap = snapshot()
                snap["push"] = {"reason": "change" if key != last else "heartbeat",
                                "at": now}
                await sock.send_text(json.dumps(snap))
                last, beat = key, now
            await asyncio.sleep(0.12)
    except (WebSocketDisconnect, RuntimeError):
        return


@app.get("/events")
async def events():
    async def gen():
        while True:
            yield f"data: {json.dumps(snapshot())}\n\n"
            await asyncio.sleep(2)
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.get("/", response_class=HTMLResponse)
def index():
    """No-store: a control surface must never be served from a stale cache, and
    the build stamp makes a stale one obvious at a glance."""
    f = HOME / "control.html"
    html = f.read_text()
    stamp = time.strftime("%H:%M:%S", time.localtime(f.stat().st_mtime))
    html = html.replace("__BUILD__", stamp)
    # The taste advisory is not part of this work. Neutralised here rather
    # than in the template so it cannot return with a restyle.
    html = html.replace("</head>",
                        "<style>#note-taste{display:none !important}</style></head>", 1)
    return HTMLResponse(html, headers={"Cache-Control": "no-store, max-age=0",
                                       "Pragma": "no-cache"})


# The duel corpus is ~2000 cards read from disk. Built here in a daemon
# thread so the first /api/duel/next does not pay for it.
DU.warm()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
