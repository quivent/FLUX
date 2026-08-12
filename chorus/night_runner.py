#!/usr/bin/env python3
"""Resume the declared overnight study without overwriting completed assets."""
import argparse
import json
import pathlib
import subprocess
import sys
import time


def atomic_json(path, value):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default=str(pathlib.Path(__file__).with_name("night-run.json")))
    ap.add_argument("--python", default=sys.executable)
    args = ap.parse_args()
    manifest_path = pathlib.Path(args.manifest).resolve()
    spec = json.loads(manifest_path.read_text())
    root = manifest_path.parent.parent
    out = pathlib.Path(spec["out_dir"])
    state_path = out / "night-run-state.json"
    jobs = [(job, replicate) for job in spec["jobs"]
            for replicate in range(1, int(spec["replicates"]) + 1)]
    state = {"schema": "flux.autonomous-run-state.v1", "name": spec["name"],
             "total_jobs": len(jobs), "status": "running", "started": time.time()}
    for index, (job, replicate) in enumerate(jobs):
        job_id = f"step-study-{job['family']}-r{replicate}"
        state.update({"current_job": job_id, "job_index": index,
                      "completed_jobs": index, "updated": time.time()})
        atomic_json(state_path, state)
        command = [args.python, str(root / "chorus" / "step_sweep.py"),
                   "--prompt", job["prompt"], "--id", job_id,
                   "--model-dir", spec["model_dir"], "--out-dir", spec["out_dir"],
                   "--step-range", spec["step_range"], "--size", str(spec["size"]),
                   "--guidance", str(spec["guidance"]), "--seed", str(job["seed"])]
        result = subprocess.run(command, check=False)
        if result.returncode:
            state.update({"status": "failed", "failed_job": job_id,
                          "exit_code": result.returncode, "updated": time.time()})
            atomic_json(state_path, state)
            return result.returncode
    state.update({"status": "done", "completed_jobs": len(jobs),
                  "finished": time.time(), "updated": time.time()})
    atomic_json(state_path, state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
