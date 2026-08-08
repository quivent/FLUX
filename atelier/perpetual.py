#!/home/dev/venv/bin/python
"""The Perpetual Sieve. The system defaults to MOTION; the human provides DIRECTION.

Governor's architecture, implemented:

    Generate -> Triage -> Genetic Selection -> Mutate -> Repeat

Per cycle:
  1. TRIAGE   Qwen2.5-VL deletes defects. It never rates quality.
  2. SCORE    survivors against the Anchor Set (the Ghost of Taste).
  3. SELECT   the Local Champion: highest fitness.
  4. MUTATE   Kontext edits the Champion along a mutation vector -> 4 variants.
              Survivors are refined, not re-rolled, so identity carries forward.
  5. DIVERSIFY every 3rd cycle is a Random Jump to the least-active concept,
              which is what stops convergence on one safe look.
  6. HARVEST  any human verdict that arrived is folded in and steers retroactively:
              a Keep becomes the new attractor, a Retire a repulsion zone.

It does not stop for a human. It does not stop at a cap. It stops on STOP.

    ./perpetual.py --interval 10
    touch /home/dev/STOP
"""
import argparse
import json
import pathlib
import sys
import time
import urllib.request

import collection as C
import concepts as K
import estate as E
import protocol as P
import taste as T
import direction as DIR
import tunables as TU

HOME = pathlib.Path("/home/dev")
STOP = HOME / "STOP"
STATE = HOME / "perpetual_state.json"
# On-node, on loopback. These were expose_port URLs from a previous container;
# those die with the node that minted them and are never resurrected, which is
# why triage silently no-opped for this whole session. Local daemons cannot
# expire out from under the loop.
OFFICER = "http://127.0.0.1:8092"        # triaged.py -- stage 1, defect triage
SELF_FEED = "http://127.0.0.1:8090"      # gallery.py -- serves /img/<gen>/<key>

VARIANTS = 4

# Operator direction, pushed in live by direction.py (None = the loop decides).
_FREEZE = None              # a pinned subject: vary FROM this card, never re-roll
_PIN_VECTOR = None          # force this Kontext vector next
_PIN_SLOT = None            # force this concept slot for the next fresh seed
_KONTEXT_STEPS = 28         # edit-pass strength
_KONTEXT_GUIDANCE = 2.5
_NEGATIVE = ""
DIVERSIFY_EVERY = 3          # 2 mutation batches -> 1 fresh seed, per the order

# The mutation vectors. Each is a Kontext instruction that changes ONE thing and
# preserves the rest -- the point of using Kontext rather than a fresh roll.
MUTATIONS = [
    ("contrast",   "Increase the tonal contrast: deepen the shadows and brighten the "
                   "highlights. Keep the character, pose, costume and composition identical."),
    ("golden",     "Shift the lighting to warm golden-hour light raking from one side. "
                   "Keep the character, pose, costume and composition identical."),
    ("simplify",   "Simplify the background and border: remove clutter, leave clean flat "
                   "paper. Keep the character, pose and costume identical."),
    ("intimacy",   "Move the framing slightly closer on the character's face and hands. "
                   "Keep the character, costume, palette and background identical."),
    ("ornament",   "Enrich the decorative border with finer botanical detail. Keep the "
                   "character, pose, costume and background identical."),
    ("palette",    "Cool the palette toward blue-grey shadows while keeping the accent "
                   "colour. Keep the character, pose, costume and composition identical."),
    ("linework",   "Strengthen the linework: bolder confident outlines, cleaner edges. "
                   "Keep the character, pose, costume and composition identical."),
]


def read(p, default):
    if p.is_file():
        try:
            return json.loads(p.read_text())
        except json.JSONDecodeError:
            pass
    return default


def state():
    return read(STATE, {"cycle": 0, "champion": None, "mutation_stats": {},
                        "slot_activity": {}, "history": []})


def save_state(s):
    STATE.write_text(json.dumps(s, indent=2))


def consult_officer(gen, keys, batch):
    images = [{"key": k, "url": f"{SELF_FEED}/img/{gen}/{k}"} for k in keys]
    body = json.dumps({"images": images, "keep": VARIANTS, "batch": batch}).encode()
    req = urllib.request.Request(f"{OFFICER}/triage", data=body,
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": "koyomi-perpetual/1"})
    with urllib.request.urlopen(req, timeout=1800) as r:
        return json.load(r)


def pick_mutation(s):
    """Prefer mutation vectors that have historically raised fitness.

    This is the gradient: the loop learns which KINDS of change move toward the
    human's taste, not just which images did.
    """
    if _PIN_VECTOR and _PIN_VECTOR in dict(MUTATIONS):
        return _PIN_VECTOR                      # operator overrides the gradient
    stats = s.get("mutation_stats", {})
    best, best_score = None, None
    for name, _ in MUTATIONS:
        st = stats.get(name)
        if not st or not st.get("n"):
            return name  # anything untried gets a turn first
        avg = st["gain"] / st["n"]
        if best_score is None or avg > best_score:
            best, best_score = name, avg
    return best


def mutation_text(name):
    return dict(MUTATIONS)[name]


def kontext_variants(src_art, instruction, n, seed_base, stem):
    """n Kontext edits of the SAME source with the same instruction, different seeds."""
    outs = []
    for i in range(n):
        body = json.dumps({"image": str(src_art), "instruction": instruction,
                           "seed": seed_base + i * 131, "stem": f"{stem}-{i}",
                           "steps": _KONTEXT_STEPS,
                           "guidance": _KONTEXT_GUIDANCE}).encode()
        req = urllib.request.Request(f"{C.DAEMON}/edit", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=1800) as r:
            outs.append(json.load(r))
    return outs


def fresh_variants(concept, steps, w, h, seed):
    spec = K.as_spec([concept])[0]
    items = [{"prompt": C.prompt_for(spec), "seed": seed + i * 101,
              "stem": f"fresh-{spec['key']}-{i}"} for i in range(VARIANTS)]
    body = json.dumps({"items": items, "steps": steps, "width": w, "height": h}).encode()
    req = urllib.request.Request(f"{C.DAEMON}/batch", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=1800) as r:
        return json.load(r)["images"], spec


def least_active(pop, s):
    """The Anti-Slop Valve picks the concept the loop has been ignoring."""
    activity = s.get("slot_activity", {})
    living = K.alive(pop)
    if _PIN_SLOT:
        pinned = [c for c in living if c["slot"] == _PIN_SLOT]
        if pinned:
            return pinned[0]                    # operator picks the subject
    return min(living, key=lambda c: activity.get(c["slot"], 0))


def cycle(a, s, store):
    s["cycle"] += 1
    n = s["cycle"]
    diversify = (n % DIVERSIFY_EVERY == 0) or not s.get("champion")

    pop = K.load()
    C.W, C.H = a.width, a.height
    mutation = None

    # A frozen subject overrides both the diversify schedule and the champion:
    # the operator, not the fitness function, decides what is being developed.
    if _FREEZE:
        diversify = False
        s["champion"] = dict(_FREEZE)

    if diversify:
        concept = least_active(pop, s)
        spec = K.as_spec([concept])[0]
        mode = f"fresh seed on '{spec['key']}' (anti-slop valve)"
        images, spec = fresh_variants(concept, a.steps, a.width, a.height,
                                      a.seed + n * 7717)
        run = C.new_run(f"perpetual {n}: {mode}", a.steps, VARIANTS)
        keys = []
        for i, img in enumerate(images):
            card = dict(spec); card["key"] = f"{spec['key']}-c{n}v{i}"
            (C.ART_DIR / f"{card['key']}.png").write_bytes(
                pathlib.Path(img["path"]).read_bytes())
            C.publish(run, card, C.compose(card), img)
            keys.append(card["key"])
        s["slot_activity"][concept["slot"]] = s["slot_activity"].get(concept["slot"], 0) + 1
    else:
        champ = s["champion"]
        mutation = pick_mutation(s)
        mode = f"kontext mutation '{mutation}' on {champ['key']}"
        src_art = pathlib.Path(champ["art"])
        if not src_art.is_file():
            src_art = pathlib.Path(champ["card"])
        edits = kontext_variants(src_art, mutation_text(mutation), VARIANTS,
                                 a.seed + n * 313, f"mut-{n}")
        spec = champ["spec"]
        run = C.new_run(f"perpetual {n}: {mode}", a.steps, VARIANTS)
        keys = []
        for i, e in enumerate(edits):
            card = dict(spec); card["key"] = f"{spec['key']}-c{n}m{i}"
            (C.ART_DIR / f"{card['key']}.png").write_bytes(
                pathlib.Path(e["path"]).read_bytes())
            C.W, C.H = a.width, a.height
            C.publish(run, card, C.compose(card), e)
            keys.append(card["key"])
        s["slot_activity"][spec["key"].split("-")[0]] = \
            s["slot_activity"].get(spec["key"].split("-")[0], 0) + 1

    gen = run.name
    print(f"[perpetual] cycle {n}: {mode} -> {gen}", flush=True)

    # 1. Triage: defects deleted, never rated.
    try:
        tri = consult_officer(gen, keys, gen)
        blocked = set(tri["blocked"])
    except Exception as e:
        print(f"[perpetual] officer unreachable ({e!r}) — nothing blocked this cycle",
              flush=True)
        tri, blocked = None, set()

    # 2. Score the survivors against the Ghost of Taste.
    survivors = [k for k in keys if k not in blocked]
    paths = [run / "cards" / f"{k}.png" for k in survivors]
    scored = T.score(store, paths) if survivors else []
    for k, sc in zip(survivors, scored):
        sc["key"] = k
    live = [sc for sc in scored if not sc.get("purged")]
    purged = [sc["key"] for sc in scored if sc.get("purged")]

    # 3. Select the Local Champion.
    prev_fit = (s.get("champion") or {}).get("fitness")
    if live and not live[0].get("cold_start"):
        best = max(live, key=lambda x: x["fitness"])
        s["champion"] = {
            "key": best["key"], "gen": gen, "fitness": best["fitness"],
            "card": str(run / "cards" / f"{best['key']}.png"),
            "art": str(run / "art" / f"{best['key']}.png"),
            "spec": next(sp for sp in [spec]), "at": time.time(),
        }
        # 4. Credit the mutation vector with the gain it produced.
        if mutation is not None and prev_fit is not None:
            st = s["mutation_stats"].setdefault(mutation, {"n": 0, "gain": 0.0})
            st["n"] += 1
            st["gain"] += best["fitness"] - prev_fit
        print(f"[perpetual] champion {best['key']} fitness {best['fitness']:.4f}"
              + (f" (was {prev_fit:.4f})" if prev_fit is not None else ""), flush=True)
    elif live:
        # Cold start: no anchors yet, so seed provisionally from the cleanest card.
        k = live[0]["key"]
        s["champion"] = {"key": k, "gen": gen, "fitness": None,
                         "card": str(run / "cards" / f"{k}.png"),
                         "art": str(run / "art" / f"{k}.png"),
                         "spec": spec, "at": time.time()}
        print(f"[perpetual] cold start — provisional champion {k}", flush=True)
    else:
        print("[perpetual] every variant blocked or purged; champion unchanged", flush=True)

    if purged:
        print(f"[perpetual] purged near a known failure: {', '.join(purged)}", flush=True)

    manifest = json.loads((run / "run.json").read_text())
    manifest["perpetual"] = {
        "cycle": n, "mode": mode, "mutation": mutation,
        "triage": tri, "scores": scored,
        "champion": (s.get("champion") or {}).get("key"),
        "anchors": len(store["anchors"]), "repulsors": len(store["repulsors"]),
    }
    (run / "run.json").write_text(json.dumps(manifest, indent=2))

    s["history"] = (s.get("history", []) + [{
        "cycle": n, "gen": gen, "mode": mode,
        "champion": (s.get("champion") or {}).get("key"),
        "fitness": (s.get("champion") or {}).get("fitness"),
    }])[-200:]
    return gen


def main():
    ap = argparse.ArgumentParser(description="The Perpetual Sieve")
    ap.add_argument("--interval", type=float, default=0,
                    help="seconds between cycles; 0 = follow the Governor's gear")
    ap.add_argument("--steps", type=int, default=32)
    ap.add_argument("--width", type=int, default=448)
    ap.add_argument("--height", type=int, default=672)
    ap.add_argument("--seed", type=int, default=8801)
    ap.add_argument("--max-cycles", type=int, default=100000)
    ap.add_argument("--consult-every", type=int, default=3,
                    help="consult the Governor every N cycles")
    a = ap.parse_args()

    if STOP.exists():
        STOP.unlink()

    s = state()
    store = T.load()

    # Adopt operator changes the instant they are published, not at the next tick.
    _me = sys.modules[__name__]
    TU.install(a, _me, lambda: s["cycle"],
               extra=lambda c, f=False: DIR.apply(c, force=f, mod=_me))
    print(f"[perpetual] armed: {len(store['anchors'])} anchor(s), "
          f"{len(store['repulsors'])} repulsor(s); diversify every {DIVERSIFY_EVERY}",
          flush=True)

    fails = 0
    while s["cycle"] < a.max_cycles and not STOP.exists():
        try:
            # 0. Live tunables, before anything else -- including the STALL check,
            #    so a breathing worker still adopts what the operator turned.
            _dir = DIR.apply(s["cycle"], mod=sys.modules[__name__])
            if _dir:
                print(f"[direction] adopted rev {_dir['rev']} "
                      f"({', '.join(_dir['changed']) or 'no field moved'})", flush=True)
            _tun = TU.apply(a, sys.modules[__name__], s["cycle"])
            if _tun:
                _delta = ", ".join(f"{k} {v[0]}->{v[1]}"
                                   for k, v in _tun["changed"].items()) or "no field moved"
                print(f"[tunables] adopted rev {_tun['rev']} ({_delta}) "
                      f"{_tun['latency_s']}s after publish", flush=True)

            # 1. His throttle, checked before anything else.
            d = E.poll_directive()
            if d and d.get("verb"):
                print(f"[estate] directive {d['verb']}: {d['note']}", flush=True)

            # 2. Node-level actions whose veto window has expired.
            for due in E.due_cosigns():
                print(f"[estate] co-sign window elapsed: {due['verb']} {due['args']} "
                      f"— requires off-node execution", flush=True)

            # 3. A stalled loop keeps breathing but does not generate.
            if E.stalled():
                print("[perpetual] STALLED — awaiting IGNITE", flush=True)
                time.sleep(min(30, a.interval))
                continue

            # 4. The Correction Wave: fold in whatever the human said while we worked.
            added = T.harvest(store)
            if added["keep"] or added["retire"]:
                T.save(store)
                print(f"[perpetual] correction wave: +{added['keep']} anchor(s), "
                      f"+{added['retire']} repulsor(s) — landscape moved", flush=True)

            cycle(a, s, store)
            save_state(s)
            fails = 0

            # 5. Consult him. His answer steers the next cycle; his silence does not
            #    stop this one.
            if s["cycle"] % a.consult_every == 0:
                packet = P.state_packet(s, store)
                packet["telemetry"] = E.telemetry(s, store)
                P.audit("packet", {"packet": packet})
                directive, raw, err = P.consult(packet)
                if err:
                    msg = E.note_unreachable(True)
                    print(f"[governor] unreachable: {err}"
                          + (f" — {msg}" if msg else ""), flush=True)
                    P.audit("unreachable", {"error": err})
                elif directive is None:
                    E.note_unreachable(False)
                    print(f"[governor] reply had no valid directive", flush=True)
                    P.audit("unparseable", {"raw": (raw or "")[:2000]})
                else:
                    E.note_unreachable(False)
                    if directive["verb"] in E.ESTATE_VERBS:
                        applied, note = E.execute(directive["verb"],
                                                  directive.get("args") or {})
                        P.audit("estate_directive",
                                {"directive": directive, "applied": applied, "note": note})
                    else:
                        applied, note = P.apply_directive(directive)
                    print(f"[governor] {directive['verb']}: {note} — "
                          f"{directive.get('rationale', '')[:140]}", flush=True)

        except Exception as e:
            fails += 1
            print(f"[perpetual] cycle failed ({fails}/5): {e!r}", flush=True)
            if fails >= 5:
                print("[perpetual] five consecutive failures — stopping", flush=True)
                break
        # Tempo is his to set: SHIFT_GEAR moves this.
        time.sleep(E.interval() if a.interval <= 0 else a.interval)

    print(f"[perpetual] stopped after {s['cycle']} cycles", flush=True)


if __name__ == "__main__":
    main()
