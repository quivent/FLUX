#!/usr/bin/env python3
"""Push settled protocol frames to the site output dir (already local) and R2.

Watches ~/models/flux-output for new protocol-*.png files and uploads each
once to:
  r2://gallery/assets/renders/tea/<name>
  r2://governor/outputs/<name>

GPU pinning is unrelated: workers still write here; this is the off-box stream.
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

OUT = Path(os.environ.get("FLUX_OUTPUT_DIR") or os.path.expanduser("~/models/flux-output"))
STATE = Path(os.environ.get("FLUXD_DIR") or os.path.expanduser("~/CLIs/flux/.fluxd")) / "protocol_r2_seen.txt"
# gemstone r2 push treats the destination as a key on the default `governor`
# bucket. A r2://gallery/... dest was stored as a literal key under governor.
# Jury evaluator uses outputs/<file> — that is the live vault prefix.
DEST_PREFIXES = ("outputs/",)
POLL = float(os.environ.get("PROTOCOL_R2_POLL_S") or 8)


def seen_set():
    if not STATE.exists():
        return set()
    return {line.strip() for line in STATE.read_text().splitlines() if line.strip()}


def mark(name: str):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    with STATE.open("a") as f:
        f.write(name + "\n")


def protocol_pngs(out: Path):
    # Fashion wall at the output root, plus independent protocol branches
    # under collections/<slug>/. Arcane stays unplugged.
    for path in out.glob("protocol-*.png"):
        name = path.name.lower()
        if "arcane" in name:
            continue
        yield path
    collections = out / "collections"
    if collections.is_dir():
        for path in collections.glob("*/protocol-*.png"):
            name = path.name.lower()
            if "arcane" in name:
                continue
            yield path


def push(path: Path) -> bool:
    try:
        name = path.relative_to(OUT).as_posix()
    except ValueError:
        name = path.name
    ok = True
    for dest in (f"{prefix}{name}" for prefix in DEST_PREFIXES):
        try:
            r = subprocess.run(
                ["gemstone", "r2", "push", str(path), dest],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if r.returncode != 0:
                print(f"r2 push fail {dest}: {(r.stderr or r.stdout)[:200]}", flush=True)
                ok = False
            else:
                print(f"r2 {dest}", flush=True)
        except Exception as exc:
            print(f"r2 push err {dest}: {exc}", flush=True)
            ok = False
    return ok


def main():
    print(f"protocol r2 stream watching {OUT}", flush=True)
    seen = seen_set()
    while True:
        if OUT.is_dir():
            for path in sorted(protocol_pngs(OUT), key=lambda p: p.stat().st_mtime):
                try:
                    key = path.relative_to(OUT).as_posix()
                except ValueError:
                    key = path.name
                if key in seen:
                    continue
                if path.stat().st_size < 1024:
                    continue
                if push(path):
                    mark(key)
                    seen.add(key)
        time.sleep(POLL)


if __name__ == "__main__":
    main()
