#!/usr/bin/env python3
"""Keep GPU 0 on the motion experiment. Kill any Arcane miner that comes back."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
PYTHON = os.path.join(ROOT, ".venv", "bin", "python")
STREAMER = os.path.join(ROOT, "gpu0_motion_stream.py")
SOCK = os.path.join(ROOT, ".fluxd", "flux-gpu0.sock")
LOG = os.path.join(ROOT, ".fluxd", "motion_stream.log")
PIDFILE = os.path.join(ROOT, ".fluxd", "motion_stream.pid")


def cmdlines():
    out = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            raw = open(os.path.join("/proc", pid, "cmdline"), "rb").read()
        except OSError:
            continue
        if not raw:
            continue
        cmd = raw.replace(b"\0", b" ").decode("utf-8", "replace")
        out.append((int(pid), cmd))
    return out


def kill_pid(pid, why):
    print("motion-lock kill %s (%s)" % (pid, why), flush=True)
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return
    time.sleep(0.3)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


def start():
    env = os.environ.copy()
    env["OUT_DIR"] = env.get("OUT_DIR") or os.path.expanduser("~/models/flux-output")
    env["FLUX_OUTPUT_DIR"] = env["OUT_DIR"]
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    logf = open(LOG, "a")
    proc = subprocess.Popen(
        [PYTHON, "-u", STREAMER],
        cwd=ROOT,
        env=env,
        stdout=logf,
        stderr=logf,
        start_new_session=True,
    )
    with open(PIDFILE, "w") as f:
        f.write(str(proc.pid) + "\n")
    print("motion-lock start pid %s" % proc.pid, flush=True)


def stamp_arcane_stopped():
    path = os.path.join(ROOT, ".fluxd", "arcane_stream.json")
    try:
        with open(path) as f:
            state = json.load(f)
    except Exception:
        state = {}
    if state.get("status") == "stopped" and "unplugged" in str(state.get("error") or ""):
        return
    state["status"] = "stopped"
    state["error"] = "operator unplugged Arcane — GPU 0 is gpu0-silk-wind-001 (atlas, off gallery)"
    state["running"] = 0
    state["updated_at"] = time.time()
    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def tick():
    stamp_arcane_stopped()
    miners = []
    motion = []
    for pid, cmd in cmdlines():
        if "arcane_atlas_stream.py" in cmd:
            kill_pid(pid, "Arcane miner — GPU 0 is motion-only")
            continue
        if "protocol_stream.py" in cmd and "flux-gpu0.sock" in cmd and "--branch" not in cmd:
            kill_pid(pid, "still/protocol streamer stealing GPU 0")
            continue
        if "gpu0_motion_stream.py" in cmd:
            motion.append(pid)
    for extra in sorted(motion)[1:]:
        kill_pid(extra, "duplicate motion streamer")
    if os.path.exists(SOCK) and not motion:
        start()


def main():
    print("motion-lock watching GPU 0 — Arcane is unplugged", flush=True)
    while True:
        try:
            tick()
        except Exception as exc:
            print("motion-lock tick error: %r" % (exc,), flush=True)
        time.sleep(5)


if __name__ == "__main__":
    sys.exit(main())
