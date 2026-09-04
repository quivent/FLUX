#!/usr/bin/env python3
"""Keep the fashion brief on GPU 3 only. GPU 0 is the motion lane — do not touch it."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
PYTHON = os.path.join(ROOT, ".venv", "bin", "python")
STREAMER = os.path.join(ROOT, "protocol_stream.py")
FASHION = (
    "The most extravagant fashion models in the most unique and exquisite dresses "
    "ever made, of all shapes and sizes and colors, the new Fashion beauty on beauty"
)
GPU3_SOCK = os.path.join(ROOT, ".fluxd", "flux-gpu3.sock")
LANES = (
    {
        "name": "gpu3",
        "socket": GPU3_SOCK,
        "state": os.path.join(ROOT, ".fluxd", "protocol_stream_gpu3.json"),
        "log": os.path.join(ROOT, ".fluxd", "protocol_stream_gpu3.log"),
        "pidfile": os.path.join(ROOT, ".fluxd", "protocol_stream_gpu3.pid"),
    },
)
PAUSE = os.path.join(ROOT, ".fluxd", "studio_fashion.pause")
BANNED = (
    "--still-life",
    "--arcane",
    "celadon",
    "tea bowl",
    "kintsugi",
    "mechanical lung",
    "zaunite",
    "princess",
)


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
        # GPU 3 fashion lane only. Independent --branch collections are not fashion.
        if "protocol_stream.py" in cmd and GPU3_SOCK in cmd and "--branch" not in cmd:
            out.append((int(pid), cmd))
    return out


def kill_pid(pid, why):
    print("fashion-lock kill %s (%s)" % (pid, why), flush=True)
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return
    time.sleep(0.4)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


def start_lane(lane):
    env = os.environ.copy()
    env["OUT_DIR"] = env.get("OUT_DIR") or os.path.expanduser("~/models/flux-output")
    env["FLUX_OUTPUT_DIR"] = env["OUT_DIR"]
    env["FLUX_HTTP"] = env.get("FLUX_HTTP") or "http://127.0.0.1:7861"
    logf = open(lane["log"], "a")
    proc = subprocess.Popen(
        [
            PYTHON,
            "-u",
            STREAMER,
            "--n",
            "256",
            "--steps",
            "18",
            "--width",
            "256",
            "--height",
            "256",
            "--depth",
            "2",
            "--prompt",
            FASHION,
            "--socket",
            lane["socket"],
            "--state",
            lane["state"],
            "--lane",
            "fashion",
        ],
        cwd=ROOT,
        env=env,
        stdout=logf,
        stderr=logf,
        start_new_session=True,
    )
    with open(lane["pidfile"], "w") as f:
        f.write(str(proc.pid) + "\n")
    print("fashion-lock start %s pid %s" % (lane["name"], proc.pid), flush=True)


def tick():
    mine = {lane["socket"]: [] for lane in LANES}
    if os.path.exists(PAUSE):
        for pid, cmd in cmdlines():
            kill_pid(pid, "studio fashion paused")
        return
    for pid, cmd in cmdlines():
        banned = [b for b in BANNED if b in cmd]
        if banned:
            kill_pid(pid, "banned " + ",".join(banned))
            continue
        if FASHION not in cmd:
            kill_pid(pid, "not the fashion brief")
            continue
        hit = False
        for lane in LANES:
            if lane["socket"] in cmd:
                mine[lane["socket"]].append(pid)
                hit = True
        if not hit:
            kill_pid(pid, "protocol streamer on an unlocked socket")
    for lane in LANES:
        pids = sorted(mine[lane["socket"]])
        for extra in pids[1:]:
            kill_pid(extra, "duplicate on " + lane["name"])
        if os.path.exists(lane["socket"]) and not pids:
            start_lane(lane)


def main():
    print("fashion-lock watching GPU 3 only", flush=True)
    while True:
        try:
            tick()
        except Exception as exc:
            print("fashion-lock tick error: %r" % (exc,), flush=True)
        time.sleep(5)


if __name__ == "__main__":
    sys.exit(main())
