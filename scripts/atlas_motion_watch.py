#!/usr/bin/env python3
import argparse
import json
import math
import pathlib
import shutil
import subprocess
import time

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont


def cell_index(path):
    stem = path.stem
    if not stem.startswith("cell_"):
        return -1
    try:
        return int(stem.split("_", 1)[1])
    except ValueError:
        return -1


def collect_frames(atlas_dir):
    frames = [p for p in atlas_dir.glob("cell_*.png") if cell_index(p) >= 0]
    return sorted(frames, key=cell_index)


def load_gray(path, size=192):
    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        return None
    return cv2.resize(image, (size, size), interpolation=cv2.INTER_AREA)


def frame_metrics(prev_gray, gray):
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    if prev_gray is None:
        return {
            "mean_abs_delta": 0.0,
            "flow_mean": 0.0,
            "flow_p95": 0.0,
            "sharpness": sharpness,
        }
    delta = cv2.absdiff(prev_gray, gray)
    flow = cv2.calcOpticalFlowFarneback(prev_gray, gray, None, 0.5, 3, 21, 3, 5, 1.2, 0)
    mag, _ = cv2.cartToPolar(flow[..., 0], flow[..., 1])
    return {
        "mean_abs_delta": float(delta.mean()),
        "flow_mean": float(mag.mean()),
        "flow_p95": float(np.percentile(mag, 95)),
        "sharpness": sharpness,
    }


def score_frames(frames):
    scores = []
    prev_gray = None
    for frame in frames:
        gray = load_gray(frame)
        if gray is None:
            continue
        metrics = frame_metrics(prev_gray, gray)
        idx = cell_index(frame)
        scores.append({
            "index": idx,
            "name": frame.name,
            "path": str(frame),
            **metrics,
        })
        prev_gray = gray
    return scores


def best_segments(scores, length=24):
    if len(scores) < length:
        return []
    segments = []
    for start in range(0, len(scores) - length + 1):
        window = scores[start:start + length]
        flow = [x["flow_mean"] for x in window[1:]]
        deltas = [x["mean_abs_delta"] for x in window[1:]]
        sharp = [x["sharpness"] for x in window]
        # Favor movement without hard jumps, and reject very soft frames.
        score = (
            float(np.mean(flow)) * 1.8
            + float(np.mean(deltas)) * 0.06
            + min(float(np.mean(sharp)) / 900.0, 2.0)
            - float(np.std(deltas)) * 0.04
        )
        segments.append({
            "start_index": window[0]["index"],
            "end_index": window[-1]["index"],
            "start_name": window[0]["name"],
            "end_name": window[-1]["name"],
            "frames": len(window),
            "score": score,
            "flow_mean": float(np.mean(flow)),
            "delta_mean": float(np.mean(deltas)),
            "sharpness_mean": float(np.mean(sharp)),
        })
    return sorted(segments, key=lambda x: x["score"], reverse=True)[:12]


def write_contact_sheet(frames, out_path, cols=8, thumb=160, limit=64):
    selected = frames[-limit:]
    if not selected:
        return False
    rows = math.ceil(len(selected) / cols)
    label_h = 20
    sheet = Image.new("RGB", (cols * thumb, rows * (thumb + label_h)), (7, 9, 16))
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("Menlo.ttc", 11)
    except OSError:
        font = ImageFont.load_default()
    for pos, frame in enumerate(selected):
        x = (pos % cols) * thumb
        y = (pos // cols) * (thumb + label_h)
        with Image.open(frame) as img:
            img = img.convert("RGB")
            img.thumbnail((thumb, thumb), Image.Resampling.LANCZOS)
            tile = Image.new("RGB", (thumb, thumb), (5, 7, 17))
            tile.paste(img, ((thumb - img.width) // 2, (thumb - img.height) // 2))
        sheet.paste(tile, (x, y))
        draw.text((x + 6, y + thumb + 4), f"{cell_index(frame):05d}", fill=(218, 202, 160), font=font)
    tmp = out_path.with_suffix(".tmp.jpg")
    sheet.save(tmp, quality=88)
    tmp.replace(out_path)
    return True


def write_preview_video(frames, out_path, fps=8, max_frames=240):
    if len(frames) < 2 or shutil.which("ffmpeg") is None:
        return False
    selected = frames[-max_frames:]
    list_path = out_path.with_suffix(".frames.txt")
    tmp_path = out_path.with_suffix(".tmp.mp4")
    with list_path.open("w") as f:
        for frame in selected:
            f.write(f"file '{frame}'\n")
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "concat", "-safe", "0", "-r", str(fps),
        "-i", str(list_path),
        "-vf", "scale=768:768:force_original_aspect_ratio=decrease,pad=768:768:(ow-iw)/2:(oh-ih)/2",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(tmp_path),
    ]
    try:
        subprocess.run(cmd, check=True, timeout=120)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False
    tmp_path.replace(out_path)
    return True


def write_json(path, payload):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def build_once(atlas_dir, out_dir, fps, segment_length):
    frames = collect_frames(atlas_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    scores = score_frames(frames)
    payload = {
        "atlas_dir": str(atlas_dir),
        "frame_count": len(frames),
        "updated": time.time(),
        "scores": scores,
        "best_segments": best_segments(scores, length=segment_length),
        "assets": {
            "contact_sheet": str(out_dir / "contact_sheet_latest.jpg"),
            "preview_mp4": str(out_dir / "preview.mp4"),
            "scores_json": str(out_dir / "scores.json"),
        },
    }
    write_json(out_dir / "scores.json", payload)
    write_contact_sheet(frames, out_dir / "contact_sheet_latest.jpg")
    write_preview_video(frames, out_dir / "preview.mp4", fps=fps)
    return payload


def main():
    parser = argparse.ArgumentParser(description="CPU-only atlas motion watcher")
    parser.add_argument("--atlas-dir", required=True)
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--interval", type=float, default=45.0)
    parser.add_argument("--fps", type=int, default=8)
    parser.add_argument("--segment-length", type=int, default=24)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    atlas_dir = pathlib.Path(args.atlas_dir).expanduser().resolve()
    out_dir = pathlib.Path(args.out_dir).expanduser().resolve() if args.out_dir else atlas_dir / "_motion"
    last_count = -1
    while True:
        count = len(collect_frames(atlas_dir))
        if count != last_count:
            payload = build_once(atlas_dir, out_dir, args.fps, args.segment_length)
            print(f"motion_watch frames={payload['frame_count']} out={out_dir}", flush=True)
            last_count = count
        if args.once:
            break
        time.sleep(max(5.0, args.interval))


if __name__ == "__main__":
    main()
