#!/usr/bin/env python3
import os, signal
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
killed = []
for name in os.listdir("/proc"):
    if not name.isdigit():
        continue
    try:
        cmd = open(os.path.join("/proc", name, "cmdline"), "rb").read().replace(b"\0", b" ").decode()
    except OSError:
        continue
    if "/protocol_stream.py" not in cmd.replace(" ", "/"):
        continue
    if "--branch" not in cmd or "microgreens" not in cmd:
        continue
    if "stop_microgreens" in cmd:
        continue
    pid = int(name)
    if pid == os.getpid():
        continue
        try:
            os.kill(pid, signal.SIGTERM)
            killed.append(pid)
        except OSError:
            pass
print("stopped", killed)
