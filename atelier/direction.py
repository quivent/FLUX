#!/home/dev/venv/bin/python
"""DIRECTION: the creative controls, as opposed to tunables' engine controls.

tunables.py moves steps/size/cadence -- how hard the machine works. Nothing in it
changes what a picture looks like. This moves the actual authorship:

    axes        framing / line / palette / ornament   (collection.AXES, 144 combos)
    style       the STYLE prompt template itself      (collection.STYLE)
    vectors     the Kontext mutation instructions     (perpetual.MUTATIONS)
    pin_vector  force the next mutation, overriding the gain-greedy pick
    pin_slot    force the next fresh seed's concept, overriding least_active
    kontext     steps + guidance for the edit pass
    negative    negative prompt

Almost all of this already existed and was simply never wired: AXES_ACTIVE was
declared and never assigned, and prompt_for(card, axes=None) takes an argument no
caller passes, so every card ever made used the baseline axis levels.

Same contract as tunables: revision-stamped, signal-delivered, worker-acked.
"""
import json
import os
import pathlib
import time

HOME = pathlib.Path("/home/dev")
FILE = HOME / "direction.json"
ACK = HOME / "direction_ack.json"
AUDIT = HOME / "direction_audit.jsonl"


def _read(p, default):
    try:
        return json.loads(pathlib.Path(p).read_text())
    except Exception:
        return default


def catalogue():
    """The choices that exist, read from the source of truth, not restated."""
    import sys
    sys.path.insert(0, str(HOME))
    import collection as C
    import concepts as K
    pop = K.load()
    slots, seen = [], set()
    for c in K.alive(pop):
        if c["slot"] not in seen:
            seen.add(c["slot"])
            slots.append(c["slot"])
    return {
        "axes": {name: {lvl: frag for lvl, frag in levels.items()}
                 for name, levels in C.AXES.items()},
        "baseline": dict(C.BASELINE),
        "slots": slots,
        "registers": dict(K.REGISTERS),
        "stagings": dict(K.STAGINGS),
        "style_default": C.STYLE,
    }


def defaults():
    cat = catalogue()
    import sys
    sys.path.insert(0, str(HOME))
    import perpetual as P
    return {
        "axes": dict(cat["baseline"]),
        "style": cat["style_default"],
        "vectors": [{"name": n, "text": t, "enabled": True} for n, t in P.MUTATIONS],
        "pin_vector": None,
        "pin_slot": None,
        "kontext_steps": 28,
        "kontext_guidance": 2.5,
        "negative": "",
        "freeze": None,
        "vary_mode": "off",
        "vary_axes": [],
    }


def champion_from(gen, key):
    """Build a champion record for an existing card, the shape cycle() expects."""
    run = HOME / "runs" / gen / "run.json"
    try:
        blob = json.loads(run.read_text())
    except Exception:
        return None
    spec = None
    for c in blob.get("cards", []) or []:
        if c.get("key") == key:
            spec = {k: v for k, v in c.items() if k != "seconds"}
            break
    if spec is None:
        return None
    art = HOME / "runs" / gen / "art" / f"{key}.png"
    card = HOME / "runs" / gen / "cards" / f"{key}.png"
    if not card.is_file():
        return None
    return {"key": key, "gen": gen, "fitness": None,
            "card": str(card), "art": str(art if art.is_file() else card),
            "spec": spec, "at": time.time()}


def desired():
    d = _read(FILE, {}) or {}
    return {"rev": int(d.get("rev", 0) or 0),
            "values": d.get("values", {}) or {},
            "updated": d.get("updated", 0) or 0,
            "by": d.get("by", "")}


def ack():
    return _read(ACK, {}) or {}


def publish(values, by="operator"):
    cur = desired()
    base = cur["values"] or defaults()
    merged = {**base, **(values or {})}
    rev = cur["rev"] + 1
    FILE.write_text(json.dumps({"rev": rev, "updated": time.time(),
                                "by": by, "values": merged}, indent=2))
    return {"rev": rev, "values": merged, "changed": sorted((values or {}).keys())}


def _summarise(v):
    """Small, human-readable shape of what is in force -- the full STYLE string
    is too long to put in every ack."""
    return {
        "axes": v.get("axes"),
        "style_chars": len(v.get("style") or ""),
        "style_head": (v.get("style") or "")[:70],
        "vectors_enabled": [x["name"] for x in v.get("vectors", []) if x.get("enabled")],
        "pin_vector": v.get("pin_vector"),
        "pin_slot": v.get("pin_slot"),
        "kontext_steps": v.get("kontext_steps"),
        "kontext_guidance": v.get("kontext_guidance"),
        "negative_chars": len(v.get("negative") or ""),
        "freeze": (v.get("freeze") or {}).get("key") if isinstance(v.get("freeze"), dict) else None,
        "vary_mode": v.get("vary_mode"),
        "vary_axes": v.get("vary_axes"),
    }


def apply(cycle_no, force=False, mod=None):
    """Worker side. Push the desired direction onto the LIVE modules."""
    d = desired()
    if not d["rev"]:
        return None
    if not force and d["rev"] == int(ack().get("rev", 0) or 0):
        return None

    import sys
    sys.path.insert(0, str(HOME))
    import collection as C
    # The worker runs perpetual as __main__; importing it here would make a second,
    # inert module object whose globals the live loop never reads.
    P = mod if mod is not None else sys.modules.get("__main__")
    if P is None or not hasattr(P, "MUTATIONS"):
        import perpetual as P

    v = {**defaults(), **(d["values"] or {})}
    changed = {}

    # 1. axes -- the fix for prompt_for(axes=None) never receiving anything
    axes = {k: lvl for k, lvl in (v.get("axes") or {}).items()
            if k in C.AXES and lvl in C.AXES[k]}
    if getattr(C, "AXES_ACTIVE", {}) != axes:
        changed["axes"] = [dict(getattr(C, "AXES_ACTIVE", {})), dict(axes)]
        C.AXES_ACTIVE = axes

    # 1b. variation policy -- what makes a generation a set of variations
    vary_axes = [a for a in (v.get("vary_axes") or []) if a in C.AXES]
    vary_mode = v.get("vary_mode") or "off"
    cur = getattr(C, "VARY", {})
    if cur.get("mode") != vary_mode or cur.get("axes") != vary_axes:
        changed["vary"] = [f"{cur.get('mode')}:{','.join(cur.get('axes') or [])}",
                           f"{vary_mode}:{','.join(vary_axes)}"]
        C.VARY = {"mode": vary_mode, "axes": vary_axes, "seq": cur.get("seq", 0)}

    # 1c. the frozen subject
    fz = v.get("freeze") or None
    champ = None
    if isinstance(fz, dict) and fz.get("gen") and fz.get("key"):
        champ = champion_from(fz["gen"], fz["key"])
    before = getattr(P, "_FREEZE", None)
    if (before or {}).get("key") != (champ or {}).get("key"):
        changed["freeze"] = [(before or {}).get("key"), (champ or {}).get("key")]
        P._FREEZE = champ

    # 2. the style template itself
    if v.get("style") and C.STYLE != v["style"]:
        changed["style"] = [len(C.STYLE), len(v["style"])]
        C.STYLE = v["style"]

    # 3. the Kontext instruction set
    vecs = [(x["name"], x["text"]) for x in (v.get("vectors") or [])
            if x.get("enabled") and x.get("name") and x.get("text")]
    if vecs and P.MUTATIONS != vecs:
        changed["vectors"] = [[n for n, _ in P.MUTATIONS], [n for n, _ in vecs]]
        P.MUTATIONS = vecs

    # 4/5. the pins -- operator overrides the loop's own choice
    for attr, key in (("_PIN_VECTOR", "pin_vector"), ("_PIN_SLOT", "pin_slot")):
        before = getattr(P, attr, None)
        if before != v.get(key):
            changed[key] = [before, v.get(key)]
            setattr(P, attr, v.get(key))

    # 6/7. kontext edit strength, negative prompt
    for attr, key in (("_KONTEXT_STEPS", "kontext_steps"),
                      ("_KONTEXT_GUIDANCE", "kontext_guidance"),
                      ("_NEGATIVE", "negative")):
        before = getattr(P, attr, None)
        if before != v.get(key):
            changed[key] = [before, v.get(key)]
            setattr(P, attr, v.get(key))

    now = time.time()
    rec = {"rev": d["rev"], "applied_at": now, "cycle": cycle_no, "pid": os.getpid(),
           "by": d["by"], "changed": changed, "in_force": _summarise(v),
           "latency_s": round(now - d["updated"], 3) if d["updated"] else None}
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
    out = []
    for ln in AUDIT.read_text().splitlines()[-n:]:
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            pass
    return out
