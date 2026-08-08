#!/home/dev/venv/bin/python
"""Batch-size benchmark at the current render settings: is batch 1 actually worse?

Runs with the perpetual worker stopped so nothing else is contending for the GPU.
3 reps per batch size, warmup discarded, reports per-image cost and the throughput
penalty of dropping to 1.
"""
import json
import statistics
import time
import urllib.request

D = "http://127.0.0.1:8080"
STEPS, W, H = 32, 448, 672
REPS = 3
SIZES = [1, 2, 4]

P = ("koyomi spice label, sichuan pepper, red husk, numbing heat, "
     "illustrated character card, clean flat paper, botanical border")


def batch(n, seed0):
    items = [{"prompt": P, "seed": seed0 + i, "stem": f"bench-{n}-{i}"} for i in range(n)]
    body = json.dumps({"items": items, "steps": STEPS, "width": W, "height": H}).encode()
    req = urllib.request.Request(D + "/batch", data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=1800) as r:
        r.read()
    return time.time() - t0


print(f"warmup…", flush=True)
batch(1, 1)

res = {}
for n in SIZES:
    runs = [batch(n, 5000 + n * 100 + r * 10) for r in range(REPS)]
    res[n] = runs
    print(f"  batch {n}: " + ", ".join(f"{x:.2f}s" for x in runs), flush=True)

print(f"\n{'batch':>5} {'median':>9} {'per-image':>11} {'imgs/min':>10} {'vs batch 4':>11}")
base = statistics.median(res[4]) / 4
for n in SIZES:
    m = statistics.median(res[n])
    per = m / n
    print(f"{n:>5} {m:>8.2f}s {per:>10.3f}s {60/per:>9.1f} {per/base:>10.2f}x")

per1 = statistics.median(res[1]) / 1
per4 = statistics.median(res[4]) / 4
print(f"\nbatch 1 costs {(per1/per4 - 1) * 100:.1f}% more GPU time per image than batch 4")
print(f"latency to FIRST image: batch1 {statistics.median(res[1]):.2f}s "
      f"vs batch4 {statistics.median(res[4]):.2f}s")
