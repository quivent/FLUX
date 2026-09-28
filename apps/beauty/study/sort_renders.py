"""Sort every render in ~/Models/flux-output into its study's collection, by the subject its prompt names.
Hardlinks only (no copies, nothing moved or deleted). Chain-study frames stay in their study. Dry run unless --apply."""
import json, os, re, sys, glob, sqlite3, time
OUT = "/home/ubuntu/Models/flux-output"; COLL = OUT + "/collections"
SUBJECTS = [  # (collection, subject, keywords)
    ("ivory-coat", "a tailored ivory coat on a runway at dawn", ["ivory coat"]),
    ("emerald-gown-all", "a silk evening gown in deep emerald", ["emerald"]),
    ("leather-jacket", "a hand-stitched leather jacket in a sunlit atelier", ["leather jacket"]),
    ("paper-dress", "an avant-garde sculptural dress made of folded paper", ["folded paper", "sculptural dress", "paper dress"]),
    ("linen-suit", "a linen summer suit on a Mediterranean terrace", ["linen", "mediterranean"]),
    ("velvet-cape", "a velvet cape in a candlelit gallery", ["velvet cape", "candlelit"]),
    ("knitwear-fog", "a knitwear collection photographed in fog", ["knitwear"]),
    ("couture-veil-all", "a couture veil catching window light", ["couture veil", "veil"]),
    ("teachers-prompt", "the teacher's own prompts: a model, high fashion from ten years ahead", ["a model,"]),
    ("agent-test", "a test prompt an agent wrote over the teacher's (not the teacher's)", ["test save check", "white chair"]),
]
prompt = {}
def put(fn, p, src):
    if fn and p and fn not in prompt: prompt[fn] = (p, src)
for l in open(COLL + "/prompts.jsonl"):
    try: r = json.loads(l); put(os.path.basename(r.get("output") or ""), r.get("prompt"), "prompts.jsonl")
    except Exception: pass
db = sqlite3.connect("/home/ubuntu/FLUX/.fluxd/studio.sqlite")
byjob = {}
for jid, p, pj in db.execute("select id, prompt, payload_json from atlas_jobs"):
    byjob[jid] = p
for f in ("/home/ubuntu/FLUX/.fluxd/jobs.jsonl", OUT + "/audit.jsonl"):
    for l in open(f):
        try: r = json.loads(l); byjob.setdefault(r.get("id") or r.get("job_id"), r.get("prompt"))
        except Exception: pass
chain = {}   # root filename -> study, for frames a chain study already holds
for h in glob.glob(COLL + "/*/history.jsonl"):
    study = h.split("/")[-2]
    for l in open(h):
        try: e = json.loads(l)
        except Exception: continue
        if e.get("file"): chain[re.sub(r"^renders/(basis-.*?-)?c\d{4}-", "", e["file"])] = study
files = sorted(os.path.basename(f) for f in glob.glob(OUT + "/*.png"))
plan, unknown, inchain = {}, [], 0
for fn in files:
    if fn in chain: inchain += 1; continue
    p = prompt.get(fn, (None,))[0]
    if not p:
        m = re.search(r"(\d{8}-\d{6}-[0-9a-f]{8})", fn); p = byjob.get(m.group(1)) if m else None
    low = (p or "").lower(); best = None
    for name, subj, kws in SUBJECTS:
        for k in kws:
            i = low.find(k)
            if i >= 0 and (best is None or i < best[0]): best = (i, name)
    if best: plan.setdefault(best[1], []).append((fn, p))
    else: unknown.append((fn, p))
print("renders", len(files), "| already in a chain study", inchain, "| unsorted", len(unknown))
for name, subj, _ in SUBJECTS: print("%5d  %s" % (len(plan.get(name, [])), name))
import collections as _c
for k, n in _c.Counter((p or "(no prompt found)")[:110] for _, p in unknown).most_common(20): print("  ?", n, repr(k))
if 0: print("  ?", fn, repr((p or "")[:120]))
if "--apply" in sys.argv:
    if unknown: plan["unsorted"] = unknown
    subj = {n: s for n, s, _ in SUBJECTS}; subj["unsorted"] = "renders whose prompt names no study"
    for name, rows in plan.items():
        d = COLL + "/" + name; os.makedirs(d + "/renders", exist_ok=True)
        rows.sort(key=lambda r: os.path.getmtime(OUT + "/" + r[0]))
        with open(d + "/history.jsonl.tmp", "w") as h:
            for i, (fn, p) in enumerate(rows, 1):
                rel = "renders/" + fn
                if not os.path.exists(d + "/" + rel): os.link(OUT + "/" + fn, d + "/" + rel)
                h.write(json.dumps({"cycle": i, "file": rel, "prompt": p, "outcome": "sorted", "change": (p or "")[:160],
                                    "at": os.path.getmtime(OUT + "/" + fn), "net": None}) + "\n")
        os.replace(d + "/history.jsonl.tmp", d + "/history.jsonl")
        cj = d + "/collection.json"
        c = json.load(open(cj)) if os.path.exists(cj) else {}
        c.update(name=name, subject=subj[name], kind="sorted", active=False, generation=len(rows), updated_at=time.time())
        json.dump(c, open(cj, "w"), indent=1)
        print("wrote", name, len(rows))
