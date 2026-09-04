#!/usr/bin/env python3
"""Rolling Tea ledger: preserve hive, images, jury, and training to R2 with an index.

Layout
  r2://governor/ledger/v1/INDEX.jsonl
  r2://governor/ledger/v1/INDEX.json
  r2://governor/ledger/v1/objects/<kind>/<YYYY>/<MM>/<DD>/<id>/...
Public copies under r2://beauty-flux/site/ledger/v1/ (CDN).

Kinds: hive, images, jury, training, discourse
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
FLUX = HOME / "CLIs" / "flux"
OUT = Path(os.environ.get("FLUX_OUTPUT_DIR") or HOME / "models" / "flux-output")
FLUXD = FLUX / ".fluxd"
STATE_PATH = FLUXD / "tea_ledger_state.json"
INDEX_JSONL = FLUXD / "tea_ledger_INDEX.jsonl"
INDEX_JSON = FLUXD / "tea_ledger_INDEX.json"
PUBLIC_BASE = os.environ.get(
    "TEA_R2_PUBLIC",
    "https://pub-197bed319eda457da858ab89c061ed38.r2.dev/site",
)
LEDGER = "ledger/v1"
POLL = float(os.environ.get("TEA_LEDGER_POLL_S") or 45)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path: Path, fallback):
    try:
        return json.loads(path.read_text())
    except Exception:
        return fallback


def save_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2) + "\n")
    tmp.replace(path)


def r2_push(local: Path, dest: str, bucket: str) -> None:
    env = os.environ.copy()
    env["R2_BUCKET"] = bucket
    subprocess.run(
        ["gemstone", "r2", "push", str(local), dest],
        check=True,
        env=env,
        timeout=180,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )


def push_both(local: Path, rel: str) -> dict:
    vault = f"{LEDGER}/{rel}"
    public = f"site/{LEDGER}/{rel}"
    r2_push(local, vault, "governor")
    r2_push(local, public, "beauty-flux")
    return {
        "r2_vault": f"r2://governor/{vault}",
        "r2_public": f"r2://beauty-flux/{public}",
        "url": f"{PUBLIC_BASE}/{LEDGER}/{rel}",
    }


def snapshot_sqlite(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    src_conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    dst_conn = sqlite3.connect(dest)
    src_conn.backup(dst_conn)
    dst_conn.close()
    src_conn.close()


class Ledger:
    def __init__(self):
        FLUXD.mkdir(parents=True, exist_ok=True)
        self.state = load_json(STATE_PATH, {"files": {}, "images": {}})
        self.entries = []
        if INDEX_JSONL.exists():
            for line in INDEX_JSONL.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    self.entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    def persist_state(self) -> None:
        save_json(STATE_PATH, self.state)

    def add(self, rec: dict) -> None:
        rec.setdefault("schema", "tea.ledger.object.v1")
        rec.setdefault("ts", int(time.time()))
        self.entries.append(rec)
        with INDEX_JSONL.open("a") as handle:
            handle.write(json.dumps(rec, sort_keys=True) + "\n")

    def compact(self) -> dict:
        counts = {}
        for rec in self.entries:
            kind = rec.get("kind") or "other"
            counts[kind] = counts.get(kind, 0) + 1
        latest = list(reversed(self.entries[-80:]))
        by_kind = {}
        for rec in reversed(self.entries):
            kind = rec.get("kind") or "other"
            by_kind.setdefault(kind, [])
            if len(by_kind[kind]) < 24:
                by_kind[kind].append(rec)
        doc = {
            "schema": "tea.ledger.v1",
            "updated_at": int(time.time()),
            "updated_iso": utc_now().isoformat(),
            "public_base": f"{PUBLIC_BASE}/{LEDGER}",
            "vault": f"r2://governor/{LEDGER}/",
            "counts": counts,
            "n": len(self.entries),
            "latest": latest,
            "by_kind": by_kind,
        }
        save_json(INDEX_JSON, doc)
        return doc

    def publish_index(self) -> None:
        doc = self.compact()
        push_both(INDEX_JSON, "INDEX.json")
        if INDEX_JSONL.exists():
            push_both(INDEX_JSONL, "INDEX.jsonl")
        return doc


def object_id(kind: str, stem: str) -> str:
    stamp = utc_now().strftime("%Y%m%dT%H%M%SZ")
    safe = "".join(ch if ch.isalnum() or ch in "-._" else "-" for ch in stem)[:80]
    return f"{stamp}-{kind}-{safe}"


def rel_for(kind: str, oid: str, name: str) -> str:
    now = utc_now()
    return f"objects/{kind}/{now:%Y}/{now:%m}/{now:%d}/{oid}/{name}"


def file_sig(path: Path) -> dict:
    st = path.stat()
    return {"size": st.st_size, "mtime": int(st.st_mtime)}


def ship_whole(ledger: Ledger, kind: str, path: Path, title: str, tags: list[str]) -> None:
    key = str(path)
    sig = file_sig(path)
    prev = ledger.state["files"].get(key)
    if prev and prev.get("size") == sig["size"] and prev.get("mtime") == sig["mtime"]:
        return
    digest = sha256_file(path)
    if prev and prev.get("sha256") == digest:
        ledger.state["files"][key] = {**sig, "sha256": digest}
        return
    oid = object_id(kind, path.stem)
    rel = rel_for(kind, oid, path.name)
    locs = push_both(path, rel)
    rec = {
        "id": oid,
        "kind": kind,
        "title": title,
        "src": key,
        "bytes": sig["size"],
        "sha256": digest,
        "tags": tags,
        **locs,
    }
    ledger.add(rec)
    ledger.state["files"][key] = {**sig, "sha256": digest, "id": oid}
    print(f"ledger {kind} {path.name} {sig['size']} -> {locs['url']}", flush=True)


def ship_jsonl_delta(ledger: Ledger, kind: str, path: Path, title: str, tags: list[str]) -> None:
    if not path.exists():
        return
    key = str(path)
    size = path.stat().st_size
    prev = ledger.state["files"].get(key) or {}
    offset = int(prev.get("offset") or 0)
    if size < offset:
        offset = 0
    if size == offset:
        return
    with path.open("rb") as handle:
        handle.seek(offset)
        chunk = handle.read()
    if not chunk.strip():
        ledger.state["files"][key] = {"offset": size, "size": size, "mtime": int(path.stat().st_mtime)}
        return
    digest = sha256_bytes(chunk)
    oid = object_id(kind, f"{path.stem}-{offset}")
    name = f"{path.stem}-{offset}-{size}.jsonl"
    staging = FLUXD / "ledger-staging"
    staging.mkdir(exist_ok=True)
    dest = staging / name
    dest.write_bytes(chunk)
    rel = rel_for(kind, oid, name)
    locs = push_both(dest, rel)
    rec = {
        "id": oid,
        "kind": kind,
        "title": title,
        "src": key,
        "byte_range": [offset, size],
        "bytes": len(chunk),
        "sha256": digest,
        "tags": tags,
        "lines": chunk.count(b"\n"),
        **locs,
    }
    ledger.add(rec)
    ledger.state["files"][key] = {
        "offset": size,
        "size": size,
        "mtime": int(path.stat().st_mtime),
        "sha256": digest,
        "id": oid,
    }
    print(f"ledger {kind} {name} +{len(chunk)}B -> {locs['url']}", flush=True)


def ship_sqlite(ledger: Ledger, kind: str, path: Path, title: str, tags: list[str]) -> None:
    if not path.exists():
        return
    key = str(path)
    sig = file_sig(path)
    prev = ledger.state["files"].get(key)
    if prev and prev.get("size") == sig["size"] and prev.get("mtime") == sig["mtime"]:
        return
    staging = FLUXD / "ledger-staging"
    staging.mkdir(exist_ok=True)
    snap = staging / (path.stem + ".sqlite3")
    snapshot_sqlite(path, snap)
    digest = sha256_file(snap)
    if prev and prev.get("sha256") == digest:
        ledger.state["files"][key] = {**sig, "sha256": digest}
        return
    oid = object_id(kind, path.stem)
    rel = rel_for(kind, oid, path.name)
    locs = push_both(snap, rel)
    rec = {
        "id": oid,
        "kind": kind,
        "title": title,
        "src": key,
        "bytes": snap.stat().st_size,
        "sha256": digest,
        "tags": tags,
        **locs,
    }
    ledger.add(rec)
    ledger.state["files"][key] = {**sig, "sha256": digest, "id": oid}
    print(f"ledger {kind} {path.name} snapshot -> {locs['url']}", flush=True)


def ship_images(ledger: Ledger) -> int:
    n = 0
    collections = OUT / "collections"
    if not collections.is_dir():
        return 0
    for png in collections.glob("*/protocol-*.png"):
        if any(part.startswith("_") for part in png.parts):
            continue
        key = png.relative_to(OUT).as_posix()
        sig = file_sig(png)
        prev = ledger.state["images"].get(key)
        if prev and prev.get("size") == sig["size"] and prev.get("mtime") == sig["mtime"]:
            continue
        rel = key  # collections/<slug>/<file>
        url = f"{PUBLIC_BASE}/{rel}"
        rec = {
            "id": object_id("images", png.stem),
            "kind": "images",
            "title": png.name,
            "src": str(png),
            "collection": png.parent.name,
            "bytes": sig["size"],
            "sha256": sha256_file(png),
            "tags": ["images", png.parent.name],
            "r2_vault": f"r2://governor/outputs/{rel}",
            "r2_public": f"r2://beauty-flux/site/{rel}",
            "url": url,
        }
        # New stills: copy into ledger objects as well as the collection path.
        if not prev:
            oid = rec["id"]
            obj_rel = rel_for("images", oid, png.name)
            try:
                locs = push_both(png, obj_rel)
                rec.update(locs)
                rec["collection_url"] = url
            except subprocess.CalledProcessError as exc:
                print(f"ledger image push fail {png.name}: {exc}", flush=True)
                continue
        ledger.add(rec)
        ledger.state["images"][key] = {**sig, "id": rec["id"]}
        n += 1
        print(f"ledger images {png.name}", flush=True)
    return n


def ingest_existing_catalog(ledger: Ledger) -> int:
    catalog = OUT / "catalogs" / "tea.json"
    if not catalog.exists():
        return 0
    data = load_json(catalog, {})
    n = 0
    for slug, items in (data.get("assets") or {}).items():
        for item in items:
            rel = item.get("rel") or ""
            if not rel.endswith(".png"):
                continue
            if rel in ledger.state["images"]:
                continue
            rec = {
                "id": object_id("images", Path(rel).stem),
                "kind": "images",
                "title": Path(rel).name,
                "src": str(OUT / rel),
                "collection": slug,
                "bytes": item.get("bytes"),
                "sha256": item.get("sha256"),
                "tags": ["images", slug, "catalog"],
                "r2_vault": item.get("r2_vault"),
                "r2_public": item.get("r2_public"),
                "url": item.get("url"),
            }
            ledger.add(rec)
            ledger.state["images"][rel] = {
                "size": item.get("bytes"),
                "mtime": item.get("mtime"),
                "id": rec["id"],
            }
            n += 1
    return n


def scan_once(ledger: Ledger, publish: bool) -> dict:
    ingest_existing_catalog(ledger)
    ship_images(ledger)

    hive_drive = HOME / "hive-research" / "logs" / "dual-seat-drive.jsonl"
    ship_jsonl_delta(ledger, "hive", hive_drive, "Hive dual-seat drive", ["hive", "governor", "qwen"])

    cal = OUT / "collections" / "microgreens" / "jury_calibration.jsonl"
    ship_jsonl_delta(ledger, "hive", cal, "Microgreens hive calibration", ["hive", "jury", "microgreens"])

    audit = OUT / "collections" / "microgreens" / "audit.jsonl"
    ship_jsonl_delta(ledger, "jury", audit, "Microgreens audit.jsonl", ["jury", "microgreens"])

    sqlite = OUT / "collections" / "microgreens" / "jury.sqlite3"
    ship_sqlite(ledger, "jury", sqlite, "Microgreens jury.sqlite3", ["jury", "microgreens"])

    train = FLUXD / "governor_train_stream.json"
    ship_whole(ledger, "training", train, "Governor spectral training stream", ["training", "governor"])

    discourse = HOME / ".gemstone" / "discourse" / "discourse.db"
    if discourse.exists():
        ship_sqlite(ledger, "discourse", discourse, "Gemstone discourse.db", ["discourse", "hive"])

    ledger.persist_state()
    if publish:
        return ledger.publish_index()
    return ledger.compact()


def find_entries(query: str, limit: int = 20) -> list:
    q = query.lower()
    hits = []
    if not INDEX_JSONL.exists():
        return hits
    for line in reversed(INDEX_JSONL.read_text().splitlines()):
        if not line.strip():
            continue
        if q not in line.lower():
            continue
        try:
            hits.append(json.loads(line))
        except json.JSONDecodeError:
            continue
        if len(hits) >= limit:
            break
    return hits


def main() -> None:
    p = argparse.ArgumentParser(description="Rolling Tea R2 ledger")
    p.add_argument("--serve", action="store_true", help="loop forever")
    p.add_argument("--once", action="store_true", help="one scan and publish (default)")
    p.add_argument("--find", default="", help="search INDEX.jsonl")
    args = p.parse_args()
    if args.find:
        hits = find_entries(args.find)
        print(json.dumps({"q": args.find, "n": len(hits), "hits": hits}, indent=2))
        return
    ledger = Ledger()
    if args.serve:
        print(f"tea ledger watching hive/images/jury/training -> {PUBLIC_BASE}/{LEDGER}/INDEX.json", flush=True)
        while True:
            try:
                doc = scan_once(ledger, publish=True)
                print(
                    f"ledger n={doc.get('n')} counts={doc.get('counts')} index={PUBLIC_BASE}/{LEDGER}/INDEX.json",
                    flush=True,
                )
            except Exception as exc:
                print(f"ledger err {exc}", flush=True)
            time.sleep(POLL)
    else:
        doc = scan_once(ledger, publish=True)
        print(json.dumps({"n": doc.get("n"), "counts": doc.get("counts"), "index": f"{PUBLIC_BASE}/{LEDGER}/INDEX.json"}, indent=2))


if __name__ == "__main__":
    main()
