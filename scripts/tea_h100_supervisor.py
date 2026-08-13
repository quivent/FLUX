#!/usr/bin/env python3
"""Node-local supervision for the fail-closed Tea H100 study pipeline."""
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
NATIVE_CELLS = pathlib.Path(os.environ.get(
    "TEA_STALLION_CELL_DIR",
    str(HOME / "models/stallion-native/spheremap_atlas_parametergridatl_1781801154422_0.sphere"),
))
STOP = False
MINERS = (
    (1, "spectral_loop", 101, 2), (2, "continuity", 211, 3), (3, "kinetic", 307, 4),
    (4, "spectral_loop", 401, 5), (5, "continuity", 503, 6), (6, "kinetic", 607, 7),
    (7, "spectral_loop", 701, 8), (8, "continuity", 809, 9), (9, "kinetic", 907, 10),
    (10, "spectral_loop", 1009, 11), (11, "continuity", 1201, 12), (12, "kinetic", 1303, 13),
)


def source_ready() -> tuple[bool, str]:
    if not NATIVE_CELLS.is_dir():
        return False, f"native source absent: {NATIVE_CELLS}"
    cells = list(NATIVE_CELLS.glob("cell_*.png"))
    if len(cells) not in (7584, 65536):
        return False, f"native source has {len(cells)} cells; require declared topology 7584 or 65536"
    return True, f"{len(cells)} native cells ready"


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
    try:
        with urllib.request.urlopen("http://127.0.0.1:7861/api/health", timeout=2) as response:
            healthy = response.status == 200
    except OSError:
        healthy = False
    if not healthy:
        pid = spawn("server", [
            str(HOME / "tea-motion/flux"), "tea", "serve", "--addr", "0.0.0.0:7861",
            "--public-read-only", "--unsafe-no-auth",
        ], {
            "OUT_DIR": str(FLUX_OUTPUT), "FLUX_PYTHON": "/usr/bin/python3",
            "TEA_STALLION_CELL_DIR": str(NATIVE_CELLS),
        }, pid_file)
        return {"state": "restarted", "pid": pid}
    pid = int(pid_file.read_text()) if process_matches(pid_file, "tea serve") else 0
    return {"state": "running", "pid": pid}


def ensure_reviewer() -> dict[str, object]:
    # This function is reached only after source_ready in the main loop.
    pid_file = RUN / "gpu-reviewer.pid"
    if process_matches(pid_file, "stallion_gpu_reviewer.py"):
        return {"state": "running", "pid": int(pid_file.read_text())}
    pid = spawn("gpu-reviewer", [
        "/usr/bin/python3", "scripts/stallion_gpu_reviewer.py",
        "--source", str(NATIVE_CELLS),
        "--output-root", str(OUTPUT), "--side", "256", "--batch-size", "32", "--poll", "0.25",
    ], {"CUDA_VISIBLE_DEVICES": "0"}, pid_file)
    return {"state": "restarted", "pid": pid}


def ensure_cognition() -> dict[str, object]:
    pid_file = RUN / "cognition.pid"
    if process_matches(pid_file, "stallion_cognition_loop.py"):
        return {"state": "running", "pid": int(pid_file.read_text())}
    pid = spawn("cognition", [
        "/usr/bin/python3", "scripts/stallion_cognition_loop.py",
        "--output-root", str(OUTPUT), "--poll", "10",
    ], {}, pid_file)
    return {"state": "restarted", "pid": pid}


def ensure_miner(number: int, mode: str, seed: int, core: int) -> dict[str, object]:
    name = f"miner-{number:02d}-{mode}"
    pid_file = RUN / "miners" / f"{name}.pid"
    if process_matches(pid_file, "stallion_motion_graph.py"):
        return {"state": "running", "pid": int(pid_file.read_text()), "mode": mode, "core": core}
    run_id = f"stallion-motion-h100-{name}-{time.strftime('%Y%m%d-%H%M%S', time.gmtime())}-{time.time_ns() % 1_000_000:06d}"
    pid = spawn(name, [
        "taskset", "-c", str(core), "/usr/bin/python3", "scripts/stallion_motion_graph.py",
        "--source", str(NATIVE_CELLS),
        "--protocol", "apps/tea/protocols/stallion-motion-v2.json",
        "--output-root", str(OUTPUT), "--run-id", run_id, "--modes", mode,
        "--frames", "24", "--fps", "10", "--seed", str(seed), "--continuous",
        "--round-pause", "0.25", "--retain", "128",
    ], {"OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"}, pid_file)
    return {"state": "restarted", "pid": pid, "mode": mode, "core": core}


def native_validation_passed() -> bool:
    try:
        payload = json.loads((OUTPUT / "gpu-reviews.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    for review in (payload.get("reviews") or {}).values():
        if (
            review.get("schema") == "tea.stallion-motion.gpu-review.v2"
            and review.get("qualified") is True
            and int(review.get("pair_count", 0)) >= 23
            and float((review.get("object_rubric") or {}).get("cumulative_background_displacement", 1.0)) <= 0.050
        ):
            return True
    return False


def ensure_validation_miner() -> dict[str, object]:
    name = "native-validation"
    pid_file = RUN / "miners" / f"{name}.pid"
    if process_matches(pid_file, "stallion_motion_graph.py"):
        return {"state": "running", "pid": int(pid_file.read_text()), "mode": "validation", "core": 2}
    seed = int(time.time()) % 2_000_000_000
    run_id = f"stallion-motion-h100-validation-{time.strftime('%Y%m%d-%H%M%S', time.gmtime())}"
    pid = spawn(name, [
        "taskset", "-c", "2", "/usr/bin/python3", "scripts/stallion_motion_graph.py",
        "--source", str(NATIVE_CELLS),
        "--protocol", "apps/tea/protocols/stallion-motion-v2.json",
        "--output-root", str(OUTPUT), "--run-id", run_id,
        "--modes", "continuity", "--frames", "24", "--fps", "10",
        "--seed", str(seed), "--rounds", "1", "--retain", "8",
    ], {"OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"}, pid_file)
    return {"state": "restarted", "pid": pid, "mode": "validation", "core": 2, "seed": seed}


def main() -> int:
    signal.signal(signal.SIGTERM, stop_requested)
    signal.signal(signal.SIGINT, stop_requested)
    RUN.mkdir(parents=True, exist_ok=True)
    while not STOP:
        ready, source_message = source_ready()
        if not ready:
            status = {
                "schema": "tea.h100-supervisor.v2", "updated_at": time.time(),
                "server": ensure_server(), "source": {"ready": False, "message": source_message},
                "cognition": ensure_cognition(), "gpu_reviewer": {"state": "gated"}, "miners": [],
            }
            tmp = RUN / "supervisor-status.json.tmp"
            tmp.write_text(json.dumps(status, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            tmp.replace(RUN / "supervisor-status.json")
            time.sleep(5)
            continue
        reviewer = ensure_reviewer()
        validation_passed = native_validation_passed()
        status = {
            "schema": "tea.h100-supervisor.v2", "updated_at": time.time(),
            "source": {"ready": True, "message": source_message},
            "server": ensure_server(), "cognition": ensure_cognition(), "gpu_reviewer": reviewer,
            "native_validation_passed": validation_passed,
            "execution_phase": "continuous" if validation_passed else "native_validation",
            "miners": (
                [ensure_miner(*spec) for spec in MINERS]
                if validation_passed else [ensure_validation_miner()]
            ),
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
