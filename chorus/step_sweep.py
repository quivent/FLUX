#!/usr/bin/env python3
"""Adjacent denoise-depth geometry study for FLUX.1.

Prompt, initial latent, seed, resolution, precision, and guidance stay fixed.
Only the total schedule length changes. Adjacent outputs are measured for
pixel change, edge change, and apparent optical-flow displacement.
"""
import argparse
import gc
import json
import os
import pathlib
import socket
import time

import numpy as np
import torch
from diffusers import FluxPipeline
from PIL import Image, ImageDraw


def atomic_json(path, value):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def publish(job_id, path, index, total, out_dir):
    relative = path.resolve().relative_to(out_dir.resolve()).as_posix()
    payload = {"type": "asset.publish", "job_id": job_id, "asset": {
        "id": f"{job_id}:{index}", "name": path.name, "path": str(path),
        "media_type": "image/png", "index": index, "cell_index": index,
        "total": total, "access_url": "/outputs/" + relative}}
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(3); conn.connect(os.environ.get("PIPER_SOCKET", "/tmp/piper.sock"))
            conn.sendall((json.dumps(payload) + "\n").encode()); conn.shutdown(socket.SHUT_WR)
            return bool(json.loads(conn.recv(4096)).get("ok"))
    except (OSError, ValueError, json.JSONDecodeError):
        return False


def parse_steps(spec):
    if ":" in spec:
        lo, hi = (int(x.strip()) for x in spec.split(":", 1))
        return list(range(lo, hi + 1))
    return sorted({int(x.strip()) for x in spec.split(",") if x.strip()})


def compare(a, b):
    aa = np.asarray(a.convert("RGB"), dtype=np.uint8)
    bb = np.asarray(b.convert("RGB"), dtype=np.uint8)
    delta = aa.astype(np.float32) - bb.astype(np.float32)
    gray_a = np.dot(aa[..., :3], [0.299, 0.587, 0.114]).astype(np.float32) / 255
    gray_b = np.dot(bb[..., :3], [0.299, 0.587, 0.114]).astype(np.float32) / 255
    edge_a = np.hypot(*np.gradient(gray_a)) > 0.08
    edge_b = np.hypot(*np.gradient(gray_b)) > 0.08
    # Phase correlation estimates whole-frame geometric displacement without
    # an OpenCV runtime dependency. It is deterministic and subpixel-free by
    # design: this study asks where schedule depth moves structure at all.
    fa, fb = np.fft.fft2(gray_a), np.fft.fft2(gray_b)
    cross = fa * np.conj(fb)
    cross /= np.maximum(np.abs(cross), 1e-9)
    peak = np.unravel_index(np.argmax(np.abs(np.fft.ifft2(cross))), gray_a.shape)
    dy, dx = (int(peak[0]), int(peak[1]))
    if dy > gray_a.shape[0] // 2:
        dy -= gray_a.shape[0]
    if dx > gray_a.shape[1] // 2:
        dx -= gray_a.shape[1]
    return {
        "rgb_rms": round(float(np.sqrt(np.mean(delta * delta))) / 255, 6),
        "pixels_over_8": round(float(np.mean(np.max(np.abs(delta), axis=2) > 8)), 6),
        "edge_xor": round(float(np.mean(edge_a != edge_b)), 6),
        "phase_dx_px": dx, "phase_dy_px": dy,
        "phase_shift_px": round(float(np.hypot(dx, dy)), 5),
    }


def make_contact(rows, path):
    tile, cols = 256, 4
    lines = (len(rows) + cols - 1) // cols
    sheet = Image.new("RGB", (tile * cols, tile * lines), "#f5f1eb")
    draw = ImageDraw.Draw(sheet)
    for i, (steps, image) in enumerate(rows):
        x, y = (i % cols) * tile, (i // cols) * tile
        sheet.paste(image.resize((tile, tile)), (x, y))
        draw.rectangle((x, y, x + 88, y + 23), fill=(247, 245, 240))
        draw.text((x + 7, y + 6), f"{steps} steps", fill=(38, 34, 29))
    sheet.save(path, quality=88, optimize=True)


def main():
    ap = argparse.ArgumentParser(description="fixed-latent adjacent FLUX step sweep")
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--id", required=True)
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--step-range", default="21:28")
    ap.add_argument("--size", type=int, default=512)
    ap.add_argument("--guidance", type=float, default=3.6)
    ap.add_argument("--seed", type=int, default=1935692473)
    args = ap.parse_args()
    steps = parse_steps(args.step_range)
    if len(steps) < 2 or any(x < 1 or x > 120 for x in steps):
        raise SystemExit("step range needs at least two values in [1,120]")
    if args.size != 512:
        raise SystemExit("step sweep is locked to 512x512")

    out = pathlib.Path(args.out_dir).expanduser()
    sphere = out / "atlas" / f"{args.id}.sphere"
    sphere.mkdir(parents=True, exist_ok=True)
    manifest_path = sphere / "manifest.json"
    manifest = {
        "kind": "adjacent_step_geometry", "study_type": "movement", "id": args.id,
        "prompt": args.prompt, "seed": args.seed, "size": args.size,
        "precision": "bf16", "guidance": args.guidance, "schedule_steps": steps,
        "constants": ["prompt", "initial_latent", "seed", "resolution", "precision", "guidance"],
        "variable": "total_denoise_steps", "status": "loading", "started": time.time(),
    }
    atomic_json(manifest_path, manifest)
    pipe = FluxPipeline.from_pretrained(args.model_dir, torch_dtype=torch.bfloat16,
                                        local_files_only=True).to("cuda")
    pipe.set_progress_bar_config(disable=True)
    prompt_embeds, pooled = pipe.encode_prompt(
        prompt=args.prompt, device="cuda", num_images_per_prompt=1,
        max_sequence_length=512)[:2]
    # Keep the encoders on CUDA for the Diffusers pipeline call: its execution
    # device is inferred from resident modules. Batch one at 512px fits; moving
    # them here would incorrectly make the denoiser input CPU-resident.
    generator = torch.Generator("cpu").manual_seed(args.seed)
    base = pipe.prepare_latents(
        1, pipe.transformer.config.in_channels // 4, args.size, args.size,
        torch.bfloat16, "cuda", generator)[0].detach()

    rows = []
    timings = []
    manifest["status"] = "running"; atomic_json(manifest_path, manifest)
    for total_steps in steps:
        began = time.perf_counter()
        image = pipe(
            prompt=None, prompt_embeds=prompt_embeds,
            pooled_prompt_embeds=pooled, width=args.size, height=args.size,
            num_inference_steps=total_steps, guidance_scale=args.guidance,
            latents=base.clone()).images[0]
        seconds = time.perf_counter() - began
        image_path = sphere / f"{args.id}-steps-{total_steps:03d}.png"
        image.save(image_path)
        publish(args.id, image_path, len(rows), len(steps), out)
        rows.append((total_steps, image)); timings.append({"steps": total_steps, "seconds": seconds})
        manifest.update({"rendered": len(rows), "timings": timings, "updated": time.time()})
        atomic_json(manifest_path, manifest)
        print(json.dumps(timings[-1]), flush=True)

    adjacent = []
    for (steps_a, image_a), (steps_b, image_b) in zip(rows, rows[1:]):
        adjacent.append({"from_steps": steps_a, "to_steps": steps_b, **compare(image_a, image_b)})
    make_contact(rows, sphere / "contact.jpg")
    manifest.update({"adjacent": adjacent, "status": "done", "finished": time.time()})
    atomic_json(manifest_path, manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
