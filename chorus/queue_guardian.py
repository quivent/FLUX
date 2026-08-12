#!/usr/bin/env python3
"""Two independent queue guardians: recovery authority and proof auditor."""
import argparse
import json
import os
import pathlib
import signal
import subprocess
import sys
import time


def atomic_json(path, value):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def alive(pidfile):
    try:
        pid = int(pidfile.read_text())
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError):
        return 0


def stop_runner(pid):
    """Stop the runner and its current child before a guarded replacement."""
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except OSError:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            return
    for _ in range(20):
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.25)
    try:
        os.killpg(pid, signal.SIGKILL)
    except OSError:
        pass


def complete(out):
    try:
        state = json.loads((out / "queue-state.json").read_text())
        return state.get("status") == "done" and state.get("completed_jobs") == 48
    except Exception:
        return False


def supervisor(args):
    root, out, run_dir = map(pathlib.Path, (args.root, args.out_dir, args.run_dir))
    pidfile, request = run_dir / "night-run.pid", run_dir / "queue-restart.request"
    while not complete(out):
        pid = alive(pidfile)
        requested = request.exists()
        if not pid or requested:
            if pid and requested:
                stop_runner(pid)
            request.unlink(missing_ok=True)
            log = (run_dir / "night-run.log").open("a")
            child = subprocess.Popen([args.python, str(root / "chorus" / "night_runner.py"),
                                      "--manifest", str(root / "chorus" / "night-run.json"),
                                      "--python", args.python], cwd=root, start_new_session=True,
                                     stdout=log, stderr=subprocess.STDOUT)
            log.close(); pidfile.write_text(str(child.pid) + "\n")
        time.sleep(11)
    return 0


def auditor(args):
    root, out, run_dir = map(pathlib.Path, (args.root, args.out_dir, args.run_dir))
    target, request = out / "queue-audit.json", run_dir / "queue-restart.request"
    prior_count, prior_change = -1, time.time()
    while True:
        try:
            state = json.loads((out / "queue-state.json").read_text())
            count = sum(int(job.get("rendered") or 0) for job in state.get("jobs") or [])
            target_count = sum(int(job.get("target") or 0) for job in state.get("jobs") or [])
            if count != prior_count:
                prior_count, prior_change = count, time.time()
            stale = state.get("status") != "done" and time.time() - prior_change > 240
            if stale:
                request.touch()
            atomic_json(target, {"schema": "flux.queue-audit.v1", "status": "failing" if stale else "healthy",
                        "rendered": count, "target": target_count, "jobs": len(state.get("jobs") or []),
                        "approved": sum(job.get("approved") is True for job in state.get("jobs") or []),
                        "stale_seconds": round(time.time() - prior_change, 1), "measured_at": time.time()})
            if state.get("status") == "done":
                return 0
        except Exception as exc:
            atomic_json(target, {"schema": "flux.queue-audit.v1", "status": "starting",
                                 "error": str(exc), "measured_at": time.time()})
        time.sleep(17)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--role", choices=("supervisor", "auditor"), required=True)
    ap.add_argument("--root", required=True); ap.add_argument("--out-dir", required=True)
    ap.add_argument("--run-dir", required=True); ap.add_argument("--python", default=sys.executable)
    args = ap.parse_args()
    pathlib.Path(args.run_dir).mkdir(parents=True, exist_ok=True)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    return supervisor(args) if args.role == "supervisor" else auditor(args)


if __name__ == "__main__":
    raise SystemExit(main())
