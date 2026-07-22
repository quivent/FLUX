#!/usr/bin/env python3
import argparse
import json
import pathlib
import random
import socket
import sys
import time
import traceback


DONE_STATES = {"done", "error", "cancelled"}


def request(sock_path, payload, timeout=30):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout)
        client.connect(str(sock_path))
        client.sendall((json.dumps(payload) + "\n").encode())
        data = b""
        while not data.endswith(b"\n"):
            chunk = client.recv(65536)
            if not chunk:
                break
            data += chunk
    if not data:
        raise RuntimeError("empty socket response")
    return json.loads(data.decode())


def load_plan(path):
    with open(path, "r", encoding="utf-8") as f:
        plan = json.load(f)
    groups = plan.get("groups") or []
    if not groups:
        raise ValueError("plan has no groups")
    return plan


def expand_jobs(plan):
    rng = random.Random(int(plan.get("seed_base") or 1))
    base = {
        "backend": plan.get("backend") or "mps",
        "width": int(plan.get("width") or 512),
        "height": int(plan.get("height") or 512),
        "steps": int(plan.get("steps") or 28),
        "guidance": float(plan.get("guidance") or 4.1),
    }
    jobs = []
    for group_index, group in enumerate(plan["groups"], start=1):
        group_id = group["id"]
        prompts = group.get("prompts") or []
        seeds_per_prompt = int(group.get("seeds_per_prompt") or 1)
        for prompt_index, prompt in enumerate(prompts, start=1):
            seeds = rng.sample(range(100_000, 999_999_999), seeds_per_prompt)
            for variant_index, seed in enumerate(seeds, start=1):
                stem = f"{group_index:02d}-{group_id}/p{prompt_index:02d}/p{prompt_index:02d}-s{variant_index:02d}-seed-{seed}.png"
                jobs.append(
                    {
                        **base,
                        "group_id": group_id,
                        "group_label": group.get("label") or group_id,
                        "prompt_index": prompt_index,
                        "variant_index": variant_index,
                        "seed": str(seed),
                        "prompt": prompt,
                        "filename": f"{plan['output_dir']}/{stem}",
                    }
                )
    return jobs


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, sort_keys=True) + "\n")


def ensure_output_dirs(out_root, jobs):
    for job in jobs:
        (out_root / job["filename"]).parent.mkdir(parents=True, exist_ok=True)


def job_status(sock_path, job_id):
    resp = request(sock_path, {"op": "jobs"}, timeout=30)
    for job in resp.get("jobs") or []:
        if job.get("id") == job_id:
            return job
    return None


def wait_done(sock_path, job_id, poll_seconds, status_path, batch_row):
    while True:
        job = job_status(sock_path, job_id)
        if job:
            status_path.write_text(
                json.dumps(
                    {
                        "batch": batch_row,
                        "worker_job": job,
                        "updated": time.time(),
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            if job.get("status") in DONE_STATES:
                return job
        time.sleep(poll_seconds)


def run_batch(args):
    plan_path = pathlib.Path(args.plan).expanduser().resolve()
    sock_path = pathlib.Path(args.socket).expanduser().resolve()
    out_root = pathlib.Path(args.out_dir).expanduser().resolve()
    plan = load_plan(plan_path)
    jobs = expand_jobs(plan)
    batch_root = out_root / plan["output_dir"]
    manifest_path = batch_root / "manifest.jsonl"
    submitted_path = batch_root / "submitted.jsonl"
    completed_path = batch_root / "completed.jsonl"
    status_path = batch_root / "status.json"
    ensure_output_dirs(out_root, jobs)
    write_jsonl(manifest_path, jobs)

    if args.dry_run:
        print(f"plan={plan_path}")
        print(f"jobs={len(jobs)}")
        print(f"manifest={manifest_path}")
        return 0

    with submitted_path.open("a", encoding="utf-8") as submitted:
        with completed_path.open("a", encoding="utf-8") as completed:
            for index, job in enumerate(jobs, start=1):
                output_path = out_root / job["filename"]
                if not args.restart and output_path.exists():
                    continue
                payload = {
                    "op": "submit",
                    "backend": job["backend"],
                    "prompt": job["prompt"],
                    "width": job["width"],
                    "height": job["height"],
                    "steps": job["steps"],
                    "guidance": job["guidance"],
                    "seed": job["seed"],
                    "filename": job["filename"],
                }
                resp = request(sock_path, payload, timeout=30)
                if not resp.get("ok"):
                    raise RuntimeError(resp)
                worker_job = resp["job"]
                batch_row = {
                    "batch_index": index,
                    "total": len(jobs),
                    "worker_job_id": worker_job["id"],
                    **job,
                }
                submitted.write(json.dumps(batch_row, sort_keys=True) + "\n")
                submitted.flush()
                print(f"{index}/{len(jobs)} {worker_job['id']} seed={job['seed']} {job['filename']}", flush=True)
                final_job = wait_done(sock_path, worker_job["id"], args.poll_seconds, status_path, batch_row)
                completed.write(json.dumps({"batch": batch_row, "worker_job": final_job}, sort_keys=True) + "\n")
                completed.flush()
                if final_job.get("status") != "done" and not args.keep_going:
                    raise RuntimeError(f"worker job {worker_job['id']} ended as {final_job.get('status')}")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Submit a FLUX batch through the resident unix socket.")
    parser.add_argument("plan")
    parser.add_argument("--socket", default=".fluxd/flux.sock")
    parser.add_argument("--out-dir", default="~/Models/flux-output")
    parser.add_argument("--poll-seconds", type=float, default=8)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--restart", action="store_true", help="ignore existing submitted.jsonl progress")
    parser.add_argument("--keep-going", action="store_true", help="continue after individual job errors")
    args = parser.parse_args()
    return run_batch(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
