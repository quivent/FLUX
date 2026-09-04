#!/usr/bin/env python3
"""Inventory Tea harvests, write a catalog, and ship to R2 so they load anywhere.

Public CDN (beauty-flux):
  https://pub-197bed319eda457da858ab89c061ed38.r2.dev/site/<rel>
Vault (governor):
  r2://governor/outputs/<rel>
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import time
from pathlib import Path

ROOT = Path(os.environ.get("FLUX_OUTPUT_DIR") or Path.home() / "models" / "flux-output")
PUBLIC_BASE = os.environ.get(
    "TEA_R2_PUBLIC",
    "https://pub-197bed319eda457da858ab89c061ed38.r2.dev/site",
)
VAULT_PREFIX = "outputs/"
PUBLIC_PREFIX = "site/"
SKIP_DIR_NAMES = {"logs", "_prior-leftover"}


def sha256(path: Path, limit: int = 0) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        if limit:
            h.update(handle.read(limit))
        else:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                h.update(chunk)
    return h.hexdigest()


def iter_files(folder: Path):
    for path in folder.rglob("*"):
        if not path.is_file():
            continue
        if any(part.startswith(".") and part not in {".protocol-branch.json"} for part in path.parts):
            continue
        if path.name.endswith(("-shm", "-wal")):
            continue
        if any(part in SKIP_DIR_NAMES or part.startswith("_prior") or part.startswith("_rejected") for part in path.relative_to(folder).parts):
            continue
        yield path


def snapshot_sqlite(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    src_conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    dst_conn = sqlite3.connect(dest)
    src_conn.backup(dst_conn)
    dst_conn.close()
    src_conn.close()


def catalog_collection(slug: str, folder: Path, extra: dict) -> dict:
    items = []
    bytes_total = 0
    for path in sorted(iter_files(folder)):
        rel = path.relative_to(ROOT).as_posix()
        size = path.stat().st_size
        bytes_total += size
        item = {
            "rel": rel,
            "name": path.name,
            "bytes": size,
            "sha256": sha256(path),
            "mtime": int(path.stat().st_mtime),
            "content_type": "image/png" if path.suffix.lower() == ".png" else "application/octet-stream",
            "r2_vault": f"r2://governor/{VAULT_PREFIX}{rel}",
            "r2_public": f"r2://beauty-flux/{PUBLIC_PREFIX}{rel}",
            "url": f"{PUBLIC_BASE}/{rel}",
        }
        items.append(item)
    pngs = [i for i in items if i["name"].endswith(".png")]
    return {
        "slug": slug,
        "folder": str(folder),
        "count": len(items),
        "pngs": len(pngs),
        "bytes": bytes_total,
        "items": items,
        **extra,
    }


def r2_sync(local: Path, dest: str, bucket: str) -> None:
    env = os.environ.copy()
    env["R2_BUCKET"] = bucket
    print(f"sync {local} -> r2://{bucket}/{dest}", flush=True)
    subprocess.run(
        ["gemstone", "r2", "sync", str(local), dest],
        check=True,
        env=env,
        timeout=1800,
    )


def r2_push(local: Path, dest: str, bucket: str) -> None:
    env = os.environ.copy()
    env["R2_BUCKET"] = bucket
    print(f"push {local} -> r2://{bucket}/{dest}", flush=True)
    subprocess.run(
        ["gemstone", "r2", "push", str(local), dest],
        check=True,
        env=env,
        timeout=180,
    )


def main() -> None:
    staging = ROOT / "catalogs"
    staging.mkdir(parents=True, exist_ok=True)
    collections = []

    mg = ROOT / "collections" / "microgreens"
    if mg.is_dir():
        snap = staging / "microgreens-jury.sqlite3"
        db = mg / "jury.sqlite3"
        if db.exists():
            snapshot_sqlite(db, snap)
            # ship a stable sqlite next to the stills
            r2_push(snap, f"{VAULT_PREFIX}collections/microgreens/jury.sqlite3", "governor")
            r2_push(snap, f"{PUBLIC_PREFIX}collections/microgreens/jury.sqlite3", "beauty-flux")
        collections.append(catalog_collection("microgreens", mg, {
            "lane": "microgreens",
            "kind": "belarro-stills",
            "wall": "/collections/microgreens",
        }))

    silk = ROOT / "collections" / "silken-horses"
    if silk.is_dir():
        collections.append(catalog_collection("silken-horses", silk, {
            "lane": "silken-horses",
            "kind": "collection",
            "wall": "/collections/silken-horses",
        }))

    catalog = {
        "schema": "tea.r2-catalog.v1",
        "generated_at": int(time.time()),
        "public_base": PUBLIC_BASE,
        "vault": "r2://governor/outputs/",
        "cdn": "r2://beauty-flux/site/",
        "root": str(ROOT),
        "collections": [
            {k: c[k] for k in c if k != "items"} | {"sample": [i["url"] for i in c["items"] if i["name"].endswith(".png")][:3]}
            for c in collections
        ],
        "assets": {c["slug"]: c["items"] for c in collections},
    }
    catalog_path = staging / "tea.json"
    catalog_path.write_text(json.dumps(catalog, indent=2) + "\n")
    print(
        "catalog",
        catalog_path,
        "collections",
        [(c["slug"], c["pngs"], c["bytes"]) for c in collections],
        flush=True,
    )

    # Live stills only (skip _prior / _rejected via iter_files). Sync the
    # collection directory after copying a filtered pack.
    pack = staging / "pack"
    if pack.exists():
        subprocess.run(["rm", "-rf", str(pack)], check=True)
    for coll in collections:
        dest_local = pack / "collections" / coll["slug"]
        dest_local.mkdir(parents=True, exist_ok=True)
        for item in coll["items"]:
            src = ROOT / item["rel"]
            rel_in = Path(item["rel"]).relative_to(f"collections/{coll['slug']}")
            target = dest_local / rel_in
            target.parent.mkdir(parents=True, exist_ok=True)
            if src.exists() and not target.exists():
                try:
                    os.link(src, target)
                except OSError:
                    subprocess.run(["cp", "-p", str(src), str(target)], check=True)

    if (pack / "collections").is_dir():
        r2_sync(pack / "collections", f"{VAULT_PREFIX}collections/", "governor")
        r2_sync(pack / "collections", f"{PUBLIC_PREFIX}collections/", "beauty-flux")

    r2_push(catalog_path, f"{VAULT_PREFIX}catalogs/tea.json", "governor")
    r2_push(catalog_path, f"{PUBLIC_PREFIX}catalog/tea.json", "beauty-flux")
    print("public catalog", f"{PUBLIC_BASE}/catalog/tea.json", flush=True)
    if collections:
        sample = next((i["url"] for i in collections[0]["items"] if i["name"].endswith(".png")), "")
        print("sample still", sample, flush=True)


if __name__ == "__main__":
    main()
