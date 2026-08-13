#!/usr/bin/env python3
"""Node-local supervision for the continuous Tea H100 study pipeline."""
from __future__ import annotations

import json
import os
import pathlib
import signal
import subprocess
import time
import urllib.request


HOME = pathlib.Path.home()
SOURCE = HOME / "tea-motion/source"
RUN = HOME / "tea-motion/run"
LOGS = HOME / "tea-motion/logs"
OUTPUT = HOME / "models/tea-motion-output/studies/stallion-motion"
FLUX_OUTPUT = HOME / "models/flux-output"
STOP = False
MINERS = (
    (1, "spectral_loop", 101, 2), (2, "continuity", 211, 3), (3, "kinetic", 307, 4),
    (4, "spectral_loop", 401, 5), (5, "continuity", 503, 6), (6, "kinetic", 607, 7),
    (7, "spectral_loop", 701, 8), (8, "continuity", 809, 9), (9, "kinetic", 907, 10),
    (10, "spectral_loop", 1009, 11), (11, "continuity", 1201, 0), (12, "kinetic", 1303, 1),
    (13, "spectral_loop", 1409, 12), (14, "continuity", 1511, 13),
)


def stop_requested(_signal: int, _frame: object) -> None:
    global STOP
    STOP = True


def process_matches(pid_file: pathlib.Path, token: str) -> bool:
    try:
        pid = int(pid_file.read_text().strip())
        cmdline = pathlib.Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode()
        return token in cmdline
    except (OSError, ValueError, UnicodeDecodeError):
        return False


def spawn(name: str, command: list[str], env: dict[str, str], pid_file: pathlib.Path) -> int:
    log_path = LOGS / ("miners" if name.startswith("miner-") else "") / f"{name}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab", buffering=0) as log:
        process = subprocess.Popen(
            command, cwd=SOURCE, env={**os.environ, **env}, stdin=subprocess.DEVNULL,
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        )
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(f"{process.pid}\n", encoding="utf-8")
    return process.pid


def ensure_server() -> dict[str, object]:
    pid_file = RUN / "server.pid"
    healthy = False
    if process_matches(pid_file, "tea serve"):
        try:
            with urllib.request.urlopen("http://127.0.0.1:7861/api/health", timeout=2) as response:
                healthy = response.status == 200
        except OSError:
            healthy = False
    if not healthy:
        pid = spawn("server", [
            str(HOME / "tea-motion/flux"), "tea", "serve", "--addr", "0.0.0.0:7861",
            "--public-read-only", "--unsafe-no-auth",
        ], {"OUT_DIR": str(FLUX_OUTPUT), "FLUX_PYTHON": "/usr/bin/python3"}, pid_file)
        return {"state": "restarted", "pid": pid}
    return {"state": "running", "pid": int(pid_file.read_text())}


def ensure_reviewer() -> dict[str, object]:
    pid_file = RUN / "gpu-reviewer.pid"
    if process_matches(pid_file, "stallion_gpu_reviewer.py"):
        return {"state": "running", "pid": int(pid_file.read_text())}
    pid = spawn("gpu-reviewer", [
        "/usr/bin/python3", "scripts/stallion_gpu_reviewer.py",
        "--source", "apps/tea/public/assets/stallion-atlas-grid.jpg",
        "--output-root", str(OUTPUT), "--side", "256", "--batch-size", "12", "--poll", "0.15",
    ], {"CUDA_VISIBLE_DEVICES": "0"}, pid_file)
    return {"state": "restarted", "pid": pid}


def ensure_miner(number: int, mode: str, seed: int, core: int) -> dict[str, object]:
    name = f"miner-{number:02d}-{mode}"
    pid_file = RUN / "miners" / f"{name}.pid"
    if process_matches(pid_file, "stallion_motion_graph.py"):
        return {"state": "running", "pid": int(pid_file.read_text()), "mode": mode, "core": core}
    run_id = f"stallion-motion-h100-{name}-{time.strftime('%Y%m%d-%H%M%S', time.gmtime())}-{time.time_ns() % 1_000_000:06d}"
    pid = spawn(name, [
        "taskset", "-c", str(core), "/usr/bin/python3", "scripts/stallion_motion_graph.py",
        "--source", "apps/tea/public/assets/stallion-atlas-grid.jpg",
        "--protocol", "apps/tea/protocols/stallion-motion-v1.json",
        "--output-root", str(OUTPUT), "--run-id", run_id, "--modes", mode,
        "--frames", "24", "--fps", "10", "--seed", str(seed), "--continuous",
        "--round-pause", "0.25", "--retain", "128",
    ], {"OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"}, pid_file)
    return {"state": "restarted", "pid": pid, "mode": mode, "core": core}


def main() -> int:
    signal.signal(signal.SIGTERM, stop_requested)
    signal.signal(signal.SIGINT, stop_requested)
    RUN.mkdir(parents=True, exist_ok=True)
    while not STOP:
        status = {
            "schema": "tea.h100-supervisor.v1", "updated_at": time.time(),
            "server": ensure_server(), "gpu_reviewer": ensure_reviewer(),
            "miners": [ensure_miner(*spec) for spec in MINERS],
        }
        tmp = RUN / "supervisor-status.json.tmp"
        tmp.write_text(json.dumps(status, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(RUN / "supervisor-status.json")
        deadline = time.time() + 5
        while time.time() < deadline and not STOP:
            time.sleep(0.25)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
