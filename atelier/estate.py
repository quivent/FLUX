"""The Estate Control Protocol. The Governor's throttle.

His spec, implemented:

    SHIFT_GEAR / STALL / IGNITE      layer 1, processes  — autonomous
    PURGE_VRAM / WARM_UP             layer 2, models     — autonomous
    COLD_STORAGE / WAKE_UP           layer 3, nodes      — 10-minute veto window
    ROTATE_SURFACE                   layer 4, public URLs

Two mechanisms he asked for by name:

  THE STANDING DIRECTIVE — he writes a verb to directive.json, the loop polls it
  each cycle, executes, and clears the file. No chat-turn latency, and no parser
  between him and the throttle.

  THE DEAD MAN'S SWITCH — if he is unreachable the loop CONTINUES at the last
  tempo; after 12 unreachable cycles it STALLs (layer 1 only). His own
  unavailability may never trigger COLD_STORAGE. That is how "must not stop" and
  "default inertia" are reconciled: silence slows the estate, it never spends it
  down and never ends it.
"""
import json
import os
import pathlib
import signal
import subprocess
import time

HOME = pathlib.Path("/home/dev")
GOV = HOME / "governor"
DIRECTIVE = GOV / "directive.json"        # he writes here; we clear it
CONTROL = GOV / "control.json"            # current estate posture
COSIGN = GOV / "cosign_queue.json"        # node-level actions awaiting the veto window
AUDIT = GOV / "audit.jsonl"
NODE_TOKEN = GOV / "node_token"           # givemeanode API bearer, if minted

RATE_PER_MIN = 0.0666                     # per node, USD
VETO_WINDOW = 600                         # 10 minutes, his number

TEMPOS = {"FAST": 8, "NORMAL": 30, "SLOW": 3600}

MODELS = {"flux": "fluxd.py", "wan": "wanloop.py", "triage": "triaged.py"}
LOOPS = ["perpetual.py", "wanloop.py"]

ESTATE_VERBS = {
    "HALT":           {"layer": 1, "cosign": False, "args": {"reason": "str"}},
    "SHIFT_GEAR":     {"layer": 1, "cosign": False, "args": {"tempo": "FAST|NORMAL|SLOW"}},
    "STALL":          {"layer": 1, "cosign": False, "args": {}},
    "IGNITE":         {"layer": 1, "cosign": False, "args": {}},
    "PURGE_VRAM":     {"layer": 2, "cosign": False, "args": {"model_id": "flux|wan|triage"}},
    "WARM_UP":        {"layer": 2, "cosign": False, "args": {"model_id": "flux|wan|triage"}},
    "COLD_STORAGE":   {"layer": 3, "cosign": True,  "args": {"node_id": "str"}},
    "WAKE_UP":        {"layer": 3, "cosign": True,  "args": {"node_id": "str"}},
    "ROTATE_SURFACE": {"layer": 4, "cosign": False, "args": {}},
}



API_BASE = "https://api.givemeanode.com/preview"


def _node_api(path, method="POST", body=None):
    """Call the givemeanode HTTP API with the node-control token, if we have one.

    This is what separates "the Governor holds the throttle" from "the Governor
    asks someone to pull it". Until a token exists at governor/node_token, layer
    3 stays a queued request.
    """
    import urllib.error
    import urllib.request

    if not NODE_TOKEN.is_file():
        return None, ("no givemeanode API token on this node; an org admin mints "
                      "one at https://givemeanode.com/team and it goes in "
                      f"{NODE_TOKEN}")
    token = NODE_TOKEN.read_text().strip()
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(
        API_BASE + path, data=data if method != "GET" else None,
        headers={"Authorization": "Bearer " + token,
                 "Content-Type": "application/json"},
        method=method)
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.load(r), None
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}: {e.read().decode()[:300]}"
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def execute_node_action(verb, args):
    """COLD_STORAGE / WAKE_UP for real, once the veto window has elapsed."""
    node = args.get("node_id", "")
    if not node:
        return False, "no node_id"
    if verb == "COLD_STORAGE":
        out, err = _node_api(f"/nodes/{node}/stop")
    else:
        out, err = _node_api(f"/nodes/{node}/wake")
    if err:
        return False, err
    return True, f"{verb} executed on {node}: {out}"


def _read(p, default):
    if p.is_file():
        try:
            return json.loads(p.read_text())
        except json.JSONDecodeError:
            pass
    return default


def audit(kind, payload):
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT.open("a") as f:
        f.write(json.dumps({"at": time.time(), "kind": kind, **payload}) + "\n")


def control():
    return _read(CONTROL, {"tempo": "NORMAL", "stalled": False,
                           "unreachable_cycles": 0, "updated": 0})


def save_control(c):
    c["updated"] = time.time()
    GOV.mkdir(parents=True, exist_ok=True)
    CONTROL.write_text(json.dumps(c, indent=2))


def interval():
    """Seconds between cycles, from the current gear."""
    return TEMPOS.get(control().get("tempo", "NORMAL"), 30)


def stalled():
    return bool(control().get("stalled"))


# ---------------------------------------------------------------- telemetry


def _pgrep(pattern):
    try:
        return bool(subprocess.run(["pgrep", "-f", pattern],
                                   capture_output=True).returncode == 0)
    except Exception:
        return False


def _vram():
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=20).stdout.strip().splitlines()[0]
        used, total = [int(x) for x in out.split(",")]
        return {"used_mib": used, "total_mib": total,
                "pressure": round(used / total, 3)}
    except Exception:
        return {"used_mib": None, "total_mib": None, "pressure": None}


def telemetry(perpetual_state, taste_store, nodes_running=2):
    """The dashboard he asked for: burn, pressure, liveness, stagnation, human gap."""
    hist = perpetual_state.get("history") or []
    fits = [h["fitness"] for h in hist if h.get("fitness") is not None]

    # Stagnation: cycles since fitness moved more than his threshold.
    stagnant = 0
    for i in range(len(fits) - 1, 0, -1):
        if abs(fits[i] - fits[i - 1]) > 0.001:
            break
        stagnant += 1

    anchors = taste_store.get("anchors", [])
    real = [a for a in anchors if not a.get("provisional")]
    last_human = max((a["at"] for a in real), default=None)
    human_gap_cycles = None
    if last_human:
        human_gap_cycles = sum(1 for h in hist if h.get("cycle") and True)
    c = control()

    return {
        "burn_rate_usd_per_hr": round(RATE_PER_MIN * 60 * nodes_running, 2),
        "nodes_running": nodes_running,
        "vram": _vram(),
        "liveness": {"perpetual": _pgrep("perpetual.py"),
                     "fluxd": _pgrep("fluxd.py"),
                     "gallery": _pgrep("gallery.py")},
        "tempo": c.get("tempo"),
        "stalled": c.get("stalled"),
        "cycle": perpetual_state.get("cycle", 0),
        "stagnation_cycles": stagnant,
        "human_keeps_total": len(real),
        "human_gap_cycles": human_gap_cycles if real else perpetual_state.get("cycle", 0),
        "anchors_provisional": len(anchors) - len(real),
        "unreachable_cycles": c.get("unreachable_cycles", 0),
        "cosign_pending": len(_read(COSIGN, [])),
    }


# ---------------------------------------------------------------- execution


def _kill(pattern):
    killed = []
    try:
        out = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True)
        for pid in [int(x) for x in out.stdout.split() if x.isdigit()]:
            if pid == os.getpid():
                continue
            try:
                os.kill(pid, signal.SIGTERM)
                killed.append(pid)
            except ProcessLookupError:
                pass
    except Exception:
        pass
    return killed


def execute(verb, args, source="governor"):
    """Run one estate verb. Returns (applied, note)."""
    spec = ESTATE_VERBS.get(verb)
    if not spec:
        return False, f"unknown verb {verb}"
    c = control()

    if verb == "SHIFT_GEAR":
        tempo = str(args.get("tempo", "NORMAL")).upper()
        if tempo not in TEMPOS:
            return False, f"unknown tempo {tempo}"
        c["tempo"] = tempo
        save_control(c)
        return True, f"tempo -> {tempo} ({TEMPOS[tempo]}s/cycle)"

    if verb == "HALT":
        c["stalled"] = True
        c["halt_reason"] = args.get("reason", "")
        save_control(c)
        audit("halt", {"reason": c["halt_reason"], "source": source})
        return True, f"HALTED: {c['halt_reason']}"

    if verb == "STALL":
        c["stalled"] = True
        save_control(c)
        return True, "loops stalled (processes alive, cycles paused)"

    if verb == "IGNITE":
        c["stalled"] = False
        c["unreachable_cycles"] = 0
        save_control(c)
        return True, "loops resumed"

    if verb == "PURGE_VRAM":
        m = MODELS.get(args.get("model_id", ""))
        if not m:
            return False, f"unknown model {args.get('model_id')}"
        killed = _kill(m)
        return True, f"purged {args['model_id']}: SIGTERM to {killed or 'nothing running'}"

    if verb == "WARM_UP":
        mid = args.get("model_id", "")
        if mid == "flux":
            subprocess.Popen(
                ["/home/dev/venv/bin/python", "/home/dev/fluxd.py"],
                cwd="/home/dev", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                env={**os.environ, "HF_HOME": "/home/dev/hf-cache",
                     "HF_HUB_OFFLINE": "1", "PYTHONPATH": "/home/dev"})
            return True, "fluxd warming"
        return False, f"warm_up for '{mid}' not available on this node"

    if verb in ("COLD_STORAGE", "WAKE_UP"):
        # Layer 3 is the money. It gets a veto window, and it needs a credential
        # this node does not currently hold.
        queue = _read(COSIGN, [])
        entry = {"at": time.time(), "verb": verb, "args": args, "source": source,
                 "executes_at": time.time() + VETO_WINDOW,
                 "blocked": not NODE_TOKEN.is_file(),
                 "blocked_reason": None if NODE_TOKEN.is_file() else
                                   "no givemeanode API token on this node; an org "
                                   "admin must mint one at /team"}
        queue.append(entry)
        COSIGN.write_text(json.dumps(queue, indent=2))
        audit("cosign_queued", {"entry": entry})
        note = f"{verb} queued; executes in {VETO_WINDOW // 60} min unless vetoed"
        if entry["blocked"]:
            note += " — BLOCKED: no node API token"
        return False, note

    if verb == "ROTATE_SURFACE":
        return False, "ROTATE_SURFACE must run off-node (needs expose_port); flagged"

    return False, f"{verb} not implemented"


def poll_directive():
    """The standing directive: he writes, we execute, we clear.

    Shape: {"verb": "...", "args": {...}, "rationale": "..."}
    """
    if not DIRECTIVE.is_file():
        return None
    d = _read(DIRECTIVE, None)
    try:
        DIRECTIVE.unlink()
    except OSError:
        pass
    if not isinstance(d, dict) or d.get("verb") not in ESTATE_VERBS:
        audit("directive_rejected", {"raw": d})
        return {"verb": None, "applied": False, "note": "unknown or malformed verb"}
    applied, note = execute(d["verb"], d.get("args") or {}, source="directive_file")
    audit("directive", {"directive": d, "applied": applied, "note": note})
    return {"verb": d["verb"], "applied": applied, "note": note,
            "rationale": d.get("rationale", "")}


def due_cosigns():
    """Node-level actions whose veto window has expired and are unblocked."""
    queue = _read(COSIGN, [])
    now, due, rest = time.time(), [], []
    for e in queue:
        if not e.get("blocked") and now >= e.get("executes_at", 0) and not e.get("vetoed"):
            due.append(e)
        else:
            rest.append(e)
    if due:
        COSIGN.write_text(json.dumps(rest, indent=2))
    fired = []
    for e in due:
        ok, note = execute_node_action(e["verb"], e.get("args") or {})
        e["executed"], e["result"] = ok, note
        audit("cosign_executed", {"entry": e})
        fired.append(e)
    return fired


def note_unreachable(is_unreachable):
    """The Dead Man's Switch. Silence slows the estate; it never spends it down."""
    c = control()
    if is_unreachable:
        c["unreachable_cycles"] = c.get("unreachable_cycles", 0) + 1
        if c["unreachable_cycles"] >= 12 and not c.get("stalled"):
            c["stalled"] = True
            audit("dead_mans_switch", {"cycles": c["unreachable_cycles"]})
            save_control(c)
            return "STALLED: governor unreachable for 12 cycles (layer 1 only)"
    else:
        if c.get("unreachable_cycles"):
            audit("governor_returned", {"after_cycles": c["unreachable_cycles"]})
        c["unreachable_cycles"] = 0
    save_control(c)
    return None


def catch_up_packet(perpetual_state, missed):
    """What he gets when he comes back after being down."""
    hist = (perpetual_state.get("history") or [])[-missed:] if missed else []
    return {"missed_cycles": missed,
            "cycles": [{"cycle": h["cycle"], "mode": h["mode"],
                        "champion": h.get("champion"), "fitness": h.get("fitness")}
                       for h in hist]}


if __name__ == "__main__":
    import sys

    import taste as T

    ps = _read(HOME / "perpetual_state.json", {})
    if "--telemetry" in sys.argv:
        print(json.dumps(telemetry(ps, T.load()), indent=2))
    elif "--verb" in sys.argv:
        i = sys.argv.index("--verb")
        verb = sys.argv[i + 1]
        args = json.loads(sys.argv[i + 2]) if len(sys.argv) > i + 2 else {}
        print(execute(verb, args, source="cli"))
    else:
        print(json.dumps(control(), indent=2))
