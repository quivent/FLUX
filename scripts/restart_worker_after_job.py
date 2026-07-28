#!/usr/bin/env python3
"""Restart the worker once a named job reaches a terminal state."""

import argparse
import json
import subprocess
import time
import urllib.request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("job_id")
    parser.add_argument("--api", default="http://127.0.0.1:7861/api/jobs")
    parser.add_argument("--interval", type=float, default=5)
    args = parser.parse_args()
    while True:
        try:
            with urllib.request.urlopen(args.api, timeout=5) as response:
                jobs = json.load(response).get("jobs", [])
            job = next((item for item in jobs if item.get("id") == args.job_id), None)
            if job and str(job.get("status", "")).lower() in {"done", "error", "cancelled"}:
                break
        except (OSError, ValueError):
            pass
        time.sleep(max(1, args.interval))
    subprocess.run(["systemctl", "restart", "flux-worker.service"], check=True)


if __name__ == "__main__":
    main()
