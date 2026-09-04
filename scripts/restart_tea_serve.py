#!/usr/bin/env python3
import os, signal, subprocess, time
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for name in os.listdir("/proc"):
    if not name.isdigit():
        continue
    path = os.path.join("/proc", name, "cmdline")
    try:
        raw = open(path, "rb").read()
    except OSError:
        continue
    if b"tea\x00serve" in raw and b"flux" in raw:
        try:
            os.kill(int(name), signal.SIGTERM)
            print("stopped", name)
        except OSError:
            pass
time.sleep(1)
env = os.environ.copy()
env.update({
    "HOME": "/home/ubuntu",
    "OUT_DIR": "/home/ubuntu/models/flux-output",
    "FLUX_OUTPUT_DIR": "/home/ubuntu/models/flux-output",
    "FLUX_PYTHON": os.path.join(root, ".venv", "bin", "python"),
    "FLUX_BACKEND": "cuda",
})
log = open(os.path.join(root, ".fluxd", "tea-serve.log"), "a")
proc = subprocess.Popen(
    [os.path.join(root, "flux"), "tea", "serve", "--addr", "0.0.0.0:7861", "--backend", "cuda", "--unsafe-no-auth"],
    cwd=root,
    env=env,
    stdout=log,
    stderr=log,
    start_new_session=True,
)
print("started", proc.pid)
