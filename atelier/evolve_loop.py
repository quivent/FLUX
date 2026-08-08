#!/home/dev/venv/bin/python
"""The wave loop: renders that SELECT and BREED, so quality compounds instead of
plateauing.

WHAT WAS WRONG
blast2.py renders 14 treatments x 16 concepts round-robin, forever. Every card
is an independent one-shot: nothing is measured, nothing is kept, nothing is
built on. Card 1000 is drawn from exactly the same distribution as card 1, so
the wall gets longer and never gets better. Two loops are missing, and they are
different loops:

  SELECTION   spend the next render where the last renders paid off. A treatment
              bandit over the 14 whole-style treatments: explore all of them
              early, concentrate on the ones that actually score once there is
              evidence. This makes the AVERAGE card better.
  BREEDING    take the best card so far and change one thing about it with
              Kontext /edit, keep the child only if it scores higher, and learn
              WHICH kind of change tends to help. This makes the BEST card
              better, and the instruction bandit is where the compounding lives:
              generation 3 of a lineage starts from generation 2's winner, and
              the edit applied to it is the edit that has been winning.

Selection without breeding just picks a better lottery ticket. Breeding without
selection improves one arbitrary card. Both, with a fitness that is about the
picture rather than about resembling a previous picture (aesthetic.py, not
taste.py), is what makes a curve instead of a line.

STRUCTURE OF A WAVE
  0. drain evolve_queue -- the operator's manual evolves outrank the loop, so
     they are served before anything the bandit wants.
  1. treatment bandit picks BATCH slots (UCB1, with a provisional update between
     picks so one treatment cannot take the whole wave in one go)
  2. one /batch render
  3. publish every card, score every card
  4. top K of the wave, merged into an all-time elite set
  5. breed: /edit each of a few elites with a bandit-chosen instruction, score
     the child, promote it if it beat its parent, and record the win or loss
     against that instruction
  6. append the wave to evolve_waves.jsonl and rewrite evolve_progress.json

WHY UCB1 AND NOT EPSILON-GREEDY
Fourteen arms and a wave of twelve pulls: an epsilon-greedy loop spends its
exploration budget uniformly forever, and with this few pulls per wave that is
most of the budget. UCB1's bonus decays as sqrt(log N / n), so a treatment that
has been sampled and found mediocre stops being re-sampled, while one that is
merely UNDER-sampled keeps its turn. Untried arms are taken first by
construction (their bonus is infinite), which is exactly the "cover all 14 in
the early waves" behaviour the wall needs.

WHY THE PUBLISH LOCK
C.publish read-modify-writes run.json. Two threads publishing into the same
generation lose cards or corrupt the manifest -- this has already happened on
this node. Every publish in this file, and every publish evolve_queue does on
our behalf, goes through the one PUBLISH lock.

STATE
  evolve_stats.json     bandit posteriors + elite set; survives a restart, so a
                        relaunched loop resumes its beliefs rather than
                        re-exploring from scratch
  evolve_waves.jsonl    one line per wave, append-only
  evolve_progress.json  the flat snapshot a UI polls

    ./evolve_loop.py --deadline 3600 --batch 12 --steps 28
"""
import argparse
import json
import math
import os
import pathlib
import random
import sys
import threading
import time
import traceback
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "/home/dev")
import aesthetic as A
import collection as C
import concepts as K
import treatments as T

DAEMON = "http://127.0.0.1:8080"
HOME = pathlib.Path("/home/dev")
STATS = HOME / "evolve_stats.json"
WAVES = HOME / "evolve_waves.jsonl"
PROGRESS = HOME / "evolve_progress.json"
VARLOG = HOME / "variations.jsonl"

W, H = 448, 672
ELITE_MAX = 12          # all-time keep; wide enough to hold several lineages
# UCB1's textbook c=1 assumes a reward that USES the whole [0,1] interval. Ours
# does not: measured over four live waves the treatment means span 0.462-0.625,
# a spread of 0.163, while c=1.2 puts a bonus of 1.67 on an arm with n=4. A
# bonus ten times the spread is not exploration, it is a uniform random choice
# wearing a bandit's name -- which is exactly what waves 1-4 did (mean 0.504,
# 0.470, 0.479, 0.462; flat). Scale the constant to the reward instead: c chosen
# so the n=1 bonus is roughly the observed spread, so an arm is re-tried while it
# is genuinely under-sampled and dropped once the evidence separates it.
UCB_C_TREAT = 0.10      # treatment scores span ~0.16
UCB_C_INSTR = 0.08      # per-edit gains span ~0.21 (-0.135 .. +0.074)
BREED_FROM_WAVE = 3     # top K of this wave get bred
BREED_FROM_ELITE = 2    # ...plus the best of all time, so lineages go deep

PUBLISH = threading.Lock()
LOGLOCK = threading.Lock()

# -------------------------------------------------------------- instructions
# The bandit's arms. Seven are perpetual.py's MUTATIONS, imported live below so
# the two loops cannot drift apart; the rest fill the vectors that pool lacks.
# Every instruction ends by pinning what must NOT change -- an unconstrained
# Kontext edit redraws the character and the lineage stops being a lineage.
AUTHORED = [
    ("clear_panel", "Clear the lower third of the image: make the bottom third an "
                    "empty flat panel of plain background with no detail at all. "
                    "Keep the character, pose, costume, palette and the upper two "
                    "thirds identical."),
    ("negative_space", "Open up more empty negative space around the figure, remove "
                       "crowding at the edges. Keep the character, pose, costume and "
                       "palette identical."),
    ("crop_tight", "Crop tighter on the figure so it fills more of the upper frame. "
                   "Keep the character, costume, palette and style identical."),
    ("cool_light", "Shift the light cooler: blue-grey shadows and a pale cool key "
                   "light. Keep the character, pose, costume and composition identical."),
    ("flatten", "Flatten the rendering into bolder simplified shapes with fewer "
                "gradients. Keep the character, pose, costume and composition identical."),
]


def instruction_pool():
    """Authored vectors + perpetual.py's seven, deduped by name.

    perpetual is imported defensively: it is another agent's file and it is
    being edited right now, so a syntax error there must cost this loop five
    arms, not its life.
    """
    pool = dict(AUTHORED)
    try:
        import perpetual
        for name, text in getattr(perpetual, "MUTATIONS", []):
            pool.setdefault(name, text)
    except Exception as e:
        log(f"[evolve] perpetual.MUTATIONS unavailable ({e!r}); authored pool only")
    return pool


def log(msg):
    with LOGLOCK:
        print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


# --------------------------------------------------------------------- state


def read_json(path, default):
    try:
        return json.loads(pathlib.Path(path).read_text())
    except Exception:
        return default


def write_json(path, obj):
    """Atomic: a reader (the UI) never sees a half-written state file."""
    p = pathlib.Path(path)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1))
    os.replace(tmp, p)


def blank_state():
    return {"wave": 0, "made": 0, "treatments": {}, "instructions": {},
            "elite": [], "recent": []}


def arm(stats, bucket, name):
    return stats[bucket].setdefault(name, {"n": 0, "sum": 0.0, "best": 0.0,
                                           "wins": 0, "losses": 0, "gain": 0.0})


def mean_of(a):
    return (a["sum"] / a["n"]) if a["n"] else 0.0


def mean_gain(a):
    """What an INSTRUCTION is worth: how much it moved a picture, not how good
    the picture already was.

    Ranking instructions by the absolute score of their children would mostly
    rank the parents they happened to be handed -- an edit applied to the 0.83
    elite looks brilliant and one applied to a 0.55 card looks useless, whatever
    either edit actually did. The gain over the exact parent is the only part of
    that number the instruction is responsible for.
    """
    return (a["gain"] / a["n"]) if a["n"] else 0.0


def ucb(a, total, c, value=mean_of):
    if a["n"] == 0:
        return float("inf")
    return value(a) + c * math.sqrt(2.0 * math.log(max(2, total)) / a["n"])


# -------------------------------------------------------------------- render


def post(path, body, timeout=1800):
    req = urllib.request.Request(DAEMON + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def pick_treatments(stats, n, names):
    """n slots by UCB1, with a provisional update after each pick.

    Without the provisional update a batch of twelve would be twelve copies of
    the current argmax -- a bandit designed for one pull at a time, run twelve
    times against unchanged statistics. Crediting each pick with that arm's own
    current mean (an optimistic no-news observation) decays its bonus inside the
    wave, so the batch spreads across the top few arms in proportion to how far
    ahead they actually are, and the untried arms still go first.
    """
    shadow = {k: dict(arm(stats, "treatments", k)) for k in names}
    total = sum(v["n"] for v in shadow.values())
    picks = []
    for _ in range(n):
        best = max(names, key=lambda k: (ucb(shadow[k], total, UCB_C_TREAT),
                                         random.random()))
        picks.append(best)
        m = mean_of(shadow[best])
        shadow[best]["n"] += 1
        shadow[best]["sum"] += m
        total += 1
    return picks


def publish_card(run, card, art_src, meta, tag):
    """Art onto disk, plate composed, manifest appended -- serialised."""
    (C.ART_DIR / f"{card['key']}.png").write_bytes(pathlib.Path(art_src).read_bytes())
    plate = C.compose(card)
    with PUBLISH:
        C.publish(run, card, plate, meta)
    with LOGLOCK:
        with open(VARLOG, "a") as f:
            f.write(json.dumps({"key": card["key"], "at": time.time(),
                                "axes": tag, "mode": "evolve"}) + "\n")
    return C.ART_DIR / f"{card['key']}.png"


# ---------------------------------------------------------------------- wave


def run_wave(stats, args, specs, pool, wave_no):
    names = [n for n in T.NAMES if n in T.TREATMENTS]
    picks = pick_treatments(stats, args.batch, names)
    C.W, C.H = W, H

    made = stats["made"]
    run = C.new_run(f"evolve w{wave_no}: {', '.join(sorted(set(picks))[:4])}",
                    args.steps, args.batch)

    items, cards = [], []
    for j, treat in enumerate(picks):
        spec = specs[(made + j) % len(specs)]
        card = dict(spec)
        card["key"] = f"{spec['key']}-{treat}-{made + j}"
        prompt = T.style_for(treat, spec)
        items.append({"prompt": prompt, "seed": 700000 + made + j, "stem": card["key"]})
        cards.append({"card": card, "treat": treat, "prompt": prompt,
                      "concept": spec["key"], "spec": spec})

    t0 = time.time()
    imgs = post("/batch", {"items": items, "steps": args.steps,
                           "width": W, "height": H})["images"]
    render_s = time.time() - t0

    pool_ex = ThreadPoolExecutor(max_workers=6)
    futs = []
    for c, img in zip(cards, imgs):
        c["raw"] = img["path"]
        c["meta"] = img
        futs.append(pool_ex.submit(publish_card, run, c["card"], img["path"], img,
                                   {"treatment": c["treat"], "wave": wave_no}))
    for f, c in zip(futs, cards):
        try:
            c["art"] = str(f.result())
        except Exception as e:
            log(f"[evolve] publish failed {c['card']['key']}: {e!r}")
            c["art"] = c["raw"]
    pool_ex.shutdown(wait=True)

    rows = A.score([c["art"] for c in cards], [c["prompt"] for c in cards])
    for c, r in zip(cards, rows):
        c["score"] = r["score"]
        c["parts"] = r
        a = arm(stats, "treatments", c["treat"])
        a["n"] += 1
        a["sum"] += r["score"]
        a["best"] = max(a["best"], r["score"])
    stats["made"] += len(cards)

    cards.sort(key=lambda c: -c["score"])
    return run, cards, render_s


def entry(c, gen=0, parent=None, instruction=None):
    return {"key": c["card"]["key"], "gen": gen, "score": round(c["score"], 4),
            "art": c["art"], "prompt": c["prompt"], "treatment": c["treat"],
            "concept": c["concept"], "spec": c["spec"], "parent": parent,
            "instruction": instruction, "at": time.time()}


def breed(stats, args, run, parents, pool, wave_no):
    """One Kontext edit per parent, scored against the parent it came from.

    The comparison is what makes this a gradient rather than a random walk: the
    child is kept only if it beat the exact image it was made from, and the
    instruction that produced it is credited or debited for that outcome. The
    child's adherence is measured against the PARENT'S prompt, because a Kontext
    edit is supposed to preserve the subject -- an edit that drifts off the
    brief should lose adherence, and it does.
    """
    names = list(pool)
    # Same provisional-update trick as the treatment bandit: without it the five
    # parents in a wave would all be handed the current argmax, which is one
    # bandit pull priced as five.
    shadow = {k: dict(arm(stats, "instructions", k)) for k in names}
    total = sum(v["n"] for v in shadow.values())
    results = []

    for p in parents:
        instr_name = max(names, key=lambda k: (ucb(shadow[k], total, UCB_C_INSTR,
                                                   value=mean_gain),
                                               random.random()))
        g = mean_gain(shadow[instr_name])
        shadow[instr_name]["n"] += 1
        shadow[instr_name]["gain"] += g
        total += 1
        text = pool[instr_name]
        gen = int(p.get("gen", 0)) + 1
        key = f"{p['key']}~x~{instr_name}~g{gen}"
        try:
            src = p.get("art") or str(C.ART_DIR / f"{p['key']}.png")
            if not pathlib.Path(src).is_file():
                log(f"[evolve] breed skip {p['key']}: art missing")
                continue
            t0 = time.time()
            out = post("/edit", {"image": src, "instruction": text,
                                 "steps": args.steps, "stem": key}, timeout=900)
            child = dict(p["spec"])
            child["key"] = key
            art = publish_card(run, child, out["path"], out,
                               {"instruction": instr_name, "parent": p["key"],
                                "gen": gen, "wave": wave_no})
            row = A.score([str(art)], [p["prompt"]])[0]
            gain = row["score"] - p["score"]
            a = arm(stats, "instructions", instr_name)
            a["n"] += 1
            a["sum"] += row["score"]
            a["gain"] += gain
            a["best"] = max(a["best"], row["score"])
            if gain > 0:
                a["wins"] += 1
            else:
                a["losses"] += 1
            results.append({"parent": p["key"], "child": key, "instruction": instr_name,
                            "gen": gen, "parent_score": round(p["score"], 4),
                            "child_score": round(row["score"], 4),
                            "gain": round(gain, 4), "win": gain > 0,
                            "seconds": round(time.time() - t0, 1),
                            "_entry": {"key": key, "gen": gen,
                                       "score": round(row["score"], 4),
                                       "art": str(art), "prompt": p["prompt"],
                                       "treatment": p["treatment"],
                                       "concept": p["concept"], "spec": p["spec"],
                                       "parent": p["key"], "instruction": instr_name,
                                       "at": time.time()}})
            log(f"[evolve]   breed {instr_name:<14} {p['key'][:38]:<38} "
                f"{p['score']:.3f} -> {row['score']:.3f} "
                f"{'WIN ' if gain > 0 else 'loss'} ({gain:+.3f})")
        except Exception as e:
            log(f"[evolve] breed failed {p['key']} / {instr_name}: {e!r}")
    return results


def merge_elite(stats, candidates):
    """All-time top ELITE_MAX by score, one entry per key."""
    seen = {e["key"]: e for e in stats["elite"]}
    for c in candidates:
        old = seen.get(c["key"])
        if old is None or c["score"] > old["score"]:
            seen[c["key"]] = c
    elite = sorted(seen.values(), key=lambda e: -e["score"])[:ELITE_MAX]
    stats["elite"] = elite
    return elite


def snapshot(stats, wave_no, best, recent_mean, pool):
    tt = sorted(((n, a) for n, a in stats["treatments"].items() if a["n"]),
                key=lambda kv: -mean_of(kv[1]))
    ti = sorted(((n, a) for n, a in stats["instructions"].items() if a["n"]),
                key=lambda kv: -(kv[1]["gain"] / max(1, kv[1]["n"])))
    write_json(PROGRESS, {
        "wave": wave_no,
        "made": stats["made"],
        "best_score": round(best, 4),
        "mean_score_recent": round(recent_mean, 4),
        "elite": [{"key": e["key"], "gen": e["gen"], "score": e["score"]}
                  for e in stats["elite"]],
        "top_treatments": [{"name": n, "n": a["n"], "mean": round(mean_of(a), 4),
                            "best": round(a["best"], 4)} for n, a in tt],
        "top_instructions": [{"name": n, "n": a["n"], "wins": a["wins"],
                              "losses": a["losses"],
                              "mean_gain": round(a["gain"] / max(1, a["n"]), 4)}
                             for n, a in ti],
        "instruction_pool": len(pool),
        "updated": time.time(),
    })


# --------------------------------------------------------------------- main


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--deadline", type=float, default=3600.0)
    ap.add_argument("--batch", type=int, default=12)
    ap.add_argument("--steps", type=int, default=28)
    ap.add_argument("--waves", type=int, default=0, help="0 = until the deadline")
    args = ap.parse_args()

    stats = read_json(STATS, blank_state())
    for k, v in blank_state().items():
        stats.setdefault(k, v)
    pool = instruction_pool()

    log(f"[evolve] start: batch {args.batch}, steps {args.steps}, {W}x{H}, "
        f"deadline {args.deadline:.0f}s, {len(T.NAMES)} treatments, "
        f"{len(pool)} instructions, resuming at wave {stats['wave']} "
        f"({stats['made']} cards, {len(stats['elite'])} elite)")

    t0 = time.time()
    backoff = 0
    while time.time() - t0 < args.deadline:
        wave_no = stats["wave"] + 1
        wt = time.time()
        try:
            # 0. the operator outranks the loop.
            try:
                import evolve_queue
                n = evolve_queue.serve_pending(publish_lock=PUBLISH, log=log,
                                               worker=f"evolve_loop:{os.getpid()}")
                if n:
                    log(f"[evolve] served {n} operator request(s) before wave {wave_no}")
            except ImportError:
                pass
            except Exception as e:
                log(f"[evolve] queue drain failed: {e!r}")

            specs = K.as_spec(K.alive(K.load()))
            run, cards, render_s = run_wave(stats, args, specs, pool, wave_no)

            scores = [c["score"] for c in cards]
            wave_mean = sum(scores) / max(1, len(scores))
            wave_best = max(scores) if scores else 0.0

            wave_elite = [entry(c) for c in cards[:BREED_FROM_WAVE]]
            elite = merge_elite(stats, wave_elite)

            parents = wave_elite[:BREED_FROM_WAVE]
            for e in elite[:BREED_FROM_ELITE]:
                if e["key"] not in {p["key"] for p in parents}:
                    parents.append(e)
            bred = breed(stats, args, run, parents, pool, wave_no)
            if bred:
                merge_elite(stats, [b.pop("_entry") for b in bred])

            stats["wave"] = wave_no
            stats["recent"] = (stats["recent"] + [round(wave_mean, 4)])[-20:]
            best_all = max([e["score"] for e in stats["elite"]] or [0.0])
            recent_mean = sum(stats["recent"][-5:]) / max(1, len(stats["recent"][-5:]))

            picked = {}
            for c in cards:
                picked.setdefault(c["treat"], []).append(round(c["score"], 3))
            conc = sorted(picked.items(), key=lambda kv: -len(kv[1]))

            with open(WAVES, "a") as f:
                f.write(json.dumps({
                    "wave": wave_no, "at": time.time(), "run": run.name,
                    "seconds": round(time.time() - wt, 1),
                    "render_seconds": round(render_s, 1),
                    "picks": {k: v for k, v in picked.items()},
                    "wave_best": round(wave_best, 4),
                    "wave_mean": round(wave_mean, 4),
                    "elite": [{"key": e["key"], "gen": e["gen"], "score": e["score"]}
                              for e in stats["elite"]],
                    "bred": bred,
                    "best_so_far": round(best_all, 4),
                    "treatment_means": {n: round(mean_of(a), 4)
                                        for n, a in stats["treatments"].items() if a["n"]},
                    "instruction_record": {n: {"w": a["wins"], "l": a["losses"],
                                               "mean_gain": round(a["gain"] / max(1, a["n"]), 4)}
                                           for n, a in stats["instructions"].items() if a["n"]},
                }) + "\n")

            write_json(STATS, stats)
            snapshot(stats, wave_no, best_all, recent_mean, pool)

            log(f"[evolve] WAVE {wave_no:>3}  best {wave_best:.3f}  mean {wave_mean:.3f}  "
                f"all-time {best_all:.3f}  {time.time() - wt:.0f}s  "
                f"picks: {', '.join(f'{k}x{len(v)}' for k, v in conc[:6])}")
            log(f"[evolve]   treatments: " + ", ".join(
                f"{n}={mean_of(a):.3f}/{a['n']}" for n, a in
                sorted(stats["treatments"].items(), key=lambda kv: -mean_of(kv[1]))[:6]))
            ins = [(n, a) for n, a in stats["instructions"].items() if a["n"]]
            if ins:
                log("[evolve]   instructions: " + ", ".join(
                    f"{n}={a['wins']}W/{a['losses']}L({a['gain']/max(1,a['n']):+.3f})"
                    for n, a in sorted(ins, key=lambda kv: -(kv[1]["gain"] / max(1, kv[1]["n"])))))

            backoff = 0
            if args.waves and wave_no >= args.waves:
                break
        except Exception:
            # A wave is the unit of failure. One bad render, one unreadable
            # image, one 500 from fluxd must cost a wave, never the loop.
            #
            # The backoff matters as much as the catch: fluxd is restarted out
            # from under this loop by other operators, and a model reload is
            # tens of seconds of connection-refused. A fixed 3s retry turns that
            # into a hundred stack traces and a wave counter in the thousands,
            # which destroys the very log the loop exists to produce.
            # NOT stats["wave"] = wave_no: a wave that never rendered is not a
            # wave, and burning an index on it leaves gaps in evolve_waves.jsonl
            # that read as lost data. The number is retried with the next attempt.
            log("[evolve] WAVE FAILED\n" + traceback.format_exc())
            write_json(STATS, stats)
            backoff = min(60, max(3, backoff * 2))
            log(f"[evolve] retrying in {backoff}s")
            time.sleep(backoff)

    log(f"[evolve] DONE {stats['made']} cards over {stats['wave']} waves in "
        f"{time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
