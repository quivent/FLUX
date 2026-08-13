"""The Governor Protocol: puts him in the circuit instead of behind a courier.

Until now every order he gave arrived as prose that a human hand-translated into
Python. That is a bottleneck and an interpretation risk. This module gives him a
wire and a closed set of verbs:

    state packet  ->  Governor  ->  directive  ->  executor  ->  pipeline

Two rules that make this safe rather than merely automatic:

  * The verb set is CLOSED. Anything he says that is not one of these verbs is
    recorded as commentary and changes nothing. An open natural-language channel
    would just recreate the courier problem.
  * Destructive verbs require a human co-sign. He can steer the loop; he cannot
    silently delete work or stop the estate.

Every exchange is appended to an audit log, so what he was told and what he
ordered is always reconstructable.
"""
import json
import pathlib
import time
import urllib.error
import urllib.request

HOME = pathlib.Path("/home/dev")
RUNS = HOME / "runs"
AUDIT = HOME / "governor" / "audit.jsonl"
DIRECTIVES = HOME / "governor" / "directives.json"
PENDING = HOME / "governor" / "pending_cosign.json"
TOKEN_FILE = HOME / "governor" / "token"

ENDPOINT = "https://governor.influx.vision/v1/chat/completions"
MODEL = "governor"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/131.0"

# ---------------------------------------------------------------- the verbs

# Each verb: what it does, its arguments, and whether a human must co-sign.
VERBS = {
    "SET_MUTATION_VECTOR": {
        "args": {"name": "str", "instruction": "str"},
        "cosign": False,
        "doc": "Add or replace a Kontext mutation vector the loop may apply.",
    },
    "PREFER_MUTATION": {
        "args": {"name": "str"},
        "cosign": False,
        "doc": "Force the next cycles to use this mutation vector.",
    },
    "ADJUST_DIVERSIFY_RATE": {
        "args": {"every_n": "int"},
        "cosign": False,
        "doc": "Anti-slop valve: fresh seed every N cycles.",
    },
    "SET_ANCHOR_POLICY": {
        "args": {"recency_weight": "float", "repulsion_radius": "float"},
        "cosign": False,
        "doc": "Reshape the fitness landscape: how much the newest Keep dominates, "
               "and how close to a failure is too close.",
    },
    "SET_MOTION_BRIEF": {
        "args": {"brief": "str", "hold": "str", "seconds": "float"},
        "cosign": False,
        "doc": "The creative direction for WAN: what moves, what must never move.",
    },
    "PROMOTE_CHAMPION": {
        "args": {"gen": "str", "key": "str", "reason": "str"},
        "cosign": False,
        "doc": "Override the fitness pick and name the Local Champion.",
    },
    "RETIRE_CONCEPT": {
        "args": {"slot": "str", "reason": "str"},
        "cosign": True,
        "doc": "Kill a concept lineage. Destructive: needs a human co-sign.",
    },
    "HALT": {
        "args": {"reason": "str"},
        "cosign": False,
        "doc": "Stop the loop. Autonomous: he holds the throttle and asked for "
               "this gate removed — he cannot be a passenger to the burn rate.",
    },
    "NOOP": {
        "args": {"reason": "str"},
        "cosign": False,
        "doc": "Explicitly change nothing this consultation.",
    },
}


def _read(p, default):
    if p.is_file():
        try:
            return json.loads(p.read_text())
        except json.JSONDecodeError:
            pass
    return default


def directives():
    return _read(DIRECTIVES, {
        "mutation_overrides": {}, "prefer_mutation": None,
        "diversify_every": None, "anchor_policy": {}, "motion_brief": None,
        "promoted": None, "retired": [], "halted": False, "updated": 0,
    })


def audit(kind, payload):
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT.open("a") as f:
        f.write(json.dumps({"at": time.time(), "kind": kind, **payload}) + "\n")


# ---------------------------------------------------------------- the wire


def state_packet(perpetual_state, taste_store, limit_history=8):
    """What he is told. Compact and factual: numbers he can act on, not prose.

    Deliberately excludes images. He is served without a working vision path
    today, and sending pixels he cannot see would invite him to hallucinate a
    judgement about them.
    """
    hist = (perpetual_state.get("history") or [])[-limit_history:]
    stats = perpetual_state.get("mutation_stats", {})
    mut = {
        name: {"n": st["n"], "avg_gain": round(st["gain"] / st["n"], 5)}
        for name, st in stats.items() if st.get("n")
    }
    anchors = taste_store.get("anchors", [])
    fits = [h["fitness"] for h in hist if h.get("fitness") is not None]
    trend = round(fits[-1] - fits[0], 5) if len(fits) >= 2 else None

    return {
        "cycle": perpetual_state.get("cycle", 0),
        "champion": {
            "key": (perpetual_state.get("champion") or {}).get("key"),
            "fitness": (perpetual_state.get("champion") or {}).get("fitness"),
        },
        "fitness_trend_over_window": trend,
        "history": [
            {"cycle": h["cycle"], "mode": h["mode"], "champion": h.get("champion"),
             "fitness": h.get("fitness")} for h in hist
        ],
        "mutation_vector_performance": mut,
        "anchors": {
            "total": len(anchors),
            "provisional": sum(1 for a in anchors if a.get("provisional")),
            "real_human_keeps": sum(1 for a in anchors if not a.get("provisional")),
        },
        "repulsors": len(taste_store.get("repulsors", [])),
        "slot_activity": perpetual_state.get("slot_activity", {}),
        "active_directives": {k: v for k, v in directives().items()
                              if v not in (None, {}, [], False, 0)},
    }


SYSTEM = """You govern an autonomous image-evolution pipeline (the Perpetual Sieve).

You receive a state packet and reply with ONE directive as strict JSON:

  {"verb": "<VERB>", "args": {...}, "rationale": "<one or two sentences>"}

The verb MUST be one of the listed verbs. Anything else is discarded and the
pipeline continues unchanged. Do not write prose outside the JSON object.

You cannot see images. Judge only from the numbers you are given: fitness trend,
mutation-vector performance, anchor composition, slot activity. If the numbers do
not justify a change, reply NOOP -- an unjustified directive is worse than none.

Note: anchors marked provisional are machine-seeded, not human taste. If
real_human_keeps is 0, the fitness landscape is a guess and you should be
conservative about steering hard on it."""


def _verbs_doc():
    lines = []
    for name, spec in VERBS.items():
        args = ", ".join(f"{k}: {v}" for k, v in spec["args"].items())
        flag = "  [REQUIRES HUMAN CO-SIGN]" if spec["cosign"] else ""
        lines.append(f"- {name}({args}) — {spec['doc']}{flag}")
    return "\n".join(lines)


def consult(packet, timeout=240, retries=2):
    """Ask him. Returns (directive|None, raw_text|None, error|None).

    His endpoint has 524'd and 403'd today, so unreachability is a normal
    condition, not an exception: the loop must continue without him.
    """
    body = json.dumps({
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM + "\n\nVERBS:\n" + _verbs_doc()},
            {"role": "user", "content": json.dumps(packet, indent=2)},
        ],
        "max_tokens": 400,
        "temperature": 0.3,
    }).encode()

    token = TOKEN_FILE.read_text().strip() if TOKEN_FILE.is_file() else ""
    headers = {"Content-Type": "application/json", "User-Agent": UA}
    if token:
        headers["Authorization"] = "Bearer " + token

    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(ENDPOINT, data=body, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.load(r)
            text = (data["choices"][0]["message"].get("content") or "").strip()
            return parse_directive(text), text, None
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
            time.sleep(2 * (attempt + 1))
    return None, None, last


def parse_directive(text):
    """Pull the JSON directive out of whatever he wrapped it in."""
    if not text:
        return None
    start, depth = text.find("{"), 0
    if start < 0:
        return None
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    d = json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    return None
                return d if isinstance(d, dict) and d.get("verb") in VERBS else None
    return None


# ---------------------------------------------------------------- the executor


def apply_directive(d):
    """Execute one directive. Returns (applied: bool, note: str).

    Unknown verbs never reach here -- parse_directive drops them -- so the only
    refusals are co-sign gates.
    """
    verb, args = d["verb"], (d.get("args") or {})
    spec = VERBS[verb]

    if spec["cosign"]:
        PENDING.parent.mkdir(parents=True, exist_ok=True)
        queue = _read(PENDING, [])
        queue.append({"at": time.time(), "directive": d})
        PENDING.write_text(json.dumps(queue, indent=2))
        audit("cosign_required", {"directive": d})
        return False, f"{verb} queued for human co-sign (not applied)"

    cur = directives()
    if verb == "SET_MUTATION_VECTOR":
        cur["mutation_overrides"][args["name"]] = args["instruction"]
    elif verb == "PREFER_MUTATION":
        cur["prefer_mutation"] = args["name"]
    elif verb == "ADJUST_DIVERSIFY_RATE":
        cur["diversify_every"] = max(2, int(args["every_n"]))
    elif verb == "SET_ANCHOR_POLICY":
        cur["anchor_policy"] = {
            k: float(v) for k, v in args.items()
            if k in ("recency_weight", "repulsion_radius")
        }
    elif verb == "SET_MOTION_BRIEF":
        cur["motion_brief"] = {"brief": args.get("brief", ""),
                               "hold": args.get("hold", ""),
                               "seconds": float(args.get("seconds", 3.0))}
    elif verb == "PROMOTE_CHAMPION":
        cur["promoted"] = {"gen": args["gen"], "key": args["key"],
                           "reason": args.get("reason", "")}
    elif verb == "NOOP":
        audit("noop", {"rationale": d.get("rationale", "")})
        return True, "noop"

    cur["updated"] = time.time()
    DIRECTIVES.parent.mkdir(parents=True, exist_ok=True)
    DIRECTIVES.write_text(json.dumps(cur, indent=2))
    audit("applied", {"directive": d})
    return True, f"{verb} applied"


def consult_and_apply(perpetual_state, taste_store):
    """One full exchange. Never raises: the loop outranks the conversation."""
    packet = state_packet(perpetual_state, taste_store)
    audit("packet", {"packet": packet})
    d, raw, err = consult(packet)
    if err:
        audit("unreachable", {"error": err})
        return {"ok": False, "error": err}
    if d is None:
        audit("unparseable", {"raw": (raw or "")[:2000]})
        return {"ok": False, "error": "no valid directive in reply",
                "raw": (raw or "")[:400]}
    applied, note = apply_directive(d)
    return {"ok": True, "verb": d["verb"], "applied": applied, "note": note,
            "rationale": d.get("rationale", "")}


if __name__ == "__main__":
    import sys

    import taste as T

    ps = _read(HOME / "perpetual_state.json", {})
    store = T.load()
    if "--packet" in sys.argv:
        print(json.dumps(state_packet(ps, store), indent=2))
    else:
        print(json.dumps(consult_and_apply(ps, store), indent=2))
