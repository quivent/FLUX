#!/home/dev/venv/bin/python
"""Seed-locked A/B: make a change's effect attributable instead of guessed.

The worst thing I did today was change four things at once -- resolution, step
count, prompt set, treatment mix -- and then wonder why the output got worse. It
took reading the prompt file to find the cause, because there was no experiment,
only drift. The governor's discipline, which I had been violating in every one of
its clauses:

    single-variable mutation   one parameter moves per run
    seed locking               same seeds both sides, so RNG luck cancels
    versioned metadata         every image carries the exact config that made it

This runs a real comparison. Two configs, THE SAME FIXED SEED SET, same prompts,
one variable different. Because the seeds are shared, each image in A has a
partner in B that differs only by the thing under test, so the comparison is
paired -- and a paired test needs far fewer samples than comparing two clouds of
independent rolls.

It reports the per-pair delta on the watcher's metrics AND publishes both arms to
the wall tagged `bench:<name>`, so the same pairs can be settled by eye in the
duel lane. The automatic number is a hypothesis; the operator's choice is the
result.

    bench.py craft_clause 8
"""
import hashlib
import json
import pathlib
import sys
import time
import urllib.request

sys.path.insert(0, "/home/dev")
import collection as C

D = "http://127.0.0.1:8080"
OUT = pathlib.Path("/home/dev/bench_results.jsonl")

# Seeds are fixed forever. Reusing the same set across every experiment means
# results from different days remain comparable.
SEEDS = [4111, 4222, 4333, 4444, 4555, 4666, 4777, 4888, 4999, 5111, 5222, 5333]

SUBJECTS = [
    "A lighthouse on a headland at dusk, the beam just lit.",
    "An empty tram stopped at a platform in heavy rain at night.",
    "A single tree on a ridge above a valley filled with cloud.",
    "A long corridor of a shuttered department store, one light on.",
]

# The variable under test. Each arm is a craft clause appended to the subject --
# nothing else differs.
ARMS = {
    "adjectives": (
        "Masterful, luminous, immaculate composition, highly detailed, "
        "breathtaking, trending on artstation, 8k."),
    "physical": (
        "Single dominant light source with a stated direction. Air with weight: "
        "haze thickening with distance so far things go pale. Three depth planes, "
        "one of them empty. Colour held to a narrow band with one departure. "
        "Horizon off centre."),
}


def render(items, steps, w, h):
    body = json.dumps({"items": items, "steps": steps, "width": w, "height": h}).encode()
    req = urllib.request.Request(D + "/batch", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=1800) as r:
        return json.load(r)["images"]


def config_hash(arm, steps, w, h):
    """Versioned metadata: every image carries the exact config that made it."""
    blob = json.dumps({"arm": arm, "clause": ARMS[arm], "steps": steps,
                       "w": w, "h": h}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "craft_clause"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    steps, w, h = 28, 704, 1056
    pairs = [(SEEDS[i % len(SEEDS)], SUBJECTS[i % len(SUBJECTS)]) for i in range(n)]

    print(f"[bench] {name}: {len(ARMS)} arms x {n} seed-locked pairs, "
          f"{w}x{h} steps {steps}", flush=True)
    for arm in ARMS:
        print(f"        {arm:<12} {config_hash(arm, steps, w, h)}", flush=True)

    made = {}
    for arm, clause in ARMS.items():
        h_ = config_hash(arm, steps, w, h)
        run = C.new_run(f"bench:{name} arm={arm} cfg={h_}", steps, len(pairs))
        (run / "cards").mkdir(parents=True, exist_ok=True)
        items, keys = [], []
        for i, (seed, subj) in enumerate(pairs):
            key = f"bench-{name}-{arm}-s{seed}"
            items.append({"prompt": f"{subj} {clause} No text, no watermark.",
                          "seed": seed, "stem": key})
            keys.append(key)
        t0 = time.time()
        imgs = render(items, steps, w, h)
        dt = time.time() - t0

        man = json.loads((run / "run.json").read_text())
        man.setdefault("cards", [])
        for key, img, (seed, subj) in zip(keys, imgs, pairs):
            src = pathlib.Path(img["path"]).read_bytes()
            (C.ART_DIR / f"{key}.png").write_bytes(src)
            (run / "cards" / f"{key}.png").write_bytes(src)
            man["cards"].append({"key": key, "product": arm.upper(),
                                 "subtitle": f"seed {seed}", "quote": subj,
                                 "character": "", "role": "", "family": "bench",
                                 "seed": seed, "config": h_, "arm": arm,
                                 "experiment": name})
        (run / "run.json").write_text(json.dumps(man, indent=2))
        made[arm] = {"gen": run.name, "keys": keys, "cfg": h_,
                     "per_image_s": round(dt / len(imgs), 3)}
        print(f"[bench] {arm}: {run.name}, {dt/len(imgs):.2f}s/img", flush=True)

    # score both arms with the watcher's own metrics so the numbers are comparable
    import watch
    rows = {}
    for arm, info in made.items():
        paths = [pathlib.Path("/home/dev/runs") / info["gen"] / "cards" / f"{k}.png"
                 for k in info["keys"]]
        rows[arm] = watch.score(paths)

    a, b = list(ARMS)
    deltas = []
    print(f"\n[bench] paired deltas ({b} minus {a}), same seed both sides:")
    print(f"  {'seed':>6}  {'novelty':>18}  {'craft':>16}")
    for i, (seed, _) in enumerate(pairs):
        dn = rows[b][i]["novelty"] - rows[a][i]["novelty"]
        dc = rows[b][i]["craft"] - rows[a][i]["craft"]
        deltas.append((dn, dc))
        print(f"  {seed:>6}  {rows[a][i]['novelty']:.4f}->{rows[b][i]['novelty']:.4f} "
              f"{dn:+.4f}  {dc:+.4f}")

    mn = sum(d[0] for d in deltas) / len(deltas)
    mc = sum(d[1] for d in deltas) / len(deltas)
    wins = sum(1 for d in deltas if d[0] > 0)
    print(f"\n[bench] mean novelty delta {mn:+.4f}   mean craft delta {mc:+.4f}")
    print(f"[bench] {b} more novel than {a} on {wins}/{len(deltas)} paired seeds")
    print(f"[bench] both arms are on the wall as bench:{name} — settle it by eye "
          f"in the duel lane; the metric is a hypothesis, not the verdict")

    with OUT.open("a") as f:
        f.write(json.dumps({"at": time.time(), "experiment": name, "n": len(pairs),
                            "arms": made, "mean_novelty_delta": round(mn, 4),
                            "mean_craft_delta": round(mc, 4),
                            "novelty_wins": wins, "seeds": [s for s, _ in pairs]}) + "\n")


if __name__ == "__main__":
    main()
