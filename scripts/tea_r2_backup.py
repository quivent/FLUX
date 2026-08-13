#!/usr/bin/env python3
"""Back up Tea's live Beauty and Stallion corpora to Cloudflare R2.

The node filesystem remains the serving origin. R2 is the durability copy:
objects retain their relative paths under stable Tea prefixes, every upload
stores a SHA-256 metadata value, and each verified sweep publishes a complete
inventory manifest and compact status receipt.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import mimetypes
import os
import pathlib
import signal
import threading
import time
from dataclasses import dataclass


SCHEMA = "tea.r2-backup.v1"
TEMP_SUFFIXES = {".part", ".tmp", ".lock", ".swp"}


@dataclass(frozen=True)
class Dataset:
    name: str
    root: pathlib.Path
    prefix: str


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: pathlib.Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def load_json(path: pathlib.Path, fallback: object) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return fallback


def client():
    import boto3
    from botocore.config import Config

    required = ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_ENDPOINT", "R2_BUCKET")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise RuntimeError("missing R2 environment: " + ", ".join(missing))
    return boto3.client(
        "s3",
        endpoint_url=os.environ["R2_ENDPOINT"],
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
        config=Config(retries={"max_attempts": 8, "mode": "standard"}, max_pool_connections=32),
    ), os.environ["R2_BUCKET"]


def settled_inventory(dataset: Dataset, settle_seconds: float, prior: dict[str, dict]) -> dict[str, dict]:
    now = time.time()
    inventory: dict[str, dict] = {}
    if not dataset.root.is_dir():
        raise RuntimeError(f"dataset root is absent: {dataset.root}")
    for path in sorted(dataset.root.rglob("*")):
        if path.is_symlink() or not path.is_file() or path.suffix.lower() in TEMP_SUFFIXES:
            continue
        relative = path.relative_to(dataset.root).as_posix()
        if any(part.startswith(".") for part in pathlib.PurePosixPath(relative).parts):
            continue
        try:
            before = path.stat()
        except OSError:
            continue
        if now - before.st_mtime < settle_seconds:
            continue
        signature = f"{before.st_size}:{before.st_mtime_ns}"
        cached = prior.get(f"{dataset.name}:{relative}", {})
        digest = cached.get("sha256") if cached.get("signature") == signature else None
        if not digest:
            digest = sha256_file(path)
            try:
                after = path.stat()
            except OSError:
                continue
            if (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
                continue
        inventory[relative] = {
            "path": str(path),
            "key": f"{dataset.prefix.rstrip('/')}/{relative}",
            "bytes": before.st_size,
            "mtime_ns": before.st_mtime_ns,
            "signature": signature,
            "sha256": digest,
        }
    return inventory


def remote_objects(s3, bucket: str, prefix: str) -> dict[str, int]:
    objects: dict[str, int] = {}
    token = None
    while True:
        request = {"Bucket": bucket, "Prefix": prefix.rstrip("/") + "/", "MaxKeys": 1000}
        if token:
            request["ContinuationToken"] = token
        page = s3.list_objects_v2(**request)
        for row in page.get("Contents") or []:
            objects[str(row["Key"])] = int(row.get("Size", 0))
        if not page.get("IsTruncated"):
            return objects
        token = page.get("NextContinuationToken")
        if not token:
            raise RuntimeError(f"truncated R2 listing without continuation token: {prefix}")


def upload_one(s3, bucket: str, entry: dict) -> None:
    path = pathlib.Path(entry["path"])
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    s3.upload_file(
        str(path), bucket, entry["key"],
        ExtraArgs={"ContentType": content_type, "Metadata": {"sha256": entry["sha256"]}},
    )


def verify_one(s3, bucket: str, entry: dict) -> str | None:
    try:
        head = s3.head_object(Bucket=bucket, Key=entry["key"])
    except Exception as exc:  # noqa: BLE001 - surfaced in the receipt
        return f"{entry['key']}: HEAD failed: {exc}"
    if int(head.get("ContentLength", -1)) != int(entry["bytes"]):
        return f"{entry['key']}: byte length differs"
    if (head.get("Metadata") or {}).get("sha256") != entry["sha256"]:
        return f"{entry['key']}: SHA-256 metadata differs"
    return None


def inventory_digest(entries: list[dict]) -> str:
    digest = hashlib.sha256()
    for entry in sorted(entries, key=lambda item: item["key"]):
        digest.update(f"{entry['key']}\0{entry['bytes']}\0{entry['sha256']}\n".encode())
    return digest.hexdigest()


def run_sweep(
    s3, bucket: str, datasets: list[Dataset], state_dir: pathlib.Path,
    workers: int, settle_seconds: float, verify: bool,
) -> dict:
    ledger_path = state_dir / "ledger.json"
    prior = load_json(ledger_path, {})
    if not isinstance(prior, dict):
        prior = {}
    inventories: dict[str, dict[str, dict]] = {}
    for dataset in datasets:
        inventories[dataset.name] = settled_inventory(dataset, settle_seconds, prior)

    remote: dict[str, int] = {}
    for dataset in datasets:
        remote.update(remote_objects(s3, bucket, dataset.prefix))
    entries = [entry for inventory in inventories.values() for entry in inventory.values()]
    pending = [entry for entry in entries if remote.get(entry["key"]) != entry["bytes"]]
    uploaded = 0
    errors: list[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(upload_one, s3, bucket, entry): entry for entry in pending}
        for future in concurrent.futures.as_completed(futures):
            entry = futures[future]
            try:
                future.result()
                uploaded += 1
            except Exception as exc:  # noqa: BLE001 - surfaced in the receipt
                errors.append(f"{entry['key']}: upload failed: {exc}")

    remote = {}
    for dataset in datasets:
        remote.update(remote_objects(s3, bucket, dataset.prefix))
    missing = [entry["key"] for entry in entries if remote.get(entry["key"]) != entry["bytes"]]
    if verify and not missing:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            for failure in pool.map(lambda entry: verify_one(s3, bucket, entry), entries):
                if failure:
                    errors.append(failure)

    generated_at = time.time()
    summary = {}
    for dataset in datasets:
        rows = list(inventories[dataset.name].values())
        summary[dataset.name] = {
            "root": str(dataset.root), "prefix": dataset.prefix,
            "objects": len(rows), "bytes": sum(row["bytes"] for row in rows),
            "inventory_sha256": inventory_digest(rows),
        }
    status = {
        "schema": SCHEMA, "status": "healthy" if not errors and not missing else "degraded",
        "generated_at": generated_at, "bucket": bucket, "datasets": summary,
        "objects": len(entries), "bytes": sum(entry["bytes"] for entry in entries),
        "uploaded_this_sweep": uploaded, "verified": bool(verify and not errors and not missing),
        "missing": missing[:200], "errors": errors[:200],
    }
    manifest = {
        "schema": SCHEMA, "generated_at": generated_at, "bucket": bucket,
        "datasets": summary,
        "objects": [
            {key: entry[key] for key in ("key", "bytes", "mtime_ns", "sha256")}
            for entry in sorted(entries, key=lambda item: item["key"])
        ],
    }
    atomic_json(state_dir / "archive-manifest.json", manifest)
    atomic_json(state_dir / "backup-status.json", status)
    atomic_json(ledger_path, {
        f"{name}:{relative}": {"signature": entry["signature"], "sha256": entry["sha256"]}
        for name, inventory in inventories.items() for relative, entry in inventory.items()
    })
    for filename in ("archive-manifest.json", "backup-status.json"):
        source = state_dir / filename
        s3.upload_file(
            str(source), bucket, f"tea/state/{filename}",
            ExtraArgs={"ContentType": "application/json", "Metadata": {"sha256": sha256_file(source)}},
        )
    return status


def main() -> int:
    home = pathlib.Path.home()
    parser = argparse.ArgumentParser(description="Back up Tea Beauty and Stallion assets to R2")
    parser.add_argument("--beauty-root", type=pathlib.Path, default=home / "anime-output")
    parser.add_argument("--stallion-root", type=pathlib.Path, default=home / "models/tea-motion-output")
    parser.add_argument("--state-dir", type=pathlib.Path, default=home / "tea-motion/run/r2-backup")
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--settle-seconds", type=float, default=5.0)
    parser.add_argument("--interval", type=float, default=30.0)
    parser.add_argument("--verify-interval", type=float, default=600.0)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    datasets = [
        Dataset("beauty", args.beauty_root.expanduser(), "tea/beauty"),
        Dataset("stallion", args.stallion_root.expanduser(), "tea/studies/stallion"),
    ]
    s3, bucket = client()
    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    signal.signal(signal.SIGINT, lambda *_: stopping.set())
    last_verify = 0.0
    while True:
        verify = args.verify or time.time() - last_verify >= args.verify_interval
        status = run_sweep(
            s3, bucket, datasets, args.state_dir.expanduser(),
            max(1, min(args.workers, 32)), args.settle_seconds, verify,
        )
        print(json.dumps(status, sort_keys=True), flush=True)
        if verify:
            last_verify = time.time()
        if args.once:
            return 0 if status["status"] == "healthy" else 2
        if stopping.wait(args.interval):
            final = run_sweep(
                s3, bucket, datasets, args.state_dir.expanduser(),
                max(1, min(args.workers, 32)), 0.0, True,
            )
            print(json.dumps(final, sort_keys=True), flush=True)
            return 0 if final["status"] == "healthy" else 2


if __name__ == "__main__":
    raise SystemExit(main())
