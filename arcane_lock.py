#!/usr/bin/env python3
"""Arcane stays unplugged. Does not occupy GPU 0 — that card is fashion."""
from __future__ import annotations

import json
import os
import signal
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(ROOT, ".fluxd", "arcane_stream.json")


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
        if "arcane_atlas_stream.py" in cmd:
            out.append(int(pid))
    return out


def kill_pid(pid):
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return
    time.sleep(0.3)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


def stamp_stopped():
    try:
        with open(STATE) as f:
            state = json.load(f)
    except Exception:
        state = {}
    state["status"] = "stopped"
    state["running"] = 0
    state["error"] = "unplugged — GPU 0 is fashion"
    state["updated_at"] = time.time()
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
        f.write("\n")
    os.replace(tmp, STATE)


def main():
    print("arcane-lock: GPU 0 is fashion; Arcane will not restart", flush=True)
    while True:
        try:
            for pid in cmdlines():
                print("arcane-lock kill miner %s" % pid, flush=True)
                kill_pid(pid)
            stamp_stopped()
        except Exception as exc:
            print("arcane-lock tick error: %r" % (exc,), flush=True)
        time.sleep(5)


if __name__ == "__main__":
    sys.exit(main())
