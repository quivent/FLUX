#!/home/dev/venv/bin/python
"""Live tunables: knobs the operator turns while the worker is already running.

perpetual.py freezes steps/size/interval into argv at launch, so before this the
only way to retune was stop -> edit -> restart, which costs a cycle and loses the
warm pipeline. This is a revision-stamped channel the worker polls once per tick:

    tunables.json        DESIRED  -- written by the control panel, rev bumped per edit
    tunables_ack.json    APPLIED  -- written by the WORKER once it adopts a rev
    tunables_audit.jsonl          -- append-only trail, one line per adoption

The ack is the whole point. The panel does not assert that a change landed; it
shows the worker's own acknowledgement -- its pid, its cycle, its timestamp, the
before/after of every field it moved -- and stays PENDING until the worker says
otherwise. A control you cannot prove landed is not a control you can tune with.

Bounds are enforced here, worker-side, because the panel is not the only thing
that can write tunables.json.
"""
import json
import os
import pathlib
import time

HOME = pathlib.Path("/home/dev")
FILE = HOME / "tunables.json"
ACK = HOME / "tunables_ack.json"
AUDIT = HOME / "tunables_audit.jsonl"

# name -> (cast, lo, hi)
FIELDS = {
    "interval":        (float, 0.5, 3600.0),
    "steps":           (int,   4,   80),
    "width":           (int,   256, 1024),
    "height":          (int,   256, 1536),
    "consult_every":   (int,   1,   1000),
    "variants":        (int,   1,   8),
    "diversify_every": (int,   1,   50),
}

# Which live object owns each field once the worker is running.
MODULE_FIELDS = {"variants": "VARIANTS", "diversify_every": "DIVERSIFY_EVERY"}


def _read(p, default):
    try:
        return json.loads(pathlib.Path(p).read_text())
    except Exception:
        return default


def clamp(name, v):
    cast, lo, hi = FIELDS[name]
    return max(lo, min(hi, cast(v)))


def desired():
    d = _read(FILE, {}) or {}
    return {"rev": int(d.get("rev", 0) or 0),
            "values": d.get("values", {}) or {},
            "updated": d.get("updated", 0) or 0,
            "by": d.get("by", "")}


def ack():
    return _read(ACK, {}) or {}


def publish(values, by="operator"):
    """Control-plane side: merge, clamp, bump the revision, write the desired set."""
    cur = desired()
    clean = {k: clamp(k, v) for k, v in (values or {}).items()
             if k in FIELDS and v is not None}
    merged = {**cur["values"], **clean}
    rev = cur["rev"] + 1
    FILE.write_text(json.dumps(
        {"rev": rev, "updated": time.time(), "by": by, "values": merged}, indent=2))
    return {"rev": rev, "values": merged, "changed": sorted(clean.keys())}


def live(a, mod):
    """What the worker is running RIGHT NOW, read off the live objects."""
    out = {}
    for k in FIELDS:
        if k in MODULE_FIELDS:
            out[k] = getattr(mod, MODULE_FIELDS[k], None)
        else:
            out[k] = getattr(a, k, None)
    return out


def apply(a, mod, cycle_no, force=False):
    """Worker side. Adopt the desired rev onto the live argv namespace `a` and the
    perpetual module `mod`. Returns the ack record when a NEW rev was adopted,
    None when there is nothing new. Cheap enough to call every tick.
    """
    d = desired()
    if not d["rev"]:
        return None
    if not force and d["rev"] == int(ack().get("rev", 0) or 0):
        return None

    changed = {}
    for k, v in d["values"].items():
        if k not in FIELDS:
            continue
        v = clamp(k, v)
        if k in MODULE_FIELDS:
            attr = MODULE_FIELDS[k]
            before = getattr(mod, attr, None)
            if before != v:
                setattr(mod, attr, v)
                changed[k] = [before, v]
        else:
            before = getattr(a, k, None)
            if before != v:
                setattr(a, k, v)
                changed[k] = [before, v]

    now = time.time()
    rec = {
        "rev": d["rev"],
        "applied_at": now,
        "cycle": cycle_no,
        "pid": os.getpid(),
        "by": d["by"],
        "changed": changed,
        "live": live(a, mod),
        "latency_s": round(now - d["updated"], 3) if d["updated"] else None,
    }
    ACK.write_text(json.dumps(rec, indent=2))
    try:
        with AUDIT.open("a") as f:
            f.write(json.dumps(rec) + "\n")
    except OSError:
        pass
    return rec


def audit_tail(n=10):
    if not AUDIT.is_file():
        return []
    try:
        lines = AUDIT.read_text().splitlines()[-n:]
    except OSError:
        return []
    out = []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            pass
    return out


# --------------------------------------------------------------- notification


def notify(pids):
    """Poke every running worker so it adopts NOW instead of at its next tick."""
    import signal
    out = []
    for pid in pids or []:
        try:
            os.kill(int(pid), signal.SIGUSR1)
            out.append(int(pid))
        except (ProcessLookupError, PermissionError, ValueError, OSError):
            pass
    return out


def install(a, mod, cycle_fn, extra=None):
    """Worker side: adopt on SIGUSR1, immediately, wherever the loop happens to be."""
    import signal

    def _handler(signum, frame):
        try:
            if extra:
                r2 = extra(cycle_fn())
                if r2:
                    print(f"[direction] SIGUSR1 -> adopted rev {r2['rev']} "
                          f"({', '.join(r2['changed']) or 'no field moved'}) "
                          f"{r2['latency_s']}s after publish", flush=True)
            rec = apply(a, mod, cycle_fn())
            if rec:
                delta = ", ".join(f"{k} {v[0]}->{v[1]}"
                                  for k, v in rec["changed"].items()) or "no field moved"
                print(f"[tunables] SIGUSR1 -> adopted rev {rec['rev']} ({delta}) "
                      f"{rec['latency_s']}s after publish", flush=True)
        except Exception as e:                      # never let a signal kill the loop
            print(f"[tunables] signal apply failed: {e!r}", flush=True)

    signal.signal(signal.SIGUSR1, _handler)

    # A restarted worker must re-adopt the standing revision. The ack outlives the
    # process, so without force=True this worker would run its argv while the panel
    # reported the previous worker's ack as current.
    if extra:
        try:
            extra(cycle_fn(), True)
        except Exception as e:
            print(f"[direction] startup adopt failed: {e!r}", flush=True)
    rec = apply(a, mod, cycle_fn(), force=True)
    if rec:
        delta = ", ".join(f"{k} {v[0]}->{v[1]}"
                          for k, v in rec["changed"].items()) or "already matching"
        print(f"[tunables] startup re-adopt rev {rec['rev']} ({delta})", flush=True)
    return True
