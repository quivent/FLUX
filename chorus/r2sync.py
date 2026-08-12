#!/usr/bin/env python3
"""Stream Chorus frames and the state that explains them to Cloudflare R2.

R2 is authoritative.  The local ledger is only a restart hint: every daemon
start and every periodic verification lists the remote prefix, repairs objects
that disappeared there, and writes a machine-readable coverage receipt.

    r2sync.py                         continuous stream
    r2sync.py --once --verify         final flush; non-zero unless complete

SIGTERM requests one final verified sweep before exit.  chorus/up.sh stops the
producer first and gives this process time to finish that sweep.
"""
import argparse
import hashlib
import json
import mimetypes
import os
import pathlib
import signal
import threading
import time

PREFIX = os.environ.get("R2_PREFIX", "chorus")
HERE = pathlib.Path(__file__).resolve().parent

# These files are the artistic memory.  A frame without them survives as an
# image but not as part of a developing practice.
LIVE_STATE_FILES = (
    "challengers.json",
    "creative-drift.jsonl",
    "drift-control.json",
    "drift-status.json",
    "operator-feedback.jsonl",
    "panel-decisions.json",
    "picks.json",
    "taste-log.jsonl",
    "taste-status.json",
    "trial-ledger.jsonl",
)
PROTOCOL_SUFFIXES = {".md", ".py", ".sh"}
SHEET_SUFFIXES = {".json", ".jpg", ".jpeg", ".png"}
STATUS_NAME = "r2-status.json"
MANIFEST_NAME = "archive-manifest.json"
SCHEMA = "chorus.r2-stream.v1"


def client():
    import boto3
    from botocore.config import Config
    missing = [key for key in ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_ENDPOINT", "R2_BUCKET")
               if not os.environ.get(key)]
    if missing:
        raise SystemExit("missing credentials in the environment: " + ", ".join(missing))
    return boto3.client(
        "s3",
        endpoint_url=os.environ["R2_ENDPOINT"],
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
        config=Config(retries={"max_attempts": 5, "mode": "standard"}, max_pool_connections=8),
    ), os.environ["R2_BUCKET"]


def atomic_json(path, value):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def load_ledger(path):
    try:
        return set(json.loads(pathlib.Path(path).read_text()))
    except (OSError, ValueError, TypeError):
        return set()


def save_ledger(path, done):
    atomic_json(path, sorted(done))


def sha256_file(path):
    digest = hashlib.sha256()
    with pathlib.Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def inventory_sha256(names):
    return hashlib.sha256(("\n".join(sorted(names)) + "\n").encode()).hexdigest()


def remote_frame_names(s3, bucket, prefix=PREFIX):
    """List the remote truth, following S3 continuation tokens."""
    base = f"{prefix}/frames/"
    names, token = set(), None
    while True:
        request = {"Bucket": bucket, "Prefix": base, "MaxKeys": 1000}
        if token:
            request["ContinuationToken"] = token
        page = s3.list_objects_v2(**request)
        for item in page.get("Contents") or []:
            key = str(item.get("Key") or "")
            if key.startswith(base) and key != base:
                names.add(key[len(base):])
        if not page.get("IsTruncated"):
            return names
        token = page.get("NextContinuationToken")
        if not token:
            raise RuntimeError("R2 frame listing was truncated without a continuation token")


def settled_frames(out_dir, settle_seconds=2.0, now=None):
    now = time.time() if now is None else now
    settled, young = {}, {}
    for path in pathlib.Path(out_dir).glob("*.png"):
        try:
            age = now - path.stat().st_mtime
        except OSError:
            continue
        (settled if age >= settle_seconds else young)[path.name] = path
    return settled, young


def state_sources(out_dir, protocol_dir=HERE):
    """Return durable key→path mappings without ever scanning credential files."""
    out_dir, protocol_dir = pathlib.Path(out_dir), pathlib.Path(protocol_dir)
    sources = {}
    for name in LIVE_STATE_FILES:
        path = out_dir / name
        if path.is_file():
            sources[f"state/{name}"] = path
    sheets = out_dir / "_sheets"
    if sheets.is_dir():
        for path in sorted(sheets.iterdir()):
            if path.is_file() and path.suffix.lower() in SHEET_SUFFIXES:
                sources[f"state/sheets/{path.name}"] = path
    for path in sorted(protocol_dir.iterdir() if protocol_dir.is_dir() else []):
        if path.is_file() and path.suffix.lower() in PROTOCOL_SUFFIXES:
            sources[f"state/protocol/{path.name}"] = path
    return sources


def content_type(path):
    return mimetypes.guess_type(str(path))[0] or "application/octet-stream"


def upload_file(s3, bucket, path, key):
    s3.upload_file(str(path), bucket, key, ExtraArgs={"ContentType": content_type(path)})


def stream_state(s3, bucket, out_dir, prefix, prior_hashes, log, protocol_dir=HERE):
    confirmed, errors, uploaded = {}, [], 0
    for relative, path in state_sources(out_dir, protocol_dir).items():
        key = f"{prefix}/{relative}"
        try:
            digest = sha256_file(path)
            info = {"key": key, "sha256": digest, "bytes": path.stat().st_size}
            if prior_hashes.get(key) != digest:
                upload_file(s3, bucket, path, key)
                uploaded += 1
            confirmed[key] = info
        except Exception as exc:  # one changing ledger must not block all frames
            errors.append(f"{relative}: {exc}")
            log(f"state failed {relative}: {exc}")
    return confirmed, errors, uploaded


def write_receipts(s3, bucket, out_dir, prefix, payload, state_entries):
    sheets = pathlib.Path(out_dir) / "_sheets"
    manifest_path = sheets / MANIFEST_NAME
    status_path = pathlib.Path(out_dir) / STATUS_NAME
    manifest = {
        "schema": SCHEMA,
        "generated_at": payload["last_sweep_at"],
        "bucket": bucket,
        "prefix": prefix,
        "frames": payload["frames"],
        "frame_inventory_sha256": payload["frame_inventory_sha256"],
        "state": sorted(state_entries.values(), key=lambda item: item["key"]),
    }
    atomic_json(manifest_path, manifest)
    atomic_json(status_path, payload)
    upload_file(s3, bucket, manifest_path, f"{prefix}/state/{MANIFEST_NAME}")
    upload_file(s3, bucket, status_path, f"{prefix}/state/{STATUS_NAME}")


def sweep(s3, bucket, out_dir, remote_frames, ledger_path, log, prefix=PREFIX,
          settle_seconds=2.0, remote_verified=False, state_hashes=None,
          protocol_dir=HERE, force_verify=False, last_success_at=None):
    """Perform one complete durability transaction and return its receipt."""
    state_hashes = state_hashes or {}
    errors = []
    if force_verify:
        try:
            remote_frames = remote_frame_names(s3, bucket, prefix)
            remote_verified = True
        except Exception as exc:
            remote_verified = False
            errors.append(f"remote inventory: {exc}")
            log(f"remote inventory failed: {exc}")

    settled, young = settled_frames(out_dir, settle_seconds)
    sent = 0
    for name, path in sorted(settled.items()):
        if name in remote_frames:
            continue
        try:
            upload_file(s3, bucket, path, f"{prefix}/frames/{name}")
            remote_frames.add(name)
            sent += 1
        except Exception as exc:
            errors.append(f"frame {name}: {exc}")
            log(f"failed {name}: {exc}")
    save_ledger(ledger_path, remote_frames)

    # Object listing covers frames. Re-upload state on every remote proof so a
    # dashboard deletion or partially-applied lifecycle policy repairs itself
    # without needing thousands of HEAD requests.
    hashes_for_this_sweep = {} if force_verify else state_hashes
    confirmed, state_errors, state_uploaded = stream_state(
        s3, bucket, out_dir, prefix, hashes_for_this_sweep, log, protocol_dir)
    errors.extend(state_errors)
    missing = sorted(set(settled) - remote_frames)
    now = time.time()
    healthy = remote_verified and not missing and not errors
    succeeded_at = now if healthy else last_success_at
    receipt = {
        "schema": SCHEMA,
        "status": "healthy" if healthy else "degraded",
        "last_sweep_at": now,
        "last_success_at": succeeded_at,
        "last_remote_verify_at": now if remote_verified else None,
        "remote_verified": remote_verified,
        "bucket": bucket,
        "prefix": prefix,
        "frames": {
            "local": len(settled) + len(young),
            "settled_local": len(settled),
            "waiting_to_settle": len(young),
            "remote": len(remote_frames),
            "missing_settled": len(missing),
            "missing_names": missing[:100],
            "uploaded_this_sweep": sent,
        },
        "state": {
            "files": len(confirmed),
            "uploaded_this_sweep": state_uploaded,
        },
        "frame_inventory_sha256": inventory_sha256(remote_frames),
        "errors": errors,
    }
    try:
        write_receipts(s3, bucket, out_dir, prefix, receipt, confirmed)
    except Exception as exc:
        receipt["status"] = "degraded"
        receipt["errors"].append(f"receipt: {exc}")
        atomic_json(pathlib.Path(out_dir) / STATUS_NAME, receipt)
        log(f"receipt failed: {exc}")
    if sent or state_uploaded or force_verify:
        log(f"streamed {sent} frame(s), {state_uploaded} state file(s); "
            f"{len(remote_frames)} remote, {len(missing)} settled missing")
    return receipt, remote_frames, {item["key"]: item["sha256"] for item in confirmed.values()}


def main():
    ap = argparse.ArgumentParser(description="Continuously verify and stream Chorus to R2.")
    ap.add_argument("--out-dir", default=str(pathlib.Path.home() / "models" / "flux-output"))
    ap.add_argument("--interval", type=float, default=15)
    ap.add_argument("--verify-interval", type=float, default=600)
    ap.add_argument("--settle-seconds", type=float, default=2)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--verify", action="store_true",
                    help="list R2 after the flush and fail unless every settled frame and state file is durable")
    args = ap.parse_args()

    out_dir = pathlib.Path(args.out_dir).expanduser()
    ledger_path = out_dir / "_sheets" / "r2-ledger.json"

    def log(message):
        print(f"{time.strftime('%H:%M:%S')} {message}", flush=True)

    s3, bucket = client()
    remote_frames, remote_verified = load_ledger(ledger_path), False
    try:
        remote_frames = remote_frame_names(s3, bucket, PREFIX)
        remote_verified = True
        log(f"reconciled {len(remote_frames)} remote frame(s) in {bucket}/{PREFIX}")
    except Exception as exc:
        log(f"remote reconciliation unavailable; ledger is an unverified hint: {exc}")

    stopping = threading.Event()
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, lambda _signum, _frame: stopping.set())
        signal.signal(signal.SIGINT, lambda _signum, _frame: stopping.set())

    state_hashes, last_verify, last_success_at = {}, 0.0, None
    while True:
        verify_now = args.verify or not remote_verified or time.time() - last_verify >= args.verify_interval
        receipt, remote_frames, state_hashes = sweep(
            s3, bucket, out_dir, remote_frames, ledger_path, log,
            settle_seconds=args.settle_seconds, remote_verified=remote_verified,
            state_hashes=state_hashes, force_verify=verify_now,
            last_success_at=last_success_at)
        remote_verified = bool(receipt["remote_verified"])
        last_success_at = receipt.get("last_success_at")
        if remote_verified:
            last_verify = time.time()
        if args.once:
            return 0 if (not args.verify or receipt["status"] == "healthy") else 2
        if stopping.wait(args.interval):
            # A producer stopped immediately before us may have left a frame in
            # the settle window. Give it that window, then perform one final
            # remote-authoritative transaction.
            stopping.wait(args.settle_seconds)
            receipt, _, _ = sweep(
                s3, bucket, out_dir, remote_frames, ledger_path, log,
                settle_seconds=0, remote_verified=remote_verified,
                state_hashes=state_hashes, force_verify=True,
                last_success_at=last_success_at)
            return 0 if receipt["status"] == "healthy" else 2


if __name__ == "__main__":
    raise SystemExit(main())
