#!/usr/bin/env python3
import argparse
import json
import math
import pathlib
import shutil
import subprocess
import sys

import cv2
import numpy as np


def expand_glob(pattern):
    path = pathlib.Path(pattern).expanduser()
    return sorted(path.parent.glob(path.name)) if not any(c in path.name for c in "*?[") else sorted(pathlib.Path(path.anchor or "/").glob(str(path)[1:]))


def expand(path):
    return pathlib.Path(path).expanduser()


def load_manifest(path):
    manifest_path = expand(path)
    manifest = json.loads(manifest_path.read_text())
    manifest["_path"] = str(manifest_path)
    return manifest


def collect_sources(manifest, override_glob, limit):
    patterns = [override_glob] if override_glob else [manifest["source_glob"], manifest.get("fallback_source_glob", "")]
    for pattern in patterns:
        if not pattern:
            continue
        sources = expand_glob(pattern)
        if sources:
            return sources[:limit] if limit and limit > 0 else sources
    raise SystemExit("no source images matched")


def slug(path):
    parts = path.with_suffix("").parts[-4:]
    return "__".join(p.replace(" ", "_") for p in parts)


def read_image(path, width, height):
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError(f"could not read {path}")
    return cv2.resize(image, (width, height), interpolation=cv2.INTER_LANCZOS4)


def soft_detail_mask(image):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
    edges = cv2.Laplacian(gray, cv2.CV_32F)
    detail = cv2.GaussianBlur(np.abs(edges), (0, 0), 5)
    light = cv2.GaussianBlur(gray, (0, 0), 19)
    mask = np.clip((detail * 5.0) + (light * 0.35), 0.0, 1.0)
    return cv2.GaussianBlur(mask, (0, 0), 9)


def affine_frame(image, t, shift_pixels, zoom):
    h, w = image.shape[:2]
    cx, cy = w / 2.0, h / 2.0
    dx = math.sin(t * math.tau) * shift_pixels
    dy = math.cos(t * math.tau) * shift_pixels * 0.35
    scale = 1.0 + zoom * (0.5 + 0.5 * math.sin(t * math.tau - math.pi / 2.0))
    matrix = cv2.getRotationMatrix2D((cx, cy), math.sin(t * math.tau) * 0.35, scale)
    matrix[0, 2] += dx
    matrix[1, 2] += dy
    return cv2.warpAffine(image, matrix, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT_101)


def displacement_frame(image, mask, t, warp_pixels):
    h, w = image.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    wave_a = np.sin((yy / max(h, 1)) * math.tau * 2.0 + t * math.tau)
    wave_b = np.cos((xx / max(w, 1)) * math.tau * 1.5 - t * math.tau)
    map_x = xx + wave_a * mask * warp_pixels
    map_y = yy + wave_b * mask * warp_pixels * 0.55
    return cv2.remap(image, map_x, map_y, interpolation=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT_101)


def blend_weight(t, strength):
    pulse = 0.5 - 0.5 * math.cos(t * math.tau)
    return strength * pulse


def make_frames(image, neighbor, args):
    mask = soft_detail_mask(image)
    frames = []
    for i in range(args.frames):
        t = i / args.frames
        shifted = affine_frame(image, t, args.shift_pixels, args.zoom)
        if args.mode in ("breath_parallax", "hybrid_alive"):
            shifted = displacement_frame(shifted, mask, t, args.warp_pixels)
        if neighbor is not None and args.mode in ("blend_shift", "hybrid_alive"):
            other = affine_frame(neighbor, (t + 0.5) % 1.0, -args.shift_pixels * 0.55, args.zoom * 0.5)
            alpha = blend_weight(t, args.blend_strength)
            shifted = cv2.addWeighted(shifted, 1.0 - alpha, other, alpha, 0.0)
        frames.append(shifted)
    return frames


def write_mp4(frames_dir, mp4_path, fps):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-framerate",
        str(fps),
        "-i",
        str(frames_dir / "frame-%04d.png"),
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(mp4_path),
    ]
    subprocess.run(cmd, check=True)
    return True


def run(args):
    manifest = load_manifest(args.manifest)
    defaults = manifest.get("defaults", {})
    for key in ("fps", "frames", "width", "height"):
        if getattr(args, key) is None:
            setattr(args, key, manifest.get(key))
    for key in ("mode", "shift_pixels", "warp_pixels", "zoom", "blend_strength"):
        if getattr(args, key) is None:
            setattr(args, key, defaults.get(key))

    sources = collect_sources(manifest, args.source_glob, args.limit)
    output_dir = expand(args.out_dir or manifest["output_dir"])
    frames_root = output_dir / "frames"
    video_root = output_dir / "mp4"
    frames_root.mkdir(parents=True, exist_ok=True)
    video_root.mkdir(parents=True, exist_ok=True)

    ledger_path = output_dir / "alive-motion-run.jsonl"
    rows = []
    for index, source in enumerate(sources):
        neighbor_path = sources[(index + args.pair_stride) % len(sources)] if len(sources) > 1 else None
        stem = slug(source)
        frames_dir = frames_root / stem
        mp4_path = video_root / f"{stem}-{args.mode}.mp4"
        if mp4_path.exists() and not args.restart:
            print(f"skip existing {mp4_path}", flush=True)
            continue

        image = read_image(source, args.width, args.height)
        neighbor = read_image(neighbor_path, args.width, args.height) if neighbor_path else None
        frames = make_frames(image, neighbor, args)
        frames_dir.mkdir(parents=True, exist_ok=True)
        for frame_index, frame in enumerate(frames):
            cv2.imwrite(str(frames_dir / f"frame-{frame_index:04d}.png"), frame)
        made_mp4 = write_mp4(frames_dir, mp4_path, args.fps) if args.mp4 else False
        if made_mp4 and not args.keep_frames:
            shutil.rmtree(frames_dir)
        row = {
            "manifest": manifest["id"],
            "source": str(source),
            "neighbor": str(neighbor_path) if neighbor_path else "",
            "mode": args.mode,
            "frames": args.frames,
            "fps": args.fps,
            "mp4": str(mp4_path) if made_mp4 else "",
            "frames_dir": str(frames_dir) if frames_dir.exists() else "",
        }
        rows.append(row)
        print(f"alive {index + 1}/{len(sources)} {source.name} -> {mp4_path if made_mp4 else frames_dir}", flush=True)

    with ledger_path.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, sort_keys=True) + "\n")
    print(f"ledger={ledger_path}")


def main():
    parser = argparse.ArgumentParser(description="Make subtle alive loops from FLUX stills.")
    parser.add_argument("manifest")
    parser.add_argument("--source-glob", default="")
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--mode", choices=("breath_parallax", "blend_shift", "hybrid_alive"), default=None)
    parser.add_argument("--fps", type=int, default=None)
    parser.add_argument("--frames", type=int, default=None)
    parser.add_argument("--width", type=int, default=None)
    parser.add_argument("--height", type=int, default=None)
    parser.add_argument("--shift-pixels", type=float, default=None)
    parser.add_argument("--warp-pixels", type=float, default=None)
    parser.add_argument("--zoom", type=float, default=None)
    parser.add_argument("--blend-strength", type=float, default=None)
    parser.add_argument("--pair-stride", type=int, default=1)
    parser.add_argument("--no-mp4", dest="mp4", action="store_false")
    parser.add_argument("--keep-frames", action="store_true")
    parser.add_argument("--restart", action="store_true")
    parser.set_defaults(mp4=True)
    run(parser.parse_args())


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        sys.exit(exc.returncode)
