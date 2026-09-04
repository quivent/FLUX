#!/usr/bin/env python3
"""FLUX beauty-protocol streamer.

Submits unique-seed renders at 18 or 28 steps, keeps a shallow queue against
the resident worker, and writes a status file the /protocol page polls.
``--n 0`` means no frame cap. Each settled job is judged by moj_evaluator.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(ROOT, ".fluxd", "protocol_stream.json")
API = os.environ.get("FLUX_HTTP", "http://127.0.0.1:7861")
FASHION_PROMPT = (
    "The most extravagant fashion models in the most unique and exquisite dresses "
    "ever made, of all shapes and sizes and colors, the new Fashion beauty on beauty"
)
STILL_LIFE_PROMPT = (
    "a celadon tea bowl with one gold kintsugi seam on handmade washi, "
    "north window light, quiet still life, off-centre, reserved highlights, "
    "one accent against a restrained ground"
)
# Jury :8001 SIGN then AMEND 2026-09-04. CLIP-L 68 tokens; extra element in-window.
# Not princess, not rose, not celadon. Hive Qwen :8000 dumped CoT — ignored.
ARCANE_PROMPT = (
    "Fortiche style animation still, holding a rusted bio-luminescent mechanical "
    "lung, gaunt Zaunite scavenger, severe unique beauty, angular scarred face, "
    "oil paint impasto over 3D sculpt, graphic painted rim, gouache, chiaroscuro, "
    "neon chemtech glow, industrial smog, not CGI"
)
ARCANE_EXTRA = "mechanical lung"
ARCANE_BANNED = (
    "princess",
    "rose",
    "celadon",
    "tea bowl",
    "kintsugi",
    "disney",
    "supermodel",
)
CLIP_TOKENIZER = os.path.expanduser("~/models/FLUX.1-dev/tokenizer")
DEFAULT_PROMPT = FASHION_PROMPT

try:
    import belarro_direction
except ImportError:
    belarro_direction = None
EVAL_PATH = [
    "generate",
    "uniqueness",
    "sensory_gates",
    "witness",
    "pixtral",
    "governor",
    "composite",
]
MAX_PROTOCOL_BRANCHES = 3
RESERVED_BRANCHES = frozenset(
    {
        "fashion",
        "arcane",
        "portraits",
        "atlas",
        "gallery",
        "protocol",
        "images",
        "movement",
        "exhibition",
        "studies",
        "stallion",
        "tea",
        "index",
        "assets",
        "api",
        "batches",
        "trash",
        "garden",
        "engine",
        "judge",
        "jury",
        "sentinel",
        "rig",
        "domains",
        "stream",
        "gpu3",
        "fp8",
        "celadon",
        "still-life",
        "still_life",
    }
)


def normalize_branch(name):
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    if not slug or len(slug) > 32:
        raise ValueError("branch name must be 1-32 letters, numbers, or hyphens")
    if slug in RESERVED_BRANCHES:
        raise ValueError("branch %r is reserved; pick a new collection name" % slug)
    return slug


def branch_relpath(branch, stream_id, index):
    return "collections/%s/protocol-%s-%s-%03d.png" % (branch, branch, stream_id, index)


def branch_state_path(root, branch):
    return os.path.join(root, ".fluxd", "protocol_stream_branch_%s.json" % branch)


def running_branch_slugs(root, ignore_state=""):
    fluxd = os.path.join(root, ".fluxd")
    slugs = set()
    ignore = os.path.abspath(ignore_state) if ignore_state else ""
    try:
        names = os.listdir(fluxd)
    except OSError:
        return slugs
    for name in names:
        if not name.startswith("protocol_stream_branch_") or not name.endswith(".json"):
            continue
        path = os.path.join(fluxd, name)
        if ignore and os.path.abspath(path) == ignore:
            continue
        try:
            with open(path) as handle:
                payload = json.load(handle)
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("status") != "running":
            continue
        slug = (payload.get("branch") or "").strip()
        if slug:
            slugs.add(slug)
    return slugs


def load_state(path=None):
    path = path or STATE_PATH
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state, path=None):
    path = path or STATE_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def sock_request(sock_path, payload, timeout=30):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout)
        client.connect(sock_path)
        client.sendall((json.dumps(payload) + "\n").encode())
        data = b""
        while not data.endswith(b"\n"):
            chunk = client.recv(65536)
            if not chunk:
                break
            data += chunk
    if not data:
        raise RuntimeError("empty socket response from %s" % sock_path)
    return json.loads(data.decode())


def get_json(path, timeout=8):
    req = urllib.request.Request(API + path, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return json.loads(res.read().decode())


def clip_holds_extra(prompt, needle=ARCANE_EXTRA):
    needle = needle.lower()
    try:
        from transformers import CLIPTokenizer

        tok = CLIPTokenizer.from_pretrained(CLIP_TOKENIZER)
        trunc = tok(prompt, add_special_tokens=True, truncation=True, max_length=77)["input_ids"]
        kept = tok.decode(trunc, skip_special_tokens=True).lower()
        return needle in kept
    except Exception:
        return needle in (prompt or "")[:220].lower()


def refuse_banned(prompt, arcane=False):
    pl = (prompt or "").lower()
    if "celadon tea bowl" in pl or "kintsugi seam" in pl:
        raise SystemExit("still-life / celadon tea-bowl stream is stopped")
    if not arcane:
        return
    for word in ARCANE_BANNED:
        if word in pl:
            raise SystemExit("arcane lane refuses %r (Fortiche World Forge, not vanity)" % word)
    if ARCANE_EXTRA not in pl:
        raise SystemExit("arcane lane requires extra element %r inside the prompt" % ARCANE_EXTRA)
    if not clip_holds_extra(prompt):
        raise SystemExit("arcane extra element is not inside the CLIP-L 77-token window")


def post_json(path, body, timeout=30):
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        API + path,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return json.loads(res.read().decode())


def audit_stats(output_dir):
    path = os.path.join(output_dir, "audit.jsonl")
    evaluated = spectacles = unscored = 0
    if not os.path.isfile(path):
        return evaluated, spectacles, unscored
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            evaluated += 1
            tier = rec.get("tier")
            if tier == "spectacle" or rec.get("is_spectacle"):
                spectacles += 1
            if rec.get("masterpiece") or tier == "masterpiece":
                spectacles += 1
            if tier == "unscored" or rec.get("composite") is None:
                unscored += 1
    return evaluated, spectacles, unscored


def active_jobs(jobs):
    return [j for j in jobs if j.get("status") in ("queued", "running")]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--n", type=int, default=0, help="frame cap; 0 = no cap")
    p.add_argument("--steps", type=int, default=28)
    p.add_argument("--prompt", default=DEFAULT_PROMPT)
    p.add_argument("--still-life", action="store_true", help="use the celadon kintsugi still-life prompt")
    p.add_argument("--arcane", action="store_true", help="Arcane Fortiche animation still + jury-signed extra element")
    p.add_argument("--depth", type=int, default=2)
    p.add_argument("--width", type=int, default=1024)
    p.add_argument("--height", type=int, default=1024)
    p.add_argument("--guidance", type=float, default=3.5)
    p.add_argument("--socket", default="", help="submit to this UDS worker instead of HTTP /api/render")
    p.add_argument("--state", default="", help="status JSON path (default .fluxd/protocol_stream.json)")
    p.add_argument("--lane", default="", help="label stored on the status file (gpu0-bf16 / gpu3-fp8)")
    p.add_argument("--branch", default="", help="independent protocol branch; writes under collections/<name>/")
    args = p.parse_args()
    sock_path = args.socket or ""
    pinned = "flux-gpu3.sock" in sock_path
    if args.still_life:
        raise SystemExit("still-life / celadon tea-bowl stream is stopped")
    branch = ""
    if args.branch:
        try:
            branch = normalize_branch(args.branch)
        except ValueError as exc:
            raise SystemExit(str(exc))
        args.lane = branch
        args.arcane = False
    elif pinned:
        if args.arcane or (args.lane or "").lower() == "arcane":
            raise SystemExit("fashion lock: --arcane is refused on GPU 0/3")
        args.prompt = FASHION_PROMPT
        args.lane = "fashion"
        args.arcane = False
    elif args.arcane or (args.lane or "").lower() == "arcane":
        raise SystemExit("fashion lock: arcane lane is refused")
    refuse_banned(args.prompt, arcane=False)
    state_path = args.state or (branch_state_path(ROOT, branch) if branch else STATE_PATH)
    if branch:
        live = running_branch_slugs(ROOT, ignore_state=state_path)
        live.discard(branch)
        if len(live) >= MAX_PROTOCOL_BRANCHES:
            raise SystemExit("already running %d protocol branches (%s); stop one first" % (
                len(live), ", ".join(sorted(live))))

    output_dir = os.environ.get("OUT_DIR") or os.environ.get("FLUX_OUTPUT_DIR") or os.path.expanduser("~/models/flux-output")
    def harvest_n(rec):
        raw = rec.get("n") if isinstance(rec, dict) and rec.get("n") is not None else args.n
        try:
            return int(raw)
        except (TypeError, ValueError):
            return int(args.n or 0)

    def still_open(rec):
        n = harvest_n(rec)
        if n <= 0:
            return True
        return int(rec.get("submitted") or 0) < n

    prev = load_state(state_path)
    resume = (
        isinstance(prev, dict)
        and prev.get("prompt") == args.prompt
        and int(prev.get("steps") or 0) == int(args.steps)
        and int(prev.get("width") or 0) == int(args.width)
        and int(prev.get("height") or 0) == int(args.height)
        and (not branch or prev.get("branch") == branch)
        and prev.get("status") in ("running", "error", "stopped")
        and (args.n <= 0 or int(prev.get("submitted") or 0) < args.n)
    )
    if resume:
        state = prev
        state["status"] = "running"
        state["error"] = ""
        state["n"] = args.n
        state["updated_at"] = time.time()
        state.setdefault("job_ids", [])
    else:
        state = {
            "id": time.strftime("stream-%Y%m%d-%H%M%S"),
            "status": "running",
            "n": args.n,
            "steps": args.steps,
            "width": args.width,
            "height": args.height,
            "guidance": args.guidance,
            "prompt": args.prompt,
            "eval_path": EVAL_PATH,
            "submitted": 0,
            "done": 0,
            "running": 0,
            "evaluated": 0,
            "spectacles": 0,
            "unscored": 0,
            "job_ids": [],
            "error": "",
            "started_at": time.time(),
            "updated_at": time.time(),
        }
    if args.lane:
        state["lane"] = args.lane
    if sock_path:
        state["socket"] = sock_path
    if branch:
        state["branch"] = branch
        state["collection"] = "collections/" + branch
        state["wall"] = "/collections/" + branch
    if args.arcane:
        state["overseer"] = "jury:8001"
        state["realm"] = "Zaun"
        state["extra"] = "rusted bio-luminescent mechanical lung"
        state["verdict"] = "SIGN"
    save_state(state, state_path)

    try:
        while still_open(state) or state["done"] < state["submitted"]:
            if sock_path:
                jobs = sock_request(sock_path, {"op": "jobs"}).get("jobs") or []
            else:
                jobs = (get_json("/api/jobs").get("jobs") or [])
            ours = {jid for jid in state["job_ids"]}
            mine = [j for j in jobs if j.get("id") in ours]
            state["done"] = sum(1 for j in mine if j.get("status") in ("done", "error", "cancelled"))
            state["running"] = len(active_jobs(mine))
            audit_dir = os.path.join(output_dir, "collections", branch) if branch else output_dir
            ev, sp, un = audit_stats(audit_dir)
            state["evaluated"] = ev
            state["spectacles"] = sp
            state["unscored"] = un

            if still_open(state) and state["running"] < args.depth:
                tag = (args.lane or "stream").replace("/", "-")
                prompt = args.prompt
                guidance = args.guidance
                steps = args.steps
                width = args.width
                height = args.height
                seed = str(int(time.time() * 1000) % 2147483647 + state["submitted"])
                if branch == "microgreens" and belarro_direction is not None:
                    study = belarro_direction.load_config()
                    prompt = belarro_direction.prompt_for(state["submitted"], study)
                    guidance = float(study.get("guidance") or args.guidance)
                    steps = int(study.get("steps") or args.steps)
                    width = int(study.get("width") or args.width)
                    height = int(study.get("height") or args.height)
                    if str(study.get("seed") or "random") == "random":
                        seed = str(int.from_bytes(os.urandom(4), "big") % 2147483647)
                    state["prompt"] = prompt
                    state["direction"] = "belarro"
                    state["guidance"] = guidance
                    state["steps"] = steps
                    state["width"] = width
                    state["height"] = height
                    vars_ = study.get("_varieties") or belarro_direction.VARIETIES
                    state["variety"] = vars_[state["submitted"] % len(vars_)][1]
                    depth = int(study.get("depth") or args.depth)
                    if depth >= 1:
                        args.depth = min(3, depth)
                if branch:
                    filename = branch_relpath(branch, state["id"], state["submitted"] + 1)
                    os.makedirs(os.path.join(output_dir, "collections", branch), exist_ok=True)
                    marker = os.path.join(output_dir, "collections", branch, ".protocol-branch.json")
                    if not os.path.isfile(marker):
                        with open(marker, "w") as handle:
                            json.dump({"branch": branch, "wall": "/collections/" + branch}, handle)
                            handle.write("\n")
                else:
                    filename = "protocol-%s-%s-%03d.png" % (tag, state["id"], state["submitted"] + 1)
                    if args.arcane or tag == "arcane":
                        filename = "arcane/" + filename
                        os.makedirs(os.path.join(output_dir, "arcane"), exist_ok=True)
                try:
                    if sock_path:
                        resp = sock_request(sock_path, {
                            "op": "submit",
                            "backend": "cuda",
                            "prompt": prompt,
                            "steps": steps,
                            "guidance": guidance,
                            "width": width,
                            "height": height,
                            "seed": seed,
                            "filename": filename,
                        })
                    else:
                        resp = post_json("/api/render", {
                            "prompt": prompt,
                            "count": 1,
                            "steps": steps,
                            "guidance": guidance,
                            "width": width,
                            "height": height,
                            "seed": seed,
                            "filename": filename,
                        })
                    job = resp.get("job") or (resp.get("jobs") or [{}])[0]
                    jid = job.get("id")
                    if jid:
                        state["job_ids"].append(jid)
                        state["submitted"] += 1
                        state["error"] = ""
                except urllib.error.HTTPError as exc:
                    state["error"] = "render %s: %s" % (exc.code, exc.read()[:200].decode("utf-8", "replace"))
                    time.sleep(2)
                except Exception as exc:
                    state["error"] = str(exc)
                    time.sleep(2)

            state["updated_at"] = time.time()
            save_state(state, state_path)
            if not still_open(state) and state["done"] >= state["submitted"]:
                break
            time.sleep(1.2)

        state["status"] = "done"
        state["updated_at"] = time.time()
        save_state(state, state_path)
    except KeyboardInterrupt:
        state["status"] = "stopped"
        state["updated_at"] = time.time()
        save_state(state, state_path)
        raise
    except Exception as exc:
        state["status"] = "error"
        state["error"] = str(exc)
        state["updated_at"] = time.time()
        save_state(state, state_path)
        raise


if __name__ == "__main__":
    main()
