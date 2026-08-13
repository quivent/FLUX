#!/home/dev/venv/bin/python
"""Render the visionary set, and keep the operator's evolve queue draining.

Two jobs in one loop because they must share one GPU: fluxd serialises everything
behind a single asyncio lock, so two render loops do not thrash VRAM but they do
halve each other's throughput. The operator's manual evolve requests outrank the
sweep, so they are drained at the top of every batch -- strict priority, no
contention, the same shape blast2 uses.

Full bleed: the picture is the plate. No compose(), no type panel, no product.
"""
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
import treatments as T
import visions as V

D = "http://127.0.0.1:8080"
a = sys.argv[1:]
DEADLINE = float(a[0]) if len(a) > 0 else 3600.0
BATCH = int(a[1]) if len(a) > 1 else 6
STEPS = int(a[2]) if len(a) > 2 else 34
W = int(a[3]) if len(a) > 3 else 704
H = int(a[4]) if len(a) > 4 else 1056

PUBLISH = threading.Lock()
PROGRESS = pathlib.Path("/home/dev/vision_progress.json")
LOG = pathlib.Path("/home/dev/vision_log.jsonl")

# Painterly mediums only. A risograph or a papercut cannot hold atmospheric depth,
# and depth is the whole point of this set.
LOOKS = ["oil", "gouache", "sumie", "makie", "stainedglass", "ukiyoe"]

try:
    import evolve_queue as EQ
except Exception:
    EQ = None


def render(items, steps, seeds=None):
    body = json.dumps({"items": items, "steps": steps, "width": W, "height": H}).encode()
    req = urllib.request.Request(D + "/batch", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=1800) as r:
        return json.load(r)["images"]


def plan():
    out = []
    for i, v in V.every():
        out.append((i, v, None))                       # as written
        for t in random.Random(i).sample(LOOKS, 2):    # and through two mediums
            out.append((i, v, t))
    random.Random(11).shuffle(out)
    return out


def main():
    plans = plan()
    print(f"[vision] {len(V.VISIONS)} visions -> {len(plans)} renders; "
          f"{W}x{H}, batch {BATCH}, steps {STEPS}", flush=True)

    pool = ThreadPoolExecutor(max_workers=6)
    logf = open(LOG, "a")
    made, t0, i = 0, time.time(), 0

    def finish(run, card, img, meta):
        try:
            src = pathlib.Path(img["path"]).read_bytes()
            (C.ART_DIR / f"{card['key']}.png").write_bytes(src)
            cards = run / "cards"
            cards.mkdir(parents=True, exist_ok=True)
            (cards / f"{card['key']}.png").write_bytes(src)
            with PUBLISH:
                man = json.loads((run / "run.json").read_text())
                man.setdefault("cards", []).append(card)
                (run / "run.json").write_text(json.dumps(man, indent=2))
            logf.write(json.dumps(meta) + "\n"); logf.flush()
        except Exception as e:
            print(f"[vision] plate failed {card['key']}: {e!r}", flush=True)

    while time.time() - t0 < DEADLINE:
        # operator first, always
        if EQ is not None:
            try:
                n = EQ.serve_pending(PUBLISH, print, budget=3, worker=f"vision:{__import__('os').getpid()}")
                if n:
                    print(f"[vision] served {n} operator evolve request(s) first", flush=True)
            except Exception as e:
                print(f"[vision] queue drain failed: {e!r}", flush=True)

        chunk = [plans[(i + k) % len(plans)] for k in range(BATCH)]
        i += BATCH
        C.W, C.H = W, H
        run = C.new_run(f"vision {made}", STEPS, BATCH)
        (run / "cards").mkdir(parents=True, exist_ok=True)

        items, cards = [], []
        for j, (idx, v, treat) in enumerate(chunk):
            body = T.TREATMENTS[treat][0].split(".")[0] + "." if treat else None
            key = f"vision{idx}{'-' + treat if treat else ''}-{made+j}"
            card = {"key": key, "product": "VISION", "subtitle": treat or "as written",
                    "quote": v[:110] + ("..." if len(v) > 110 else ""),
                    "character": "", "role": "", "family": "vision"}
            items.append({"prompt": V.prompt_for(v, body),
                          "seed": 1300000 + made + j, "stem": key,
                          "negative": V.NEG})
            cards.append((card, {"key": key, "vision": idx, "treatment": treat,
                                 "at": time.time()}))

        tb = time.time()
        try:
            imgs = render(items, STEPS)
        except Exception as e:
            print(f"[vision] render failed: {e!r}", flush=True)
            time.sleep(1)
            continue
        dt = time.time() - tb

        for (card, meta), img in zip(cards, imgs):
            pool.submit(finish, run, card, img, meta)

        made += len(imgs)
        el = time.time() - t0
        PROGRESS.write_text(json.dumps({
            "made": made, "elapsed": round(el, 1), "deadline": DEADLINE,
            "per_image_s": round(dt / max(1, len(imgs)), 3),
            "w": W, "h": H, "steps": STEPS}))
        print(f"[vision] {made:>4}  {el:>6.1f}s  {dt/len(imgs):.2f}s/img", flush=True)

    pool.shutdown(wait=True)
    logf.close()
    print(f"[vision] DONE {made} in {time.time()-t0:.1f}s", flush=True)


if __name__ == "__main__":
    main()
