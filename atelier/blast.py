#!/home/dev/venv/bin/python
"""A driven sweep: N distinct cards inside a deadline, at the best quality the
clock allows.

    blast.py TARGET DEADLINE_S BATCH [WIDTH HEIGHT STEPS]

The perpetual loop cannot do this and it is not a tuning problem: it spends ~14s
per SINGLE card on triage, CLIP scoring, compose, a governor consult and the
interval sleep, against a 2.17s render. So this drives fluxd directly and:

  1. BATCHES -- per-image cost falls with batch size (2.488s at 1, 2.173s at 4).
  2. OVERLAPS -- compose() is PIL on the CPU while the GPU is idle, so it runs in
     a thread pool against the next batch's render.
  3. SPENDS THE SLACK ON QUALITY -- with STEPS given, steps are pinned and the
     budget goes into resolution and step count. Without it, steps float to hold
     the schedule.

"Different" is not seed noise. The sweep is the full cartesian product of every
living concept against all 144 axis combinations (framing x line x palette x
ornament) -- 2304 distinct prompts, shuffled on a fixed seed so consecutive cards
differ on several axes at once.

C.publish() read-modify-writes the run's manifest, so the compose pool must
serialise on it -- six threads publishing into one run raced and corrupted
run.json, losing cards from the feed.
"""
import itertools
import json
import pathlib
import random
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "/home/dev")
import collection as C
import concepts as K

D = "http://127.0.0.1:8080"
a = sys.argv[1:]
TARGET = int(a[0]) if len(a) > 0 else 250
DEADLINE_S = float(a[1]) if len(a) > 1 else 1800.0
BATCH = int(a[2]) if len(a) > 2 else 8
W = int(a[3]) if len(a) > 3 else 448
H = int(a[4]) if len(a) > 4 else 672
FIXED_STEPS = int(a[5]) if len(a) > 5 else 0

STEPS_HI, STEPS_LO = 32, 10
PROGRESS = pathlib.Path("/home/dev/blast_progress.json")
PUBLISH = threading.Lock()          # C.publish mutates a shared manifest
LOGLOCK = threading.Lock()


def render(items, steps):
    body = json.dumps({"items": items, "steps": steps, "width": W, "height": H}).encode()
    req = urllib.request.Request(D + "/batch", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=1800) as r:
        return json.load(r)["images"]


def plan():
    specs = K.as_spec(K.alive(K.load()))
    names = list(C.AXES)
    combos = [dict(zip(names, lv)) for lv in
              itertools.product(*[list(C.AXES[n]) for n in names])]
    pairs = [(s, ax) for ax in combos for s in specs]
    random.Random(20260808).shuffle(pairs)
    return pairs


def main():
    pairs = plan()
    budget = DEADLINE_S / TARGET
    print(f"[blast] {len(pairs)} distinct concept x axis combinations; target {TARGET} "
          f"in {DEADLINE_S:.0f}s = {budget:.2f}s/image budget", flush=True)
    print(f"[blast] {W}x{H}, batch {BATCH}, steps "
          f"{'pinned ' + str(FIXED_STEPS) if FIXED_STEPS else 'adaptive'}", flush=True)

    steps = FIXED_STEPS or 16
    made = 0
    t0 = time.time()
    pool = ThreadPoolExecutor(max_workers=6)
    varlog = open("/home/dev/variations.jsonl", "a")

    def finish(run, card, img, axes):
        try:
            (C.ART_DIR / f"{card['key']}.png").write_bytes(
                pathlib.Path(img["path"]).read_bytes())
            plate = C.compose(card)            # PIL, the expensive part, unlocked
            with PUBLISH:                      # manifest write, must be serial
                C.publish(run, card, plate, img)
            with LOGLOCK:
                varlog.write(json.dumps({"key": card["key"], "at": time.time(),
                                         "axes": axes, "mode": "blast"}) + "\n")
                varlog.flush()
        except Exception as e:
            print(f"[blast] plate failed {card['key']}: {e!r}", flush=True)

    i = 0
    while made < TARGET:
        elapsed = time.time() - t0
        if elapsed > DEADLINE_S:
            print(f"[blast] deadline reached at {made}", flush=True)
            break

        chunk = [pairs[(i + k) % len(pairs)] for k in range(BATCH)]
        i += BATCH
        C.W, C.H = W, H
        run = C.new_run(f"blast {made}-{made+BATCH} @{W}x{H} s{steps}", steps, BATCH)
        items, cards = [], []
        for j, (spec, axes) in enumerate(chunk):
            card = dict(spec)
            card["key"] = f"{spec['key']}-b{made+j}"
            items.append({"prompt": C.prompt_for(card, axes),
                          "seed": 100000 + made + j, "stem": card["key"]})
            cards.append((card, axes))

        tb = time.time()
        try:
            imgs = render(items, steps)
        except Exception as e:
            print(f"[blast] render failed: {e!r}", flush=True)
            time.sleep(1)
            continue
        dt = time.time() - tb

        for (card, axes), img in zip(cards, imgs):
            pool.submit(finish, run, card, img, axes)

        made += len(imgs)
        per = dt / max(1, len(imgs))
        elapsed = time.time() - t0
        rate = made / max(0.001, elapsed)
        remain = max(0.001, DEADLINE_S - elapsed)
        need = (TARGET - made) / remain

        if not FIXED_STEPS:
            if need > rate * 1.05 and steps > STEPS_LO:
                steps -= 2
            elif need < rate * 0.80 and steps < STEPS_HI:
                steps += 1

        PROGRESS.write_text(json.dumps({
            "made": made, "target": TARGET, "elapsed": round(elapsed, 1),
            "deadline": DEADLINE_S, "rate_per_s": round(rate, 3),
            "per_image_s": round(per, 3), "steps": steps, "w": W, "h": H,
            "projected_total": int(rate * DEADLINE_S), "batch": BATCH}))
        print(f"[blast] {made:>4}/{TARGET}  {elapsed:>6.1f}s  {per:.3f}s/img  "
              f"rate {rate:.2f}/s  steps {steps}  proj {int(rate*DEADLINE_S)}", flush=True)

    pool.shutdown(wait=True)
    varlog.close()
    total = time.time() - t0
    print(f"[blast] DONE {made} cards in {total:.1f}s = {made/total:.2f}/s "
          f"({total/max(1,made):.3f}s per image) at {W}x{H}", flush=True)


if __name__ == "__main__":
    main()
