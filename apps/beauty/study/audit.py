"""Audit the study loop against what actually happened: every invariant, over the frames since a given cycle."""
import json, re, sys, collections
D = "/home/ubuntu/Models/flux-output/collections/forest-at-a-distance/"
since = int(sys.argv[1])
rows = [json.loads(l) for l in open(D + "history.jsonl")]
fr = [e for e in rows if e.get("file") and e.get("cycle", 0) >= since]
marks_rows = [e for e in rows if e.get("outcome") == "teacher mark" and e.get("cycle", 0) >= since]
jobs = {}
for l in open("/home/ubuntu/FLUX/.fluxd/jobs.jsonl"):
    try: j = json.loads(l); jobs[j["id"]] = j
    except Exception: pass
sd = lambda f: (re.search(r"seed-(\d+)", f or "") or [None, None])[1]
subj = json.load(open("/home/ubuntu/FLUX/.fluxd/collection.json"))["subject"]
print("frames audited:", len(fr), "(cycles %d-%d); teacher-mark rows: %d" % (fr[0]["cycle"], fr[-1]["cycle"], len(marks_rows)))
bad = collections.defaultdict(list)
keys = collections.Counter((sd(e["file"]), e.get("prompt")) for e in fr)
for e in fr:
    c = e["cycle"]
    if keys[(sd(e["file"]), e.get("prompt"))] > 1: bad["repeat (same seed and words)"].append(c)
    j = jobs.get(e.get("job"))
    if not j: bad["job missing from the ledger"].append(c)
    elif j.get("prompt") != e.get("prompt"): bad["rendered prompt differs from recorded"].append(c)
    p = e.get("prompt") or ""
    if not p.startswith(subj[:40]): bad["prompt does not start with the subject"].append(c)
    if e.get("craft") and e["craft"] not in p: bad["craft not in the rendered prompt"].append(c)
    if "profile" in e and e.get("base"):
        pr = e["profile"] or {}
        if not pr: bad["judged with an empty profile"].append(c)
        elif all(v.get("d", 0) == 0 for a, v in pr.items() if a != "change") and not str(e["outcome"]).startswith("not kept: too little"):
            bad["judge answered all zeros"].append(c)
        calls = [v.get("calls") for v in pr.values() if v.get("calls")]
        if calls and len(calls[0]) != 2: bad["not two judge calls"].append(c)
        if calls and sorted(o for o, _ in calls[0]) != [-1, 1]: bad["the two calls were not both orders"].append(c)
        same = sd(e["file"]) == sd(e["base"])
        if not same and "writer failed" not in (e.get("change") or "") and c % 6 != 1: bad["new seed on a non-explore step"].append(c)
        if same and (e.get("measured") or {}).get("d_champ", 1) < 0.001: bad["identical to its base"].append(c)
    if "writer failed" in (e.get("change") or "") or "proposal failed" in (e.get("change") or ""): bad["writer failed"].append(c)
    if len((e.get("craft") or "").split()) > 30: bad["craft longer than 30 words"].append(c)
    if re.search(r"(?i)\b(wide|full[- ]body|three[- ]quarter|medium) shot\b.*\b(wide|full[- ]body|three[- ]quarter|medium) shot\b", e.get("craft") or ""):
        bad["two shot sizes in one craft"].append(c)
ts = [e["at"] for e in fr]
gaps = [b - a for a, b in zip(ts, ts[1:])]
print("seconds per frame: median %.1f, max %.1f" % (sorted(gaps)[len(gaps)//2], max(gaps)))
print("outcomes:", collections.Counter(str(e.get("outcome", ""))[:26] for e in fr).most_common())
print("same seed as base:", sum(1 for e in fr if e.get("base") and sd(e["file"]) == sd(e["base"])), "of", sum(1 for e in fr if e.get("base")))
for k, v in bad.items(): print("FAULT %-45s %3d  e.g. %s" % (k, len(v), v[:8]))
if not bad: print("no faults")
