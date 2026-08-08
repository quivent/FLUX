#!/home/dev/venv/bin/python
"""Operator evolve requests: a file-backed priority queue, plus its executor.

Two append-only logs and one lock file, no dependencies beyond the stdlib:

    evolve_queue.jsonl     one submitted request per line, never rewritten
    evolve_results.jsonl   one EVENT per line (claimed / done / error)
    evolve_queue.lock      flock, held only across read-decide-append

Append-only means a crash mid-write costs at most one trailing line, and two
readers never see a half-rewritten file. Status is derived by replaying the
event log rather than stored, so `claim` is the only writer that has to be
atomic -- and it is, because the whole read-decide-append runs under an
exclusive flock.

Priority is a plain integer, higher first, ties broken by submission order.
Operator evolves come in at PRIORITY_OPERATOR so they sort ahead of anything a
background process might ever enqueue.

The executor half (`serve_pending`) lives here rather than in blast2 so the
render loop's hook stays four lines: it claims a request, drives fluxd's
Kontext /edit, and publishes the results as ordinary cards.
"""
import fcntl
import json
import os
import pathlib
import time
import urllib.request

HOME = pathlib.Path("/home/dev")
QUEUE = HOME / "evolve_queue.jsonl"
RESULTS = HOME / "evolve_results.jsonl"
LOCK = HOME / "evolve_queue.lock"
PUBLISH_LOCK = HOME / "evolve_publish.lock"
RUNS = HOME / "runs"
DAEMON = "http://127.0.0.1:8080"

PRIORITY_OPERATOR = 100
PRIORITY_BACKGROUND = 10


class _Flock:
    """Exclusive advisory lock on a sentinel file. Released even on exception."""

    def __init__(self, path):
        self.path = pathlib.Path(path)
        self.fh = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(self.path, "a+")
        fcntl.flock(self.fh, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        try:
            fcntl.flock(self.fh, fcntl.LOCK_UN)
        finally:
            self.fh.close()
            self.fh = None
        return False


def _append(path, obj):
    line = json.dumps(obj, separators=(",", ":")) + "\n"
    with open(path, "a") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())


def _lines(path):
    try:
        raw = pathlib.Path(path).read_text()
    except OSError:
        return []
    out = []
    for ln in raw.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:      # a torn trailing line; ignore it
            pass
    return out


def _events():
    """id -> {claimed_at, finished_at, status, result, error, worker}."""
    ev = {}
    for e in _lines(RESULTS):
        rid = e.get("id")
        if not rid:
            continue
        cur = ev.setdefault(rid, {})
        kind = e.get("event")
        if kind == "claimed":
            cur["status"] = "running"
            cur["claimed_at"] = e.get("at")
            cur["worker"] = e.get("worker")
        elif kind == "done":
            cur["status"] = "done"
            cur["finished_at"] = e.get("at")
            cur["result"] = e.get("result")
        elif kind == "error":
            cur["status"] = "error"
            cur["finished_at"] = e.get("at")
            cur["error"] = e.get("error")
    return ev


def _merge(req, ev):
    r = dict(req)
    e = ev.get(req["id"], {})
    r["status"] = e.get("status", "queued")
    for k in ("claimed_at", "finished_at", "result", "error", "worker"):
        if k in e:
            r[k] = e[k]
    return r


# ---------------------------------------------------------------- queue API


def submit(req):
    """Append a request; returns its id. Caller supplies the payload fields."""
    r = dict(req or {})
    r.setdefault("id", "ev-%d-%04x" % (int(time.time() * 1000), int.from_bytes(os.urandom(2), "big")))
    r.setdefault("at", time.time())
    r.setdefault("kind", "evolve")
    r.setdefault("priority", PRIORITY_OPERATOR)
    r.setdefault("n", 1)
    r.setdefault("steps", 28)
    r.setdefault("guidance", 2.5)
    r["status"] = "queued"
    with _Flock(LOCK):
        _append(QUEUE, r)
    return r["id"]


def claim(worker=None):
    """Take the highest-priority unclaimed request, or None.

    The read of both logs and the append of the `claimed` event happen inside
    one exclusive flock, so two workers can never take the same request.
    """
    with _Flock(LOCK):
        ev = _events()
        pending = [r for r in _lines(QUEUE) if r.get("id") and r["id"] not in ev]
        if not pending:
            return None
        pending.sort(key=lambda r: (-int(r.get("priority", 0)), r.get("at", 0)))
        req = pending[0]
        _append(RESULTS, {"id": req["id"], "event": "claimed", "at": time.time(),
                          "worker": worker or f"pid{os.getpid()}"})
    req["status"] = "running"
    return req


def complete(rid, result):
    with _Flock(LOCK):
        _append(RESULTS, {"id": rid, "event": "done", "at": time.time(),
                          "result": result})
    return True


def fail(rid, error):
    with _Flock(LOCK):
        _append(RESULTS, {"id": rid, "event": "error", "at": time.time(),
                          "error": str(error)[:2000]})
    return True


def get(rid):
    ev = _events()
    for r in _lines(QUEUE):
        if r.get("id") == rid:
            return _merge(r, ev)
    return None


def recent(n=20):
    ev = _events()
    rows = [_merge(r, ev) for r in _lines(QUEUE) if r.get("id")]
    rows.sort(key=lambda r: r.get("at", 0), reverse=True)
    return rows[: max(1, int(n))]


def depth():
    """How many requests are waiting, and how many are mid-render."""
    ev = _events()
    q = r = 0
    for row in _lines(QUEUE):
        st = ev.get(row.get("id"), {}).get("status", "queued")
        if st == "queued":
            q += 1
        elif st == "running":
            r += 1
    return {"queued": q, "running": r}


# ------------------------------------------------------------- the executor


def source_art(gen, key):
    """The pixels to edit: the bare art if we kept it, else the composed card."""
    for sub in ("art", "cards"):
        p = RUNS / gen / sub / f"{key}.png"
        if p.is_file():
            return p, sub
    return None, None


def card_record(gen, key):
    try:
        run = json.loads((RUNS / gen / "run.json").read_text())
    except (OSError, json.JSONDecodeError):
        return None, {}
    for c in run.get("cards", []) or []:
        if c.get("key") == key:
            return c, run
    return None, run


def _spec_for(record):
    """Recover the fields compose() needs but the manifest does not store
    (rgb, accent, botanical, subject) from the live concept pool, falling back
    to collection.SPEC and finally to a neutral accent."""
    import collection as C
    slot = ((record or {}).get("concept") or {}).get("slot")
    variant = ((record or {}).get("concept") or {}).get("variant")
    try:
        import concepts as K
        for c in K.alive(K.load()):
            if c.get("slot") == slot and (variant is None or c.get("variant") == variant):
                return c
        for c in K.alive(K.load()):
            if c.get("slot") == slot:
                return c
    except Exception:
        pass
    for s in getattr(C, "SPEC", []):
        if s.get("key") == slot or s.get("product") == (record or {}).get("product"):
            return s
    return {}


def _edit(image, instruction, steps, guidance, seed, stem, timeout=900):
    body = json.dumps({"image": str(image), "instruction": instruction,
                       "steps": int(steps), "guidance": float(guidance),
                       "seed": seed, "stem": stem}).encode()
    req = urllib.request.Request(DAEMON + "/edit", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        out = json.load(r)
    # /edit answers with a bare image dict; /batch answers with {"images":[...]}.
    if isinstance(out, dict) and "images" in out:
        out = out["images"][0]
    return out


def run_one(req, publish_lock=None, log=print):
    """Render one claimed request and publish its children as ordinary cards.

    Child keys are `<parentkey>-e<i>` so the parent is recoverable from the key
    alone -- that is what /api/card lineage walks.
    """
    import collection as C

    gen, key = req["gen"], req["key"]
    src, which = source_art(gen, key)
    if src is None:
        raise FileNotFoundError(f"no art or card png for {gen}/{key}")
    record, run = card_record(gen, key)
    if record is None:
        raise FileNotFoundError(f"{key} is not in {gen}/run.json")
    spec = _spec_for(record)

    n = max(1, min(4, int(req.get("n", 1) or 1)))
    steps = int(req.get("steps", 28) or 28)
    guidance = float(req.get("guidance", 2.5) or 2.5)
    base_seed = req.get("seed")

    # Compose at the parent's card geometry so an evolved card sits on the wall
    # at the same size as the grid card it came from.
    old_wh = (C.W, C.H)
    C.W = int(run.get("width") or old_wh[0])
    C.H = int(run.get("height") or old_wh[1])

    made, errors = [], []
    try:
        new_run = C.new_run(f"evolve {key}: {req.get('instruction','')[:60]}",
                            steps, n)
        for i in range(1, n + 1):
            child = f"{key}-e{i}"
            seed = (int(base_seed) + i - 1) if base_seed is not None else None
            t0 = time.time()
            img = _edit(src, req["instruction"], steps, guidance, seed, child)
            card = dict(spec)
            card.update({k: record.get(k) for k in
                         ("product", "subtitle", "family", "character", "role",
                          "quote", "concept") if record.get(k) is not None})
            card["key"] = child
            card["rgb"] = tuple(card.get("rgb") or (90, 84, 78))
            try:
                (C.ART_DIR / f"{child}.png").write_bytes(
                    pathlib.Path(img["path"]).read_bytes())
                plate = C.compose(card)
                if publish_lock is not None:
                    with publish_lock, _Flock(PUBLISH_LOCK):
                        C.publish(new_run, card, plate, img)
                else:
                    with _Flock(PUBLISH_LOCK):
                        C.publish(new_run, card, plate, img)
            except Exception as e:                      # one child, not the lot
                errors.append(f"{child}: {e!r}")
                log(f"[evolve] publish failed {child}: {e!r}")
                continue
            made.append({"gen": new_run.name, "key": child,
                         "seconds": round(img.get("seconds") or (time.time() - t0), 2),
                         "seed": img.get("seed")})
            log(f"[evolve] {req['id']} -> {new_run.name}/{child} "
                f"({time.time()-t0:.1f}s)")
        # The record of what each evolved card actually got, same log the grid
        # writes so /api/variations sees evolves too.
        try:
            with open(HOME / "variations.jsonl", "a") as f:
                for m in made:
                    f.write(json.dumps({"key": m["key"], "at": time.time(),
                                        "axes": {"evolve": req["instruction"][:120]},
                                        "parent": key, "mode": "evolve"}) + "\n")
        except OSError:
            pass
    finally:
        C.W, C.H = old_wh

    if not made:
        raise RuntimeError("; ".join(errors) or "no children rendered")
    return {"gen": made[0]["gen"], "keys": [m["key"] for m in made],
            "cards": made, "source": f"{gen}/{which}/{key}.png",
            "errors": errors}


def serve_pending(publish_lock=None, log=print, budget=4, worker=None):
    """Drain the queue, highest priority first. Returns how many ran.

    Called from the render loop BETWEEN batches, so operator requests preempt
    the grid without ever running a second /batch concurrently: there is one
    caller of the GPU at a time, by construction.
    """
    ran = 0
    while ran < budget:
        req = claim(worker=worker)
        if req is None:
            break
        log(f"[evolve] claim {req['id']} {req.get('gen')}/{req.get('key')} "
            f"n={req.get('n')}: {str(req.get('instruction'))[:70]}")
        try:
            complete(req["id"], run_one(req, publish_lock=publish_lock, log=log))
        except Exception as e:
            log(f"[evolve] FAILED {req['id']}: {e!r}")
            fail(req["id"], repr(e))
        ran += 1
    return ran


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "drain":
        print("ran", serve_pending())
    else:
        print(json.dumps({"depth": depth(), "recent": recent(5)}, indent=2))
