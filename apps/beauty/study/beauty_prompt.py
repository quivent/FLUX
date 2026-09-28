"""The prompt behind the Beauty wall, and the directions to factor into it.

State lives in ~/FLUX/.fluxd/prompt.json:
  prompts     the prompts as written, one per line on /control (the wall rotates through them)
  directions  notes on what to factor in; each is folded into every prompt
  composed    each prompt with the directions appended as written (no model rewrites it)
gpu_filler.py reads `composed` for every render it queues.

Served on 127.0.0.1:8096; Caddy routes beauty.influx.vision/api/prompt*, /api/judging and /api/collection here.
  GET    /api/prompt                   the state
  POST   /api/prompt                   {"prompts": [...]} replaces the prompts
  POST   /api/prompt/direction         {"text": "..."} adds a direction
  DELETE /api/prompt/direction?id=...  removes one
"""
import json, os, re, threading, time, urllib.request, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

FILE = Path("/home/ubuntu/FLUX/.fluxd/prompt.json")
JUDGING = Path("/home/ubuntu/FLUX/.fluxd/judging.json")
COLL = Path("/home/ubuntu/FLUX/.fluxd/collection.json")
LIVE = Path("/home/ubuntu/FLUX/.fluxd/beauty_live_prompt.json")   # beauty_pipeline.py renders this text from its next frame
SAVED = Path("/home/ubuntu/Models/flux-output/collections/saved-prompts.json")   # streams to R2 with the collections    # the active collection (beauty_jury.py evolves it, gpu_filler.py renders it)
COLLECTIONS = Path("/home/ubuntu/Models/flux-output/collections")
STUDY_CFG = Path("/home/ubuntu/FLUX/.fluxd/study_config.json")   # the study loop's settings (Collection page)


HEAVY = ("rendered", "tried_by_base", "crafts_by_base", "bias_hist", "memory", "lessons", "tried", "anchor_cw", "heads", "batch", "teacher_steps")


def slim(cur):
    """What the pages show of the active study: without the loop's internal lists (they grew to ~144 KB per poll)."""
    return {k: v for k, v in (cur or {}).items() if k not in HEAVY}


def collections_list():
    out = []
    for f in sorted(COLLECTIONS.glob("*/collection.json")):
        try:
            c = json.loads(f.read_text())
            out.append({k: c.get(k) for k in ("name", "title", "subject", "generation", "best", "updated_at")})
        except Exception:
            pass
    return out


def slug(t):
    return "".join(ch if ch.isalnum() else "-" for ch in t.lower()).strip("-")[:48] or "collection"   # what the jury judges (beauty_jury.py reads it): wall | off
WITNESS = "http://127.0.0.1:8001/v1/chat/completions"
LOCK = threading.Lock()
SEED = [
    "a tailored ivory coat on a runway at dawn", "silk evening gown in deep emerald, studio portrait",
    "a hand-stitched leather jacket hanging in a sunlit atelier", "an avant-garde sculptural dress made of folded paper",
    "a linen summer suit on a Mediterranean terrace", "a velvet cape in a candlelit gallery",
    "a knitwear collection photographed in fog", "a couture veil catching window light",
]


def load():
    try:
        return json.loads(FILE.read_text())
    except Exception:
        return {"prompts": SEED, "directions": [], "composed": SEED, "composer": "none", "updated_at": time.time()}


def save(s):
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, indent=1))
    os.replace(tmp, FILE)


def witness_model():
    with urllib.request.urlopen(WITNESS.replace("chat/completions", "models"), timeout=3) as r:
        return json.load(r)["data"][0]["id"]


def fold(prompt, directions, model):
    notes = "\n".join(f"- {d['text']}" for d in directions)
    body = {"model": model, "temperature": 0, "max_tokens": 300,
            "chat_template_kwargs": {"enable_thinking": False},
            "messages": [{"role": "system", "content": "You rewrite FLUX image prompts. Keep the prompt's subject and intent, "
                          "work every direction into it, and return only the rewritten prompt as one paragraph."},
                         {"role": "user", "content": f"Prompt:\n{prompt}\n\nDirections:\n{notes}"}]}
    req = urllib.request.Request(WITNESS, json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)["choices"][0]["message"]["content"].strip().strip('"')


def compose(s):
    """Fill s["composed"], one per prompt."""
    ds = s["directions"]
    if not ds:
        s["composed"], s["composer"] = list(s["prompts"]), "none"
        return s
    tail = "; ".join(d["text"] for d in ds)
    # no model rewrites anything: the prompt as written, then the directions as written
    s["composed"], s["composer"] = [f"{p}. {tail}" for p in s["prompts"]], "appended as written"
    return s


class H(BaseHTTPRequestHandler):
    def send(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        path = urlparse(self.path).path.rstrip("/")
        if path == "/api/saved-prompt":            # the teacher's own saved prompts, every version kept
            try:
                return self.send(json.loads(SAVED.read_text()))
            except Exception:
                return self.send({"current": "", "versions": []})
        if path == "/api/jurors":                 # who is actually seated, checked at the port (not the Beauty server's old list)
            seats = [("pixtral", "Pixtral 12B · W4A16", "Aesthetic critic", 8004),
                     ("qwen", "Ornith 1.5 · English-v2 FP8", "Structural witness", 8006),
                     ("governor", "Gemma 4 12B · FP8", "Synthesis · law & intent", 8005)]
            out = []
            for key, name, job, port in seats:
                try:
                    with urllib.request.urlopen("http://127.0.0.1:%d/v1/models" % port, timeout=2) as r:
                        ids = [m["id"] for m in json.load(r)["data"]]
                    live = True
                except Exception:
                    ids, live = [], False
                out.append({"key": key, "name": name, "job": job, "port": port, "live": live, "models": ids})
            return self.send({"jurors": out})
        if path == "/api/collection/config":      # the teacher's settings for the study loop, read by beauty_jury every frame
            try:
                cfg = json.loads(STUDY_CFG.read_text())
            except Exception:
                cfg = {}
            return self.send({"config": cfg})
        if path == "/api/collection/history":
            q = parse_qs(urlparse(self.path).query)
            name = slug(q.get("name", [""])[0])
            limit = max(1, min(400, int(q.get("limit", ["120"])[0] or 120)))
            hist = COLLECTIONS / name / "history.jsonl"
            rows = []
            try:
                for line in hist.read_text().splitlines():
                    e = json.loads(line)
                    if "cycle" not in e:
                        continue                      # older, pre-cycle entries
                    rows.append({"cycle": e["cycle"], "outcome": e.get("outcome"), "net": e.get("net"), "aim": e.get("aim"),
                                 "change": e.get("change"), "at": e.get("at"),
                                 "src": "/outputs/collections/%s/%s" % (name, e["file"]) if e.get("file") else None,
                                 "aspects": {a: v.get("d") for a, v in (e.get("profile") or {}).items() if v.get("d")},
                                 "base_src": "/outputs/collections/%s/%s" % (name, e["base"]) if e.get("base") else None,
                                 "base_cycle": int(e["base"].split("/c")[1][:4]) if str(e.get("base") or "").startswith("renders/c") else None,
                                 "d_champ": (e.get("measured") or {}).get("d_champ"),
                                 "note": next((v.get("why") for v in (e.get("profile") or {}).values() if v.get("why")), "")})
            except Exception:
                pass
            try:
                marks = json.loads((COLLECTIONS / name / "marks.json").read_text())
            except Exception:
                marks = {}
            try:
                scores = json.loads((COLLECTIONS / name / "scores.json").read_text())
            except Exception:
                scores = {}
            for r in rows:
                r["mark"] = marks.get(str(r["cycle"]), 0)
                r["scores"] = scores.get(str(r["cycle"]), {})
            try:
                removed = set(json.loads((COLLECTIONS / name / "removed.json").read_text()))
            except Exception:
                removed = set()
            show_removed = q.get("removed", ["0"])[0] == "1"
            for r in rows:
                r["removed"] = r["cycle"] in removed
            if not show_removed:
                rows = [r for r in rows if not r["removed"]]
            champions = [r for r in rows if r["src"] and (r["mark"] > 0 or (r["mark"] == 0 and str(r["outcome"] or "").startswith(("accepted", "first champion"))))]
            return self.send({"name": name, "champions": champions, "recent": rows[-limit:][::-1], "total": len(rows)})
        if path == "/api/collection":
            try:
                cur = json.loads(COLL.read_text())
            except Exception:
                cur = {}
            return self.send({"active": slim(cur), "collections": collections_list()})
        if path == "/api/judging":
            try:
                return self.send(json.loads(JUDGING.read_text()))
            except Exception:
                return self.send({"mode": "wall"})
        if path != "/api/prompt":
            return self.send({"error": "not found"}, 404)
        self.send(load())

    def do_POST(self):
        path = urlparse(self.path).path.rstrip("/")
        try:
            b = self.body()
        except Exception:
            return self.send({"error": "bad json"}, 400)
        if path == "/api/collection":
            act = b.get("action")
            if act == "stop":
                try:
                    cur = json.loads(COLL.read_text())
                except Exception:
                    cur = {}
                cur["active"] = False
                COLL.write_text(json.dumps(cur, indent=1))
                return self.send({"active": slim(cur), "collections": collections_list()})
            if act != "start":
                return self.send({"error": "action must be start or stop"}, 400)
            subject = str(b.get("subject") or "").strip()
            name = slug(str(b.get("name") or subject))
            if not subject:
                return self.send({"error": "subject required"}, 400)
            saved = COLLECTIONS / name / "collection.json"
            try:
                cur = json.loads(saved.read_text()) if saved.exists() else {}
            except Exception:
                cur = {}
            if cur.get("subject") and cur["subject"] != subject:
                return self.send({"error": "collection '%s' already holds the subject '%s'" % (name, cur["subject"])}, 409)
            cur.update({"name": name, "subject": subject, "active": True, "preset": str(b.get("preset") or cur.get("preset") or "hero")})
            cur.setdefault("prompt", subject); cur.setdefault("guidance", 3.5); cur.setdefault("generation", 0)
            cur.setdefault("seed", __import__("random").randrange(1, 2**31 - 1))   # the first champion's seed; challengers reuse the champion's
            cur.setdefault("started", time.time())
            (COLLECTIONS / name).mkdir(parents=True, exist_ok=True)
            saved.write_text(json.dumps(cur, indent=1))
            COLL.write_text(json.dumps(cur, indent=1))
            return self.send({"active": slim(cur), "collections": collections_list()})
        if path == "/api/collection/config":
            try:
                cur = json.loads(STUDY_CFG.read_text())
            except Exception:
                cur = {}
            for k in ("weights", "gain", "min_change", "bold_after", "revision_after", "reseed_after", "judge_calls", "size", "steps", "vision", "writer"):
                if k in b:
                    cur[k] = b[k]
            cur["updated_at"] = time.time()
            tmp = STUDY_CFG.with_suffix(".tmp"); tmp.write_text(json.dumps(cur, indent=1)); os.replace(tmp, STUDY_CFG)
            return self.send({"config": cur})
        if path == "/api/saved-prompt":
            text = str(b.get("prompt") or "").strip()
            if not text:
                return self.send({"error": "empty prompt"}, 400)
            try:
                cur = json.loads(SAVED.read_text())
            except Exception:
                cur = {"current": "", "versions": []}
            if not cur["versions"] or cur["versions"][-1]["prompt"] != text:
                cur["versions"].append({"prompt": text, "at": time.time()})
            cur["current"] = text
            SAVED.write_text(json.dumps(cur, indent=1))
            renders = str(b.get("renders") or "")
            if renders.strip():                   # the running stream switches to exactly this text for its next frame
                tmp = LIVE.with_suffix(".tmp")
                tmp.write_text(json.dumps({"prompt": renders, "at": time.time()}))
                os.replace(tmp, LIVE)
            return self.send(cur)
        if path == "/api/collection/remove":       # take a photo out of a collection (kept on disk; reversible)
            name, cyc, rm = slug(str(b.get("name") or "")), int(b.get("cycle") or 0), bool(b.get("removed", True))
            if not name or not cyc:
                return self.send({"error": "name and cycle required"}, 400)
            rp = COLLECTIONS / name / "removed.json"
            try:
                cur = set(json.loads(rp.read_text()))
            except Exception:
                cur = set()
            (cur.add if rm else cur.discard)(cyc)
            tmp = rp.with_suffix(".tmp"); tmp.write_text(json.dumps(sorted(cur))); os.replace(tmp, rp)
            try:                                      # a removed frame is no longer staged for influx.pictures
                import re as _re
                for line in (COLLECTIONS / name / "history.jsonl").read_text().splitlines():
                    e = json.loads(line)
                    if e.get("cycle") == cyc and e.get("file") and rm:
                        ino = (COLLECTIONS / name / e["file"]).stat().st_ino
                        for f in Path("/home/ubuntu/Models/flux-output/influx-outputs").glob("protocol-*.png"):
                            if f.stat().st_ino == ino:
                                f.unlink()
            except Exception:
                pass
            return self.send({"ok": True, "removed": sorted(cur)})
        if path == "/api/collection/score":        # the teacher's difference and uniqueness scores (beauty is /mark)
            name, cyc, dim = slug(str(b.get("name") or "")), int(b.get("cycle") or 0), str(b.get("dim") or "")
            val = int(b.get("value") or 0)
            if not name or not cyc or dim not in ("direction", "difference", "uniqueness") or not -20 <= val <= 20:
                return self.send({"error": "name, cycle, dim (difference|uniqueness) and value (-20..20) required"}, 400)
            sp = COLLECTIONS / name / "scores.json"
            try:
                sc = json.loads(sp.read_text())
            except Exception:
                sc = {}
            row = sc.setdefault(str(cyc), {})
            if val:
                row[dim] = val
            else:
                row.pop(dim, None)
            if not row:
                sc.pop(str(cyc), None)
            tmp = sp.with_suffix(".tmp"); tmp.write_text(json.dumps(sc)); os.replace(tmp, sp)
            return self.send({"ok": True, "scores": sc.get(str(cyc), {})})
        if path == "/api/collection/mark":
            name, cyc, mk = slug(str(b.get("name") or "")), int(b.get("cycle") or 0), int(b.get("mark") or 0)
            if not name or not cyc or not -20 <= mk <= 20:
                return self.send({"error": "name, cycle and mark (a score, -20..20) required"}, 400)
            marks_p = COLLECTIONS / name / "marks.json"
            try:
                marks = json.loads(marks_p.read_text())
            except Exception:
                marks = {}
            if mk:
                marks[str(cyc)] = mk
            else:
                marks.pop(str(cyc), None)
            marks_p.write_text(json.dumps(marks))
            (COLL.parent / "mark.json").write_text(json.dumps({"name": name, "cycle": cyc, "mark": mk, "at": time.time()}))
            return self.send({"ok": True, "marks": marks})
        if path == "/api/judging":
            m = str(b.get("mode", ""))
            if m not in ("wall", "off"):
                return self.send({"error": "mode must be wall or off"}, 400)
            JUDGING.write_text(json.dumps({"mode": m, "at": time.time()}))
            return self.send({"mode": m})
        with LOCK:
            s = load()
            if path == "/api/prompt":
                ps = [p.strip() for p in b.get("prompts", []) if str(p).strip()]
                if not ps:
                    return self.send({"error": "at least one prompt"}, 400)
                s["prompts"] = ps
            elif path == "/api/prompt/direction":
                t = str(b.get("text", "")).strip()
                if not t:
                    return self.send({"error": "empty direction"}, 400)
                s["directions"].append({"id": uuid.uuid4().hex[:8], "text": t, "at": time.time()})
            else:
                return self.send({"error": "not found"}, 404)
            s["updated_at"] = time.time()
            save(compose(s))
        self.send(s)

    def do_DELETE(self):
        u = urlparse(self.path)
        if u.path.rstrip("/") != "/api/prompt/direction":
            return self.send({"error": "not found"}, 404)
        i = parse_qs(u.query).get("id", [""])[0]
        with LOCK:
            s = load()
            s["directions"] = [d for d in s["directions"] if d["id"] != i]
            s["updated_at"] = time.time()
            save(compose(s))
        self.send(s)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    if not FILE.exists():
        save(load())
    ThreadingHTTPServer(("127.0.0.1", 8096), H).serve_forever()
