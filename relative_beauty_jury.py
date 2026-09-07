#!/usr/bin/env python3
"""Bridge resident FLUX jobs into the real visual jury for one collection."""
import json
import os
import socket
import time
from pathlib import Path

import jury_evaluator

SOCKET = os.environ.get("BEAUTY_FLUX_SOCKET", "/home/dev/FLUX/.fluxd/flux-gpu0.sock")
COLLECTION = os.environ.get("BEAUTY_COLLECTION", "relative-beauty")
STATE = Path(os.environ.get(
    "BEAUTY_JURY_STATE",
    f"/home/dev/models/flux-output/collections/{COLLECTION}/jury-runtime.json",
))


def request(payload):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(30)
        client.connect(SOCKET)
        client.sendall((json.dumps(payload) + "\n").encode())
        data = b""
        while not data.endswith(b"\n"):
            chunk = client.recv(65536)
            if not chunk:
                break
            data += chunk
    if not data:
        raise RuntimeError("empty FLUX socket response")
    return json.loads(data)


def save(payload):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n")
    tmp.replace(STATE)


def main():
    state = {"status": "starting", "collection": COLLECTION, "seen": []}
    if STATE.exists():
        try:
            state.update(json.loads(STATE.read_text()))
        except Exception:
            pass
    seen = set(state.get("seen") or [])
    state["status"] = "running"
    save(state)
    while True:
        try:
            jobs = request({"op": "jobs"}).get("jobs") or []
            candidates = [
                job for job in jobs
                if job.get("status") == "done"
                and f"collections/{COLLECTION}/" in str(job.get("filename") or job.get("output" or ""))
                and job.get("id") not in seen
            ]
            if candidates:
                # Judge the newest available result. Do not let a fast generator
                # create an ever-growing stale jury backlog.
                job = max(candidates, key=lambda item: float(item.get("finished") or 0))
                state.update({"station": "judging", "job_id": job["id"], "updated_at": time.time()})
                save(state)
                receipt = jury_evaluator.score_frame(job)
                seen.update(item.get("id") for item in candidates if item.get("id"))
                state.update({
                    "station": "verdict-ready",
                    "last_job_id": job["id"],
                    "last_score": receipt.get("curved_score"),
                    "last_receipt": receipt,
                    "seen": list(seen)[-500:],
                    "error": "",
                    "updated_at": time.time(),
                })
                save(state)
            else:
                state.update({"station": "waiting-for-frame", "updated_at": time.time()})
                save(state)
        except Exception as exc:
            state.update({"station": "error", "error": repr(exc), "updated_at": time.time()})
            save(state)
            time.sleep(2)
        time.sleep(.5)


if __name__ == "__main__":
    main()
