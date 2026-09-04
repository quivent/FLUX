#!/usr/bin/env python3
"""GPU 0 motion experiment.

Submits one atlas_sphere latent path to the BF16 worker. Frames write under
outputs/atlas/<id>.sphere/ which the fashion gallery never lists.
"""
from __future__ import annotations

import json
import os
import socket
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(ROOT, ".fluxd", "motion_stream.json")
SOCK = os.path.join(ROOT, ".fluxd", "flux-gpu0.sock")
DRAFT_PATH = os.path.join(ROOT, "atlas_drafts", "gpu0-silk-wind-001.json")
JOB_ID = "gpu0-silk-wind-001"


def load_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


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


def find_job(jobs):
    for job in jobs or []:
        if job.get("id") == JOB_ID or job.get("kind") == "atlas_sphere":
            if job.get("id") == JOB_ID:
                return job
    for job in jobs or []:
        if job.get("id") == JOB_ID:
            return job
    return None


def main():
    draft = load_json(DRAFT_PATH)
    if not draft:
        raise SystemExit("missing draft %s" % DRAFT_PATH)
    state = {
        "id": JOB_ID,
        "studio": "gpu0-motion",
        "status": "running",
        "lane": "motion",
        "wall": "/movement",
        "gallery": False,
        "output": "atlas/%s.sphere" % JOB_ID,
        "prompt": draft.get("prompt"),
        "n": draft.get("render_count"),
        "steps": draft.get("steps"),
        "size": draft.get("size"),
        "socket": SOCK,
        "started_at": time.time(),
        "updated_at": time.time(),
        "error": "",
        "done": 0,
        "atlas_total": draft.get("render_count") or 64,
        "job_status": "",
    }
    save_state(state)
    print("gpu0 motion experiment %s" % JOB_ID, flush=True)

    while True:
        jobs = sock_request({"op": "jobs"}).get("jobs") or []
        job = find_job(jobs)
        if job is None:
            resp = sock_request(
                {
                    "op": "atlas_sphere",
                    "id": JOB_ID,
                    "backend": "cuda",
                    "draft": draft,
                    "prompt": draft.get("prompt"),
                    "size": draft.get("size"),
                    "steps": draft.get("steps"),
                    "render_count": draft.get("render_count"),
                    "study_type": "movement",
                }
            )
            job = (resp.get("job") or {}) if resp.get("ok") else {}
            print("submit atlas_sphere %s already=%s" % (job.get("id"), resp.get("already")), flush=True)
        status = (job or {}).get("status") or "unknown"
        done = int((job or {}).get("atlas_done") or 0)
        total = int((job or {}).get("atlas_total") or state["atlas_total"])
        state.update(
            {
                "job_status": status,
                "done": done,
                "atlas_total": total,
                "updated_at": time.time(),
                "status": "done" if status == "done" else ("error" if status == "error" else "running"),
                "error": (job or {}).get("error") or "",
            }
        )
        save_state(state)
        if status in ("done", "error", "cancelled"):
            print("motion experiment %s" % status, flush=True)
            while True:
                time.sleep(30)
                save_state(state)
        time.sleep(2)


if __name__ == "__main__":
    main()
