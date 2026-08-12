#!/usr/bin/env python3
"""Drain the step prelude and 48-study beauty queue without overwrites."""
import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
import time


def atomic_json(path, value):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def slug(value):
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def ledger_generations(path):
    seen = set()
    try:
        for line in path.read_text().splitlines():
            row = json.loads(line)
            seen.add(int(row.get("generation") or 0))
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    return len(seen)


def step_values(spec):
    if ":" in spec:
        low, high = (int(value) for value in spec.split(":", 1))
        return list(range(low, high + 1))
    return sorted({int(value) for value in spec.split(",") if value.strip()})


def prelude_snapshot(spec, out, current=None):
    target = len(step_values(spec["step_range"]))
    jobs = []
    for job in spec["jobs"]:
        for replicate in range(1, int(spec["replicates"]) + 1):
            job_id = f"step-study-{job['family']}-r{replicate}"
            sphere = out / "atlas" / f"{job_id}.sphere"
            count = len(list(sphere.glob(f"{job_id}-steps-*.png")))
            state = "done" if count >= target else "running" if job_id == current else "queued"
            jobs.append({"name": job_id.replace("step-study-", "").replace("-", " ").title(),
                         "slug": job_id, "approved": True, "axis": "denoise depth",
                         "rendered": count, "target": target, "status": state})
    return {"schema": "flux.beauty-queue-state.v1", "name": "Adjacent Geometry Prelude",
            "status": "running", "updated": time.time(), "current": current,
            "completed_jobs": sum(job["status"] == "done" for job in jobs),
            "total_jobs": len(jobs), "jobs": jobs}


def queue_snapshot(catalog, out, current=None, status="running"):
    jobs, completed = [], 0
    for index, job in enumerate(catalog["jobs"]):
        job_slug = slug(job["name"])
        target = int(job.get("generations") or catalog["defaults"]["generations"])
        count = ledger_generations(out / "collections" / job_slug / "creative-drift.jsonl")
        state = "done" if count >= target else "running" if job_slug == current else "queued"
        completed += int(state == "done")
        jobs.append({"index": index, "name": job["name"], "slug": job_slug,
                     "approved": job.get("approved") is True, "axis": job.get("axis"),
                     "rendered": count, "target": target, "status": state})
    return {"schema": "flux.beauty-queue-state.v1", "name": catalog["name"],
            "status": status, "updated": time.time(), "current": current,
            "completed_jobs": completed, "total_jobs": len(jobs), "jobs": jobs}


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
    queue_path = out / "queue-state.json"
    jobs = [(job, replicate) for job in spec["jobs"]
            for replicate in range(1, int(spec["replicates"]) + 1)]
    state = {"schema": "flux.autonomous-run-state.v1", "name": spec["name"],
             "total_jobs": len(jobs), "status": "running", "started": time.time()}
    for index, (job, replicate) in enumerate(jobs):
        job_id = f"step-study-{job['family']}-r{replicate}"
        atomic_json(queue_path, prelude_snapshot(spec, out, job_id))
        state.update({"current_job": job_id, "job_index": index,
                      "completed_jobs": index, "updated": time.time()})
        atomic_json(state_path, state)
        command = [args.python, str(root / "chorus" / "step_sweep.py"),
                   "--prompt", job["prompt"], "--id", job_id,
                   "--model-dir", spec["model_dir"], "--out-dir", spec["out_dir"],
                   "--step-range", spec["step_range"], "--size", str(spec["size"]),
                   "--guidance", str(spec["guidance"]), "--seed", str(job["seed"])]
        result = subprocess.run(command, env={**dict(os.environ),
                                "FLUX_QUEUE_STATE": str(queue_path),
                                "FLUX_QUEUE_JOB": job_id}, check=False)
        if result.returncode:
            state.update({"status": "failed", "failed_job": job_id,
                          "exit_code": result.returncode, "updated": time.time()})
            atomic_json(state_path, state)
            return result.returncode
    state.update({"status": "prelude_done", "completed_jobs": len(jobs),
                  "prelude_finished": time.time(), "updated": time.time()})
    atomic_json(state_path, state)
    catalog = json.loads(pathlib.Path(spec["production_manifest"]).read_text())
    if len(catalog.get("jobs") or []) != 48 or not all(job.get("approved") is True for job in catalog["jobs"]):
        raise SystemExit("beauty queue requires exactly 48 explicitly approved jobs")
    defaults = catalog["defaults"]
    collections = out / "collections"; collections.mkdir(parents=True, exist_ok=True)
    for job in catalog["jobs"]:
        job_slug = slug(job["name"])
        target = int(job.get("generations") or defaults["generations"])
        job_out = collections / job_slug; job_out.mkdir(parents=True, exist_ok=True)
        if ledger_generations(job_out / "creative-drift.jsonl") >= target:
            continue
        atomic_json(queue_path, queue_snapshot(catalog, out, job_slug))
        control = job_out / "drift-control.json"
        if not control.exists():
            merged = {key: job.get(key, value) for key, value in defaults.items()
                      if key not in ("generations", "width", "height", "batch")}
            merged.update({"phase": "auto", "paused": False, "pinned": {},
                           "study_prompt": job["focus"], "study_name": job["name"],
                           "approved": True, "axis": job.get("axis")})
            atomic_json(control, merged)
        command = [args.python, str(root / "chorus" / "loop.py"),
                   "--out-dir", str(job_out), "--model-dir", spec["model_dir"],
                   "--control", str(control), "--max-generations", str(target),
                   "--batch", "1", "--width", "512", "--height", "512",
                   "--steps", str(job.get("steps", defaults["steps"])),
                   "--guidance", str(job.get("guidance", defaults["guidance"])),
                   "--mutation-rate", str(job.get("mutation_rate", defaults["mutation_rate"])),
                   "--latent-max-cosine", str(job.get("latent_max_cosine", defaults["latent_max_cosine"])),
                   "--style-hold-generations", str(job.get("style_hold_generations", defaults["style_hold_generations"])),
                   "--phase-hours", str(job.get("phase_hours", defaults["phase_hours"])),
                   "--seed", str(job["seed"])]
        result = subprocess.run(command, env={**dict(os.environ),
                                "FLUX_OUTPUT_ROOT": str(out),
                                "FLUX_QUEUE_STATE": str(queue_path),
                                "FLUX_QUEUE_JOB": job_slug}, check=False)
        if result.returncode:
            atomic_json(queue_path, queue_snapshot(catalog, out, job_slug, "failed"))
            return result.returncode
    state.update({"status": "done", "finished": time.time(), "updated": time.time()})
    atomic_json(state_path, state)
    atomic_json(queue_path, queue_snapshot(catalog, out, status="done"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
