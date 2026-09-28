"""Stage every Beauty collection for influx.pictures: hardlinks named protocol-<album>-stream-<YYYYMMDD>-<HHMMSS>-<NNN>.png."""
import json, os, time
from pathlib import Path
C = Path("/home/ubuntu/Models/flux-output/collections")
P = Path("/home/ubuntu/Models/flux-output/influx-outputs"); P.mkdir(parents=True, exist_ok=True)
ALBUMS = {"emerald": ["emerald"], "couture-veil": ["couture-veil", "couture-veil-all"], "ivory-coat": ["ivory-coat"], "linen-suit": ["linen-suit"],
          "paper-dress": ["paper-dress"], "velvet-cape": ["velvet-cape"], "knitwear-fog": ["knitwear-fog"], "leather-jacket": ["leather-jacket"]}
for album, cols in ALBUMS.items():
    n = seq = 0; used = set()
    for col in cols:
        removed = set(json.loads((C / col / "removed.json").read_text())) if (C / col / "removed.json").exists() else set()
        for l in open(C / col / "history.jsonl"):
            e = json.loads(l)
            if not e.get("file") or e.get("cycle") in removed or not (C / col / e["file"]).exists(): continue
            seq += 1
            name = "protocol-%s-stream-%s-%03d.png" % (album, time.strftime("%Y%m%d-%H%M%S", time.gmtime(e.get("at") or 0)), seq % 1000)
            if name in used or (P / name).exists():
                continue
            used.add(name); os.link(C / col / e["file"], P / name); n += 1
    print("%-15s staged %5d" % (album, n))
print("total staged files:", len(os.listdir(P)))
