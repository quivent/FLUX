from __future__ import annotations

import html
import json
import mimetypes
import os
import pathlib
import socket
import time
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


ADDR = os.environ.get("GALLERY_ADDR", "127.0.0.1:7870")
FLUX_OUTPUT_DIR = pathlib.Path(os.environ.get("FLUX_OUTPUT_DIR", "/runs/flux-output"))
FLUX_STATE = pathlib.Path(os.environ.get("FLUX_STATE", "/opt/FLUX/.fluxd/jobs.jsonl"))
WAN_OUTPUT_DIR = pathlib.Path(os.environ.get("WAN_OUTPUT_DIR", "/runs/wan/outputs"))
WAN_STATE_DIR = pathlib.Path(os.environ.get("WAN_STATE_DIR", "/runs/wan/.wand"))
MEDIA_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".webm", ".mov", ".mkv"}


def split_addr(value: str) -> tuple[str, int]:
    host, raw_port = value.rsplit(":", 1)
    return host, int(raw_port)


def port_open(host: str, port: int, timeout: float = 0.35) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def read_json(path: pathlib.Path) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return {}


def flux_jobs(limit: int = 80) -> list[dict[str, Any]]:
    if not FLUX_STATE.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in FLUX_STATE.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]:
        try:
            job = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(job, dict):
            job["kind"] = "flux"
            rows.append(job)
    return list(reversed(rows))


def wan_jobs(limit: int = 80) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    for state in ("running", "queue", "done", "failed"):
        root = WAN_STATE_DIR / state
        for path in sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
            job = read_json(path)
            if not job:
                continue
            job_id = str(job.get("job_id") or path.stem)
            manifest = read_json(pathlib.Path(str(job.get("output_dir") or WAN_OUTPUT_DIR)) / job_id / "manifest.json")
            job = {**job, **manifest}
            job["kind"] = "wan"
            job["state"] = state
            job["id"] = job.get("job_id") or job_id
            jobs.append(job)
    order = {"running": 0, "queue": 1, "done": 2, "failed": 3}
    return sorted(
        jobs,
        key=lambda j: (order.get(str(j.get("state")), 9), -(parse_time(j.get("started_at") or j.get("created_at") or j.get("finished_at")) or 0)),
    )[:limit]


def media_kind(path: pathlib.Path) -> str:
    return "video" if path.suffix.lower() in {".mp4", ".webm", ".mov", ".mkv"} else "image"


def media_url(kind: str, path: pathlib.Path, root: pathlib.Path) -> str:
    rel = path.relative_to(root)
    return "/media/" + kind + "/" + "/".join(urllib.parse.quote(part) for part in rel.parts)


def scan_media(kind: str, root: pathlib.Path, limit: int = 120) -> list[dict[str, Any]]:
    if not root.is_dir():
        return []
    files = [
        p for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in MEDIA_EXTS
    ]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    out: list[dict[str, Any]] = []
    for path in files[:limit]:
        stat = path.stat()
        out.append({
            "kind": kind,
            "media_type": media_kind(path),
            "name": path.name,
            "path": str(path),
            "url": media_url(kind, path, root),
            "bytes": stat.st_size,
            "mtime": stat.st_mtime,
        })
    return out


def counts(jobs: list[dict[str, Any]]) -> dict[str, int]:
    out = {"queued": 0, "running": 0, "done": 0, "failed": 0, "error": 0}
    for job in jobs:
        state = str(job.get("state") or job.get("status") or "").lower()
        if state in out:
            out[state] += 1
        elif state == "queue":
            out["queued"] += 1
    return out


def parse_time(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def job_seconds(job: dict[str, Any]) -> float | None:
    raw = job.get("seconds")
    if isinstance(raw, (int, float)) and raw > 0:
        return float(raw)
    started = parse_time(job.get("started_at") or job.get("created_at"))
    finished = parse_time(job.get("finished_at"))
    if started and finished and finished > started:
        return finished - started
    return None


def duration_profile(jobs: list[dict[str, Any]], fallback: float) -> float:
    samples = [
        seconds for job in jobs
        for seconds in [job_seconds(job)]
        if seconds and seconds > 0 and str(job.get("state") or job.get("status") or "").lower() in {"done", "completed"}
    ]
    if not samples:
        return fallback
    samples = sorted(samples[-12:])
    return samples[len(samples) // 2]


def pipeline(kind: str, jobs: list[dict[str, Any]], fallback_seconds: float) -> list[dict[str, Any]]:
    estimate = duration_profile(jobs, fallback_seconds)
    now = datetime.now(timezone.utc).timestamp()
    queued_ahead = 0.0
    rows: list[dict[str, Any]] = []
    for index, job in enumerate(jobs):
        state = str(job.get("state") or job.get("status") or "").lower()
        if state not in {"queue", "queued", "running"}:
            continue
        started = parse_time(job.get("started_at") or job.get("created_at"))
        elapsed = max(0.0, now - started) if started and state == "running" else 0.0
        eta = max(0.0, estimate - elapsed) if state == "running" else queued_ahead + estimate
        if state in {"queue", "queued"}:
            queued_ahead += estimate
        rows.append({
            "kind": kind,
            "position": len(rows) + 1,
            "id": job.get("id") or job.get("job_id") or f"{kind}-{index + 1}",
            "state": state,
            "prompt": job.get("prompt") or job.get("error") or "",
            "task": job.get("task") or kind,
            "size": job.get("size") or "",
            "seed": job.get("seed"),
            "estimate_seconds": round(estimate, 1),
            "eta_seconds": round(eta, 1),
            "elapsed_seconds": round(elapsed, 1),
        })
    return rows[:40]


def snapshot() -> dict[str, Any]:
    flux = flux_jobs()
    wan = wan_jobs()
    return {
        "ok": True,
        "generated_at": time.time(),
        "health": {
            "flux": {
                "ok": FLUX_OUTPUT_DIR.is_dir() and FLUX_STATE.exists(),
                "gallery": port_open("127.0.0.1", 7861),
                "output_dir": str(FLUX_OUTPUT_DIR),
                "state": str(FLUX_STATE),
                "counts": counts(flux),
            },
            "wan": {
                "ok": WAN_OUTPUT_DIR.is_dir() and WAN_STATE_DIR.is_dir(),
                "gallery": port_open("127.0.0.1", 8792),
                "worker": bool(list((WAN_STATE_DIR / "queue").glob("*.json")) or WAN_STATE_DIR.is_dir()),
                "output_dir": str(WAN_OUTPUT_DIR),
                "state_dir": str(WAN_STATE_DIR),
                "defaults": {
                    "task": "t2v-A14B",
                    "size": "1280x720",
                    "gpus": 1,
                    "precision": "bf16 model config, bf16 T5 checkpoint, convert_model_dtype=true",
                    "offload_model": True,
                },
                "counts": counts(wan),
            },
        },
        "flux": {"jobs": flux, "pipeline": pipeline("flux", flux, 18.0), "media": scan_media("flux", FLUX_OUTPUT_DIR)},
        "wan": {"jobs": wan, "pipeline": pipeline("wan", wan, 900.0), "media": scan_media("wan", WAN_OUTPUT_DIR)},
    }


def page() -> bytes:
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>gallery.influx.vision</title>
<style>
:root{{--bg:#080a10;--panel:#101521;--line:rgba(232,238,246,.14);--text:#edf2f8;--muted:#9da9b8;--gold:#ffd16a;--cyan:#64d7ff;--pink:#ff8ab3;--green:#85f7b5;--red:#ff647d}}
*{{box-sizing:border-box}}body{{margin:0;background:#080a10;color:var(--text);font:14px/1.45 Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}a{{color:inherit}}
main{{max-width:1640px;margin:0 auto;padding:22px clamp(16px,3vw,42px) 42px}}.top{{display:flex;align-items:end;justify-content:space-between;gap:18px;border-bottom:1px solid var(--line);padding-bottom:16px}}.mark{{color:var(--cyan);font:800 12px/1 ui-monospace,SFMono-Regular,Menlo,monospace;text-transform:uppercase}}h1{{font-size:clamp(34px,5vw,72px);line-height:.92;margin:8px 0 0;font-weight:850}}.sub{{color:var(--muted);max-width:820px;margin-top:8px}}.health{{display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end}}.pill,.tab{{border:1px solid var(--line);border-radius:7px;background:rgba(16,21,33,.78);padding:8px 10px;color:var(--muted)}}.dot{{display:inline-block;width:9px;height:9px;border-radius:50%;background:var(--red);margin-right:7px;box-shadow:0 0 16px rgba(255,100,125,.5)}}.dot.ok{{background:var(--green);box-shadow:0 0 16px rgba(133,247,181,.35)}}.tabs{{display:flex;gap:8px;margin:18px 0 14px}}button.tab{{cursor:pointer;font-weight:800}}button.tab.active{{border-color:rgba(100,215,255,.48);color:var(--cyan);box-shadow:0 0 24px rgba(100,215,255,.10)}}.stats{{display:grid;grid-template-columns:repeat(5,minmax(110px,1fr));gap:10px;margin-bottom:14px}}.stat{{border:1px solid var(--line);border-radius:8px;background:rgba(16,21,33,.72);padding:12px}}.stat b{{display:block;color:var(--gold);font-size:24px}}.stat span{{display:block;color:var(--muted);font:800 10px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace;text-transform:uppercase;margin-top:5px}}.pipeline{{display:grid;gap:8px;margin-bottom:14px}}.pipe{{display:grid;grid-template-columns:42px minmax(0,1fr) auto;gap:10px;align-items:center;border:1px solid var(--line);border-radius:8px;background:rgba(16,21,33,.58);padding:9px 10px}}.pipe i{{font-style:normal;color:var(--cyan);font:900 12px/1 ui-monospace,SFMono-Regular,Menlo,monospace}}.pipe b{{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}.pipe span{{display:block;color:var(--muted);font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}.eta{{color:var(--gold);font:900 11px/1 ui-monospace,SFMono-Regular,Menlo,monospace;text-transform:uppercase}}.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(290px,1fr));gap:12px}}.card{{border:1px solid var(--line);border-radius:8px;background:rgba(16,21,33,.76);overflow:hidden;min-width:0}}.preview{{aspect-ratio:16/9;background:#050711;display:grid;place-items:center;color:var(--muted)}}.preview img,.preview video{{width:100%;height:100%;object-fit:cover;display:block}}.body{{padding:12px}}.row{{display:flex;align-items:center;justify-content:space-between;gap:10px}}.row b{{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}.state{{color:var(--gold);border:1px solid rgba(255,209,106,.22);border-radius:999px;padding:4px 7px;font:800 10px/1 ui-monospace,SFMono-Regular,Menlo,monospace;text-transform:uppercase}}.prompt{{margin-top:9px;color:var(--muted);display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden;min-height:58px}}.facts{{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px}}.facts span{{color:var(--cyan);border:1px solid rgba(100,215,255,.16);border-radius:999px;padding:4px 7px;font:800 10px/1 ui-monospace,SFMono-Regular,Menlo,monospace}}.empty{{border:1px dashed var(--line);border-radius:8px;color:var(--muted);padding:24px;text-align:center;background:rgba(16,21,33,.44)}}@media(max-width:760px){{.top{{display:block}}.health{{justify-content:flex-start;margin-top:14px}}.stats{{grid-template-columns:repeat(2,minmax(0,1fr))}}.pipe{{grid-template-columns:32px minmax(0,1fr)}}.eta{{grid-column:2}}}}
</style>
</head>
<body>
<main>
<div class="top"><div><div class="mark">gallery.influx.vision</div><h1>Render gallery</h1><div class="sub">Unified FLUX image archive and WAN video queue for the H200 render host.</div></div><div id="health" class="health"></div></div>
<div class="tabs"><button class="tab active" data-tab="flux">FLUX</button><button class="tab" data-tab="wan">WAN</button></div>
<section class="stats" id="stats"></section>
<section class="pipeline" id="pipeline"></section>
<section class="grid" id="grid"><div class="empty">Loading gallery</div></section>
</main>
<script>
let data=null, tab='flux';
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
const fmtBytes=n=>n?((n/1024/1024).toFixed(1)+' MB'):'';
const fmtTime=s=>{{s=Number(s||0);if(s<60)return Math.round(s)+'s';if(s<3600)return Math.round(s/60)+'m';return (s/3600).toFixed(1)+'h'}};
function healthPill(name,h){{return '<span class="pill"><i class="dot '+(h?.ok?'ok':'')+'"></i>'+esc(name)+' '+(h?.ok?'ok':'down')+'</span>'}}
function renderHealth(){{document.getElementById('health').innerHTML=healthPill('FLUX',data?.health?.flux)+healthPill('WAN',data?.health?.wan)}}
function renderStats(){{const h=data?.health?.[tab]||{{}}, m=data?.[tab]?.media||[], c=h.counts||{{}};document.getElementById('stats').innerHTML=['media '+m.length,'running '+(c.running||0),'queued '+(c.queued||c.queue||0),'done '+(c.done||0),'failed '+((c.failed||0)+(c.error||0))].map(x=>{{const p=x.split(' ');return '<div class="stat"><b>'+esc(p[1])+'</b><span>'+esc(p[0])+'</span></div>'}}).join('')}}
function renderPipeline(){{const rows=data?.[tab]?.pipeline||[];document.getElementById('pipeline').innerHTML=rows.map(j=>'<div class="pipe"><i>#'+esc(j.position)+'</i><div><b>'+esc(j.id)+'</b><span>'+esc(j.state)+' · '+esc(j.task)+' · '+esc(j.size)+' · '+esc(j.prompt)+'</span></div><div class="eta">'+esc(j.state==='running'?'ETA ':'~')+esc(fmtTime(j.eta_seconds))+'</div></div>').join('')||'<div class="empty">No queued or running '+esc(tab.toUpperCase())+' jobs</div>'}}
function mediaCard(m){{const p=m.media_type==='video'?'<video src="'+esc(m.url)+'" controls muted loop playsinline></video>':'<img src="'+esc(m.url)+'" alt="">';return '<article class="card"><a class="preview" href="'+esc(m.url)+'" target="_blank" rel="noreferrer">'+p+'</a><div class="body"><div class="row"><b title="'+esc(m.name)+'">'+esc(m.name)+'</b><span class="state">'+esc(m.kind)+'</span></div><div class="facts"><span>'+esc(m.media_type)+'</span><span>'+esc(fmtBytes(m.bytes))+'</span></div></div></article>'}}
function jobCard(j){{const id=j.id||j.job_id||'job', state=j.state||j.status||'job';return '<article class="card"><div class="preview">No media yet</div><div class="body"><div class="row"><b>'+esc(id)+'</b><span class="state">'+esc(state)+'</span></div><div class="prompt">'+esc(j.prompt||j.error||'')+'</div><div class="facts"><span>'+esc(j.task||tab)+'</span><span>'+esc(j.size||'')+'</span><span>'+esc(j.seed!==undefined&&j.seed!==null?'seed '+j.seed:'')+'</span></div></div></article>'}}
function renderGrid(){{const media=data?.[tab]?.media||[], jobs=(data?.[tab]?.jobs||[]).filter(j=>['running','queue','queued','failed','error'].includes(String(j.state||j.status||'').toLowerCase())).slice(0,24);document.getElementById('grid').innerHTML=[...jobs.map(jobCard),...media.map(mediaCard)].join('')||'<div class="empty">No '+esc(tab.toUpperCase())+' outputs yet</div>'}}
function render(){{if(!data)return;renderHealth();renderStats();renderPipeline();renderGrid()}}
document.querySelectorAll('.tab').forEach(b=>b.onclick=()=>{{tab=b.dataset.tab;document.querySelectorAll('.tab').forEach(x=>x.classList.toggle('active',x===b));render()}});
async function load(){{const r=await fetch('/api/snapshot');data=await r.json();render()}}
if(window.EventSource){{const es=new EventSource('/api/events');es.addEventListener('snapshot',ev=>{{data=JSON.parse(ev.data);render()}});es.onerror=()=>setTimeout(load,1000)}}else setInterval(load,2000);
load();
</script>
</body>
</html>""".encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.address_string()} - {fmt % args}")

    def send_json(self, body: dict[str, Any]) -> None:
        data = json.dumps(body, sort_keys=True).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_HEAD(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in {"/", "/gallery", "/health", "/api/health", "/api/snapshot"}:
            self.send_response(200)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        self.send_error(404, html.escape(parsed.path))

    def send_media(self, kind: str, rel: str) -> None:
        root = FLUX_OUTPUT_DIR if kind == "flux" else WAN_OUTPUT_DIR
        target = (root / urllib.parse.unquote(rel)).resolve()
        try:
            target.relative_to(root.resolve())
        except ValueError:
            self.send_error(400)
            return
        if not target.is_file():
            self.send_error(404)
            return
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(target.stat().st_size))
        self.end_headers()
        with target.open("rb") as fh:
            while chunk := fh.read(1024 * 1024):
                self.wfile.write(chunk)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in {"/", "/gallery"}:
            data = page()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if parsed.path in {"/health", "/api/health", "/api/snapshot"}:
            self.send_json(snapshot())
            return
        if parsed.path == "/api/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            while True:
                payload = json.dumps(snapshot(), sort_keys=True)
                self.wfile.write(f"event: snapshot\ndata: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()
                time.sleep(2)
        if parsed.path.startswith("/media/flux/"):
            self.send_media("flux", parsed.path[len("/media/flux/"):])
            return
        if parsed.path.startswith("/media/wan/"):
            self.send_media("wan", parsed.path[len("/media/wan/"):])
            return
        self.send_error(404, html.escape(parsed.path))


if __name__ == "__main__":
    host, port = split_addr(ADDR)
    ThreadingHTTPServer((host, port), Handler).serve_forever()
