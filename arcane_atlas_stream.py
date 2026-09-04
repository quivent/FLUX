#!/usr/bin/env python3
"""Arcane atlas-mining streamer.

Independent of the fashion beauty wall. Submits Fortiche animation-still
variants (camera / light / character / paint) to the GPU 0 BF16 worker and
writes them under outputs/arcane/ so /gallery/arcane is the only public wall.
"""
from __future__ import annotations

import json
import os
import socket
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(ROOT, ".fluxd", "arcane_stream.json")
SOCK = os.path.join(ROOT, ".fluxd", "flux-gpu0.sock")

# Governor 2026-09-04: front-load hybrid-animation paint, isolate the lung as
# a biological integration, lower guidance, never say Fortiche / beauty / holding / not-X.
PROMPT_VERSION = "governor-v6"
EXTRA = "rusted bioluminescent mechanical lung"
TAIL = (
    "High-end animated series frame, visible paint strokes, "
    "gritty industrial atmosphere, dark sump-city background"
)

CAMERAS = (
    "three-quarter portrait",
    "strict left profile",
    "low-angle portrait",
    "over-shoulder glance",
    "frontal portrait",
)
LIGHTS = (
    "hand-painted rim light",
    "graphic shadows",
    "theatrical chiaroscuro",
    "oil-paint texture over clean 3D forms",
)
FIGURES = (
    "woman",
    "man",
)


def prompt_for(i: int) -> str:
    figure = FIGURES[i % len(FIGURES)]
    core = (
        "Cinematic hybrid animation still, sculpted 3D character form with painterly 2D brushwork, "
        "oil-paint texture over clean 3D forms, graphic shadows, hand-painted rim light. "
        "A gaunt Zaunite %s with human ears, severe angular features, scarred skin. "
        "Integrated into the open ribcage is a rusted bioluminescent mechanical lung with brass bellows."
    ) % figure
    return "%s %s, %s, %s." % (
        core,
        TAIL,
        CAMERAS[i % len(CAMERAS)],
        LIGHTS[i % len(LIGHTS)],
    )


def load_state():
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
        f.write("\n")
    os.replace(tmp, STATE_PATH)


def sock_request(payload, timeout=30):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout)
        client.connect(SOCK)
        client.sendall((json.dumps(payload) + "\n").encode())
        data = b""
        while not data.endswith(b"\n"):
            chunk = client.recv(65536)
            if not chunk:
                break
            data += chunk
    if not data:
        raise RuntimeError("empty socket response")
    return json.loads(data.decode())


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
            if rec.get("tier") in ("spectacle", "masterpiece") or rec.get("is_spectacle"):
                spectacles += 1
            if rec.get("tier") == "unscored" or rec.get("composite") is None:
                unscored += 1
    return evaluated, spectacles, unscored


def main():
    n = 256
    steps = 30
    guidance = 3.5
    depth = 2
    output_dir = os.environ.get("OUT_DIR") or os.path.expanduser("~/models/flux-output")
    arcane_dir = os.path.join(output_dir, "arcane")
    os.makedirs(arcane_dir, exist_ok=True)
    prev = load_state()
    resume = (
        isinstance(prev, dict)
        and prev.get("n") == n
        and prev.get("steps") == steps
        and int(prev.get("submitted") or 0) < n
        and prev.get("status") in ("running", "error", "stopped")
        and prev.get("studio") == "arcane-atlas"
        and prev.get("prompt_version") == PROMPT_VERSION
    )
    if resume:
        state = prev
        state["status"] = "running"
        state["prompt_version"] = PROMPT_VERSION
        state["guidance"] = guidance
        state["error"] = ""
        state["updated_at"] = time.time()
        state.setdefault("job_ids", [])
    else:
        state = {
            "id": time.strftime("atlas-%Y%m%d-%H%M%S"),
            "studio": "arcane-atlas",
            "prompt_version": PROMPT_VERSION,
            "status": "running",
            "n": n,
            "steps": steps,
            "width": 1024,
            "height": 1024,
            "guidance": guidance,
            "lane": "arcane",
            "socket": SOCK,
            "eval_path": [
                "generate",
                "uniqueness",
                "sensory_gates",
                "forliche",
                "witness",
                "pixtral",
                "governor",
                "composite",
            ],
            "submitted": 0,
            "done": 0,
            "running": 0,
            "evaluated": 0,
            "spectacles": 0,
            "unscored": 0,
            "job_ids": [],
            "variant": "",
            "prompt": "",
            "error": "",
            "started_at": time.time(),
            "updated_at": time.time(),
        }
    save_state(state)
    print("arcane atlas mining %s" % state["id"], flush=True)

    try:
        while state["submitted"] < n or state["done"] < state["submitted"]:
            jobs = sock_request({"op": "jobs"}).get("jobs") or []
            ours = set(state["job_ids"])
            mine = [j for j in jobs if j.get("id") in ours]
            state["done"] = sum(1 for j in mine if j.get("status") in ("done", "error", "cancelled"))
            state["running"] = sum(1 for j in mine if j.get("status") in ("queued", "running"))
            ev, sp, un = audit_stats(os.path.join(output_dir, "arcane"))
            state["evaluated"] = ev
            state["spectacles"] = sp
            state["unscored"] = un
            if state["submitted"] < n and state["running"] < depth:
                i = state["submitted"]
                prompt = prompt_for(i)
                seed = str(int(time.time() * 1000) % 2147483647 + i)
                filename = "arcane/protocol-arcane-%s-%03d.png" % (state["id"], i + 1)
                resp = sock_request(
                    {
                        "op": "submit",
                        "backend": "cuda",
                        "prompt": prompt,
                        "steps": steps,
                        "guidance": guidance,
                        "width": 1024,
                        "height": 1024,
                        "seed": seed,
                        "filename": filename,
                    }
                )
                job = resp.get("job") or {}
                jid = job.get("id")
                if jid:
                    state["job_ids"].append(jid)
                    state["submitted"] += 1
                    state["variant"] = "%s | %s | %s" % (
                        FIGURES[i % len(FIGURES)],
                        CAMERAS[i % len(CAMERAS)],
                        LIGHTS[i % len(LIGHTS)],
                    )
                    state["prompt"] = prompt
                    print("submit %s %s" % (jid, state["variant"]), flush=True)
            state["updated_at"] = time.time()
            save_state(state)
            time.sleep(1.2)
        state["status"] = "done"
        save_state(state)
    except Exception as exc:
        state["status"] = "error"
        state["error"] = str(exc)[:300]
        save_state(state)
        raise


if __name__ == "__main__":
    main()
