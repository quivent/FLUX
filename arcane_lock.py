#!/usr/bin/env python3
"""Keep the Arcane atlas miner on GPU 0. Fashion stays on GPU 3."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
PYTHON = os.path.join(ROOT, ".venv", "bin", "python")
STREAMER = os.path.join(ROOT, "arcane_atlas_stream.py")
SOCK = os.path.join(ROOT, ".fluxd", "flux-gpu0.sock")
LOG = os.path.join(ROOT, ".fluxd", "arcane_stream.log")
PIDFILE = os.path.join(ROOT, ".fluxd", "arcane_stream.pid")
FASHION = "The most extravagant fashion models"


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
        if "arcane_atlas_stream.py" in cmd or (
            "protocol_stream.py" in cmd and "flux-gpu0.sock" in cmd
        ):
            out.append((int(pid), cmd))
    return out


def kill_pid(pid, why):
    print("arcane-lock kill %s (%s)" % (pid, why), flush=True)
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
    print("arcane-lock start pid %s" % proc.pid, flush=True)


def tick():
    miners = []
    for pid, cmd in cmdlines():
        if "protocol_stream.py" in cmd and "flux-gpu0.sock" in cmd:
            kill_pid(pid, "fashion/protocol streamer stealing GPU 0")
            continue
        if "arcane_atlas_stream.py" in cmd:
            miners.append(pid)
    for extra in sorted(miners)[1:]:
        kill_pid(extra, "duplicate miner")
    if os.path.exists(SOCK) and not miners:
        start()


def main():
    print("arcane-lock watching GPU 0", flush=True)
    while True:
        try:
            tick()
        except Exception as exc:
            print("arcane-lock tick error: %r" % (exc,), flush=True)
        time.sleep(5)


if __name__ == "__main__":
    sys.exit(main())
