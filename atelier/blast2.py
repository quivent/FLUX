#!/home/dev/venv/bin/python
"""Render the treatment grid: 14 treatments x 16 concepts, all genuinely different.

blast.py swept adjectives inside one fixed composition and produced one picture
144 ways. This walks treatments instead -- each replaces the whole style block, so
neighbouring cards read as different artists rather than different settings of the
same artist.

Order matters for the wall: treatment-major would give fourteen long identical
runs. Advancing BOTH treatment and concept every card means each consecutive
plate changes medium and subject, so the feed reads as a portfolio immediately.

An operator override file (grid_override.json) is re-read every batch, so a
prompt typed on the panel takes effect on the next batch without a restart.
"""
import json
import os
import pathlib
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "/home/dev")
import collection as C
import concepts as K
import evolve_queue as EQ
import treatments as T

D = "http://127.0.0.1:8080"
a = sys.argv[1:]
DEADLINE_S = float(a[0]) if len(a) > 0 else 1500.0
BATCH = int(a[1]) if len(a) > 1 else 12
STEPS = int(a[2]) if len(a) > 2 else 28
W, H = 448, 672

PROGRESS = pathlib.Path("/home/dev/blast_progress.json")
OVERRIDE = pathlib.Path("/home/dev/grid_override.json")
PUBLISH = threading.Lock()
LOGLOCK = threading.Lock()


def render(items, steps):
    body = json.dumps({"items": items, "steps": steps, "width": W, "height": H}).encode()
    req = urllib.request.Request(D + "/batch", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=1800) as r:
        return json.load(r)["images"]


def override():
    """{prompt_suffix, only_treatments[], only_slots[]} -- read fresh every batch."""
    try:
        return json.loads(OVERRIDE.read_text())
    except Exception:
        return {}


def plan(names, specs):
    pairs = []
    for i in range(max(len(names), len(specs)) * 40):
        pairs.append((specs[(i // len(names) + i) % len(specs)], names[i % len(names)]))
    return pairs


def main():
    print(f"[grid] {len(T.NAMES)} treatments; {W}x{H}, batch {BATCH}, "
          f"steps {STEPS}, {DEADLINE_S:.0f}s", flush=True)
    print("       " + ", ".join(T.NAMES), flush=True)

    made, t0 = 0, time.time()
    pool = ThreadPoolExecutor(max_workers=6)
    varlog = open("/home/dev/variations.jsonl", "a")

    def finish(run, card, img, treat):
        try:
            (C.ART_DIR / f"{card['key']}.png").write_bytes(
                pathlib.Path(img["path"]).read_bytes())
            plate = C.compose(card)
            with PUBLISH:
                C.publish(run, card, plate, img)
            with LOGLOCK:
                varlog.write(json.dumps({"key": card["key"], "at": time.time(),
                                         "axes": {"treatment": treat},
                                         "mode": "grid"}) + "\n")
                varlog.flush()
        except Exception as e:
            print(f"[grid] plate failed {card['key']}: {e!r}", flush=True)

    i = 0
    while time.time() - t0 < DEADLINE_S:
        # PREEMPTION: operator evolve requests jump the grid. Draining here,
        # between batches, gives them strict priority with zero GPU contention --
        # this loop is the only caller of fluxd, so a Kontext edit and a grid
        # batch can never be in flight at the same time. PUBLISH is passed in
        # because C.publish read-modify-writes a run manifest.
        try:
            EQ.serve_pending(publish_lock=PUBLISH,
                             log=lambda m: print(m, flush=True),
                             worker=f"blast2:{os.getpid()}")
        except Exception as e:
            print(f"[grid] evolve drain failed: {e!r}", flush=True)

        ov = override()
        names = [n for n in (ov.get("only_treatments") or T.NAMES) if n in T.TREATMENTS] or T.NAMES
        specs = K.as_spec(K.alive(K.load()))
        if ov.get("only_slots"):
            keep = [s for s in specs if s["key"].split("-")[0] in ov["only_slots"]]
            specs = keep or specs
        suffix = (ov.get("prompt_suffix") or "").strip()
        pairs = plan(names, specs)

        chunk = [pairs[(i + k) % len(pairs)] for k in range(BATCH)]
        i += BATCH
        C.W, C.H = W, H
        treats = sorted({t for _, t in chunk})
        run = C.new_run(f"grid {made}: {', '.join(treats[:4])}", STEPS, BATCH)
        items, cards = [], []
        for j, (spec, treat) in enumerate(chunk):
            card = dict(spec)
            card["key"] = f"{spec['key']}-{treat}-{made+j}"
            p = T.style_for(treat, spec)
            if suffix:
                p = p + " " + suffix
            items.append({"prompt": p, "seed": 500000 + made + j, "stem": card["key"]})
            cards.append((card, treat))

        tb = time.time()
        try:
            imgs = render(items, STEPS)
        except Exception as e:
            print(f"[grid] render failed: {e!r}", flush=True)
            time.sleep(1)
            continue
        dt = time.time() - tb

        for (card, treat), img in zip(cards, imgs):
            pool.submit(finish, run, card, img, treat)

        made += len(imgs)
        el = time.time() - t0
        PROGRESS.write_text(json.dumps({
            "made": made, "target": 0, "elapsed": round(el, 1),
            "deadline": DEADLINE_S, "rate_per_s": round(made / max(.001, el), 3),
            "per_image_s": round(dt / max(1, len(imgs)), 3), "steps": STEPS,
            "w": W, "h": H, "batch": BATCH, "suffix": suffix,
            "treatments": names,
            "projected_total": int(made / max(.001, el) * DEADLINE_S)}))
        print(f"[grid] {made:>4}  {el:>6.1f}s  {dt/len(imgs):.3f}s/img  "
              f"{', '.join(treats[:5])}" + (f"  +'{suffix[:40]}'" if suffix else ""),
              flush=True)

    pool.shutdown(wait=True)
    varlog.close()
    tot = time.time() - t0
    print(f"[grid] DONE {made} in {tot:.1f}s = {tot/max(1,made):.3f}s/img", flush=True)


if __name__ == "__main__":
    main()
