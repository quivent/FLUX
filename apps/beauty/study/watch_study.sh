# wait for N new frames of the study, then print each frame's verdict, the writer's reasoning, and the rendered prompt
N=${1:-6}; D=/home/ubuntu/Models/flux-output/collections/forest-at-a-distance; H=$D/history.jsonl
n0=$( [ -f $H ] && wc -l < $H || echo 0 ); end=$(( $(date +%s) + ${2:-420} ))
while [ $(date +%s) -lt $end ]; do [ -f $H ] && [ $(wc -l < $H) -ge $((n0+N)) ] && break; inotifywait -q -t 30 -e modify,create $D >/dev/null 2>&1 || sleep 2; done
python3 - "$n0" <<"PY"
import json, sys, time
D="/home/ubuntu/Models/flux-output/collections/forest-at-a-distance/"
rows=[json.loads(l) for l in open(D+"history.jsonl")][int(sys.argv[1]):]
c=json.load(open(D+"collection.json"))
for e in rows:
    if "profile" not in e and e.get("outcome")=="teacher mark": continue
    prof=" ".join("%s%+g"%(a.split()[0][:5],v["d"]) for a,v in (e.get("profile") or {}).items())
    print("c%s %s %-6s %s | %s"%(e.get("cycle"), time.strftime("%H:%M:%S",time.gmtime(e.get("at",0))), e.get("move"), e.get("outcome"), prof))
    print("   craft:", e.get("craft")); print("   hyp:", (e.get("hypothesis") or "")[:170])
    if e.get("profile"): print("   judge:", next(iter(e["profile"].values())).get("why"))
print("NOW best:", (c.get("best") or {}).get("file"), "| stall", c.get("stall"), "| next:", c.get("last_change"), "| guidance", c.get("guidance"))
print("insight:", json.dumps(c.get("insight"))[:400])
PY
