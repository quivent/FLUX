"""Replication: every accepted step since the neutral judge started, re-judged fresh in both orders (one call after the other)."""
import json, sys, time
sys.path.insert(0, "/home/ubuntu/tracker")
import beauty_diff as bd
D = "/home/ubuntu/Models/flux-output/collections/forest-at-a-distance/"
since = float(sys.argv[1])
rows = [json.loads(l) for l in open(D + "history.jsonl")]
new = [e for e in rows if e.get("at", 0) >= since and e.get("profile")]
acc = [e for e in new if str(e.get("outcome", "")).startswith("accepted: better")]
print("frames judged since the neutral judge:", len(new), "| accepted:", len(acc))
held = 0
for e in acc:
    a, b = D + e["base"], D + e["file"]
    fwd, _ = bd.judge("qwen", a, b, "")        # new frame as B
    rev, _ = bd.judge("qwen", b, a, "")        # new frame as A
    beauty = (fwd["beauty"]["d"] - rev["beauty"]["d"]) / 2
    uniq = (fwd["original"]["d"] - rev["original"]["d"]) / 2
    ok = beauty > 0 or (beauty == 0 and uniq > 0)
    held += ok
    print("c%s -> c%s | loop net %+.1f | replication: beauty %+.1f uniqueness %+.1f | %s | change %.2f" % (
        e["base"][9:13], e["cycle"], e.get("net", 0), beauty, uniq, "HOLDS" if ok else "does not hold", (e.get("measured") or {}).get("d_champ", 0)))
print("replicated: %d of %d" % (held, len(acc)))
