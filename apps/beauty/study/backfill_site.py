import json, os, time
from pathlib import Path
D = Path("/home/ubuntu/Models/flux-output/collections/forest-at-a-distance")
P = Path("/home/ubuntu/Models/flux-output/influx-outputs"); P.mkdir(parents=True, exist_ok=True)
n = 0
for l in open(D / "history.jsonl"):
    e = json.loads(l)
    if e.get("file") and (D / e["file"]).exists():
        name = "protocol-nudity-in-nature-stream-%s-%03d.png" % (time.strftime("%Y%m%d-%H%M%S", time.gmtime(e.get("at") or 0)), int(e["cycle"]) % 1000)
        if not (P / name).exists():
            os.link(D / e["file"], P / name); n += 1
for p in ["/home/ubuntu/FLUX/.fluxd/collection.json", str(D / "collection.json")]:
    c = json.load(open(p)); c["title"] = "Nudity in nature"; c["album"] = "nudity-in-nature"; json.dump(c, open(p, "w"), indent=1)
print("backfilled", n, "frames; title and album set;", len(os.listdir(P)), "files staged")
