#!/home/dev/venv/bin/python
"""Render the scenes full-bleed: no card, no type panel, no product.

collection.compose() stamps a product name, a quote and house marks into the
bottom third of every plate. That furniture is the whole reason the previous
output could only ever be portraits. Here the art IS the plate: the rendered
image is written to both art/ and cards/, so the wall shows the picture itself.

Resolution is raised for this run and it is not vanity. The earlier 896x1344
experiment was wrong because those were thumbnails of a portrait; these are dense
scenes -- a night market with a hundred stalls, a tree city with hundreds of lit
windows -- where detail per figure is the point and 448x672 simply cannot hold it.
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
import scenes as S
import treatments as T

D = "http://127.0.0.1:8080"
a = sys.argv[1:]
DEADLINE = float(a[0]) if len(a) > 0 else 1800.0
BATCH = int(a[1]) if len(a) > 1 else 8
STEPS = int(a[2]) if len(a) > 2 else 32
W = int(a[3]) if len(a) > 3 else 640
H = int(a[4]) if len(a) > 4 else 960

PUBLISH = threading.Lock()
PROGRESS = pathlib.Path("/home/dev/dream_progress.json")
LOG = pathlib.Path("/home/dev/dream_log.jsonl")

# Treatments that suit scene work. The label-oriented ones (engraving specimen
# plates, botanical borders) are left out -- they fight a full scene.
SCENE_TREATMENTS = ["cel80s", "oil", "gouache", "ukiyoe", "sumie", "riso",
                    "makie", "stainedglass", "nouveau", "shojo70"]


def render(items, steps):
    body = json.dumps({"items": items, "steps": steps, "width": W, "height": H}).encode()
    req = urllib.request.Request(D + "/batch", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=1800) as r:
        return json.load(r)["images"]


def plan():
    """Every scene, plain and through a few treatments; shuffled so the feed
    never shows six of the same mood in a row."""
    out = []
    for mood, i, sc in S.every():
        out.append((mood, i, sc, None))                    # the scene as written
        for t in random.Random(i).sample(SCENE_TREATMENTS, 3):
            out.append((mood, i, sc, t))                   # and through a medium
    random.Random(7).shuffle(out)
    return out


def main():
    plans = plan()
    print(f"[dream] {len(S.every())} scenes across {len(S.MOODS)} moods -> "
          f"{len(plans)} renders queued; {W}x{H}, batch {BATCH}, steps {STEPS}",
          flush=True)
    for m, items in S.MOODS.items():
        print(f"        {m:<11} {len(items)}", flush=True)

    pool = ThreadPoolExecutor(max_workers=6)
    logf = open(LOG, "a")
    made, t0, i = 0, time.time(), 0

    def finish(run, card, img, meta):
        try:
            src = pathlib.Path(img["path"]).read_bytes()
            (C.ART_DIR / f"{card['key']}.png").write_bytes(src)
            # The picture IS the plate: no compose(), so no type furniture.
            gen_cards = run / "cards"
            gen_cards.mkdir(parents=True, exist_ok=True)
            (gen_cards / f"{card['key']}.png").write_bytes(src)
            with PUBLISH:
                man = json.loads((run / "run.json").read_text())
                man.setdefault("cards", []).append(card)
                (run / "run.json").write_text(json.dumps(man, indent=2))
            logf.write(json.dumps(meta) + "\n"); logf.flush()
        except Exception as e:
            print(f"[dream] plate failed {card['key']}: {e!r}", flush=True)

    while time.time() - t0 < DEADLINE:
        chunk = [plans[(i + k) % len(plans)] for k in range(BATCH)]
        i += BATCH
        moods = sorted({m for m, _, _, _ in chunk})
        run = C.new_run(f"dream {made}: {', '.join(moods)}", STEPS, BATCH)
        (run / "cards").mkdir(parents=True, exist_ok=True)

        items, cards = [], []
        for j, (mood, idx, sc, treat) in enumerate(chunk):
            body = T.TREATMENTS[treat][0].split(".")[0] + "." if treat else None
            key = f"{mood}{idx}{'-' + treat if treat else ''}-{made+j}"
            card = {"key": key, "product": mood.upper(),
                    "subtitle": (treat or "as written"),
                    "quote": sc[:110] + ("..." if len(sc) > 110 else ""),
                    "character": "", "role": "", "family": mood}
            items.append({"prompt": S.prompt_for(sc, body),
                          "seed": 900000 + made + j, "stem": key})
            cards.append((card, {"key": key, "mood": mood, "scene_index": idx,
                                 "treatment": treat, "at": time.time()}))

        tb = time.time()
        try:
            imgs = render(items, STEPS)
        except Exception as e:
            print(f"[dream] render failed: {e!r}", flush=True)
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
            "w": W, "h": H, "steps": STEPS, "moods": moods}))
        print(f"[dream] {made:>4}  {el:>6.1f}s  {dt/len(imgs):.2f}s/img  "
              f"{', '.join(moods)}", flush=True)

    pool.shutdown(wait=True)
    logf.close()
    print(f"[dream] DONE {made} in {time.time()-t0:.1f}s", flush=True)


if __name__ == "__main__":
    main()
