#!/usr/bin/env python3
"""Mark motion discontinuities and queue non-destructive replacement candidates."""
import argparse
import json
import math
import pathlib
import re
import socket
import time

import numpy as np
from PIL import Image


def atomic_json(path, value):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def frame_number(path):
    found = re.findall(r"(\d+)", path.stem)
    return int(found[-1]) if found else 0


def metrics(a, b):
    aa = np.asarray(a.convert("RGB"), dtype=np.float32) / 255
    bb = np.asarray(b.convert("RGB"), dtype=np.float32) / 255
    delta = aa - bb
    ga = np.dot(aa, [0.299, 0.587, 0.114])
    gb = np.dot(bb, [0.299, 0.587, 0.114])
    ea = np.hypot(*np.gradient(ga)) > 0.08
    eb = np.hypot(*np.gradient(gb)) > 0.08
    rms = float(np.sqrt(np.mean(delta * delta)))
    edge = float(np.mean(ea != eb))
    return {"rgb_rms": round(rms, 6), "edge_xor": round(edge, 6),
            "motion_score": round(0.62 * rms + 0.38 * edge, 6)}


def request(sock_path, payload):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(20); conn.connect(sock_path)
        conn.sendall((json.dumps(payload) + "\n").encode()); conn.shutdown(socket.SHUT_WR)
        data = b""
        while not data.endswith(b"\n"):
            chunk = conn.recv(65536)
            if not chunk:
                break
            data += chunk
    return json.loads(data)


def main():
    ap = argparse.ArgumentParser(description="continuous motion gap/still repair pass")
    ap.add_argument("--sphere", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--glob", default="cell_*.png")
    ap.add_argument("--still-ratio", type=float, default=0.38)
    ap.add_argument("--gap-ratio", type=float, default=2.35)
    ap.add_argument("--candidates", type=int, default=4)
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--submit", action="store_true")
    ap.add_argument("--socket", default=".fluxd/img2img.sock")
    args = ap.parse_args()
    sphere = pathlib.Path(args.sphere).expanduser().resolve()
    out_dir = pathlib.Path(args.out_dir).expanduser().resolve()
    frames = sorted(sphere.glob(args.glob), key=frame_number)
    if len(frames) < 3:
        raise SystemExit("continuity pass needs at least three connected frames")
    pairs = list(zip(frames, frames[1:]))
    if args.loop:
        pairs.append((frames[-1], frames[0]))
    rows = []
    for left, right in pairs:
        rows.append({"left": left.name, "right": right.name,
                     **metrics(Image.open(left), Image.open(right))})
    scores = np.asarray([row["motion_score"] for row in rows], dtype=np.float64)
    median = float(np.median(scores))
    still_at, gap_at = median * args.still_ratio, median * args.gap_ratio
    repair_root = sphere / "_repair"
    source_root, candidate_root = repair_root / "sources", repair_root / "candidates"
    source_root.mkdir(parents=True, exist_ok=True); candidate_root.mkdir(exist_ok=True)
    jobs, flagged = [], []
    for pair_index, ((left, right), row) in enumerate(zip(pairs, rows)):
        reason = "still" if row["motion_score"] < still_at else "gap" if row["motion_score"] > gap_at else ""
        row["mark"] = reason or "pass"
        if not reason:
            continue
        a, b = Image.open(left).convert("RGB"), Image.open(right).convert("RGB")
        source = a if reason == "still" else Image.blend(a, b, 0.5)
        source_path = source_root / f"between-{frame_number(left):05d}-{frame_number(right):05d}-{reason}.png"
        source.save(source_path)
        flagged.append(row)
        strength = 0.34 if reason == "still" else 0.18
        for variant in range(args.candidates):
            filename = (candidate_root / f"between-{frame_number(left):05d}-{frame_number(right):05d}"
                        f"-{reason}-candidate-{variant + 1}.png").relative_to(out_dir).as_posix()
            jobs.append({"op": "submit_img2img", "prompt": args.prompt,
                         "image": str(source_path), "width": 512, "height": 512,
                         "steps": 28, "guidance": 3.6, "strength": strength,
                         "seed": str(1935692473 + pair_index * 104729 + variant * 209759),
                         "filename": filename,
                         "conditioning": f"continuity replacement: {reason}",
                         "interval": [left.name, right.name], "reason": reason,
                         "selection": "Gemma council required; originals remain authoritative"})
    ledger = {"kind": "continuity_repair", "sphere": str(sphere), "frames": len(frames),
              "pairs": len(rows), "median_motion": median, "still_threshold": still_at,
              "gap_threshold": gap_at, "flagged": flagged, "rows": rows,
              "replacement_jobs": len(jobs), "status": "marked", "created": time.time()}
    atomic_json(repair_root / "continuity-ledger.json", ledger)
    with (repair_root / "replacement-queue.jsonl").open("w") as stream:
        for job in jobs:
            stream.write(json.dumps(job, sort_keys=True) + "\n")
    if args.submit:
        receipts = []
        for job in jobs:
            response = request(args.socket, job)
            receipts.append({"interval": job["interval"], "reason": job["reason"], "response": response})
            if not response.get("ok"):
                raise RuntimeError(response)
        atomic_json(repair_root / "submission-receipts.json", receipts)
        ledger["status"] = "submitted"; ledger["submitted"] = len(receipts)
        atomic_json(repair_root / "continuity-ledger.json", ledger)
    print(json.dumps({"frames": len(frames), "pairs": len(rows), "flagged": len(flagged),
                      "replacement_jobs": len(jobs), "ledger": str(repair_root / "continuity-ledger.json")}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
