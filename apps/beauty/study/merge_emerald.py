"""All emerald studies into one collection, "Emerald": every frame hardlinked (nothing moved or deleted), in time order.
The source studies are kept on disk; their collection.json is renamed collection.merged.json so the list shows one Emerald."""
import json, os, re, time
from pathlib import Path
C = Path("/home/ubuntu/Models/flux-output/collections")
SRC = ["emerald-gown", "emerald-minimal", "emerald-vision", "emerald-gown-all"]
dst = C / "emerald"; (dst / "renders").mkdir(parents=True, exist_ok=True)
rows, seen = [], set()
for s in SRC:
    h = C / s / "history.jsonl"
    if not h.exists(): continue
    for l in h.read_text().splitlines():
        try: e = json.loads(l)
        except Exception: continue
        f = e.get("file")
        if not f or not (C / s / f).exists(): continue
        base = re.sub(r"^renders/(basis-.*?-)?c\d{4}-", "", f).replace("renders/", "")
        if base in seen: continue                      # the same render in two studies is one frame
        seen.add(base)
        rows.append({"src": C / s / f, "name": base, "at": e.get("at") or (C / s / f).stat().st_mtime, "prompt": e.get("prompt"), "from": s})
rows.sort(key=lambda r: r["at"])
with open(dst / "history.jsonl.tmp", "w") as out:
    for i, r in enumerate(rows, 1):
        rel = "renders/" + r["name"]
        if not (dst / rel).exists(): os.link(r["src"], dst / rel)
        out.write(json.dumps({"cycle": i, "file": rel, "prompt": r["prompt"], "outcome": "sorted", "change": "from " + r["from"], "at": r["at"], "net": None}) + "\n")
os.replace(dst / "history.jsonl.tmp", dst / "history.jsonl")
json.dump({"name": "emerald", "title": "Emerald", "subject": "every emerald study together: the gown studies, minimal, vision and the wall's emerald frames",
           "kind": "sorted", "active": False, "generation": len(rows), "updated_at": time.time()}, open(dst / "collection.json", "w"), indent=1)
for s in SRC:
    cj = C / s / "collection.json"
    if cj.exists(): cj.rename(C / s / "collection.merged.json")
print("Emerald:", len(rows), "frames from", SRC)
