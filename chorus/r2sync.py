#!/usr/bin/env python3
"""r2sync — stream finished frames to Cloudflare R2 as they land.

The node's disk is persistent but not durable in any sense worth relying on:
delete_node is a crypto-erase, /scratch does not survive a stop at all, and a
volume is one accident from taking four thousand frames with it. Everything the
loop makes should exist somewhere that outlives the node.

Streaming rather than batching, because a batch job is a thing that has not run
yet. Each frame is uploaded within seconds of being written, so the worst case
is losing the render in flight rather than losing an hour.

Credentials arrive in the environment and never on a command line -- command
lines are recorded, environments are not:

    R2_ACCESS_KEY_ID  R2_SECRET_ACCESS_KEY  R2_ENDPOINT  R2_BUCKET

    chorus/r2sync.py --once     upload anything missing, then exit
    chorus/r2sync.py            watch and stream
"""
import argparse
import json
import os
import pathlib
import time

PREFIX = os.environ.get("R2_PREFIX", "chorus")


def client():
    import boto3
    from botocore.config import Config
    missing = [k for k in ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_ENDPOINT", "R2_BUCKET")
               if not os.environ.get(k)]
    if missing:
        raise SystemExit("missing credentials in the environment: " + ", ".join(missing))
    return boto3.client(
        "s3",
        endpoint_url=os.environ["R2_ENDPOINT"],
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        # R2 ignores the region but the signer insists on one.
        region_name="auto",
        config=Config(retries={"max_attempts": 5, "mode": "standard"}, max_pool_connections=8),
    ), os.environ["R2_BUCKET"]


def load_ledger(path):
    try:
        return set(json.loads(pathlib.Path(path).read_text()))
    except (OSError, ValueError):
        return set()


def save_ledger(path, done):
    # A local ledger, not a HEAD request per file: at four thousand frames a
    # round trip each would cost more than the uploads.
    pathlib.Path(path).write_text(json.dumps(sorted(done)))


def sweep(s3, bucket, out_dir, done, ledger_path, log, settle=2.0):
    sent = 0
    for path in sorted(out_dir.glob("*.png")):
        if path.name in done:
            continue
        # A file still being written would upload truncated. The loop renames
        # atomically, but an mtime younger than the settle window is skipped
        # anyway -- it will be caught on the next pass a second later.
        try:
            if time.time() - path.stat().st_mtime < settle:
                continue
        except OSError:
            continue
        key = f"{PREFIX}/frames/{path.name}"
        try:
            s3.upload_file(str(path), bucket, key,
                           ExtraArgs={"ContentType": "image/png"})
        except Exception as exc:
            log(f"failed {path.name}: {exc}")
            continue
        done.add(path.name)
        sent += 1
        if sent % 25 == 0:
            save_ledger(ledger_path, done)
    # The ledgers and the taste log are small and change constantly; they are
    # the part that explains the frames, so they ride along every pass.
    for side in ("creative-drift.jsonl", "taste-log.jsonl", "picks.json",
                 "challengers.json", "drift-status.json"):
        f = out_dir / side
        if f.exists():
            try:
                s3.upload_file(str(f), bucket, f"{PREFIX}/state/{side}")
            except Exception as exc:
                log(f"failed {side}: {exc}")
    if sent:
        save_ledger(ledger_path, done)
        log(f"streamed {sent} frame(s); {len(done)} total in {PREFIX}/frames/")
    return sent


def main():
    ap = argparse.ArgumentParser(description="Stream frames to Cloudflare R2 as they land.")
    ap.add_argument("--out-dir", default=str(pathlib.Path.home() / "models" / "flux-output"))
    ap.add_argument("--interval", type=float, default=15)
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()

    out_dir = pathlib.Path(args.out_dir).expanduser()
    ledger_path = out_dir / "_sheets" / "r2-ledger.json"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)

    def log(message):
        line = f"{time.strftime('%H:%M:%S')} {message}"
        print(line, flush=True)

    s3, bucket = client()
    done = load_ledger(ledger_path)
    log(f"streaming to {bucket}/{PREFIX} ({len(done)} already sent)")

    while True:
        try:
            sweep(s3, bucket, out_dir, done, ledger_path, log)
        except Exception as exc:
            log(f"sweep error: {exc}")
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
