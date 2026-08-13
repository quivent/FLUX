#!/usr/bin/env python3
"""Continuously add neural optical-flow evidence to Stallion motion studies.

The CPU miners remain the proposal engine. This reviewer consumes only retained
candidate paths, uses RAFT-Small on the H100 to measure warp residual and flow
acceleration, and writes a bounded sidecar index for the Tea gallery.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import signal
import time
from typing import Any

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image
from torchvision.models.optical_flow import Raft_Small_Weights, raft_small


STOP = False


def stop_requested(_signum: int, _frame: Any) -> None:
    global STOP
    STOP = True


def atomic_json(path: pathlib.Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".gpu.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def proportional_boxes(width: int, height: int, cols: int, rows: int) -> list[tuple[int, int, int, int]]:
    return [
        (round(col * width / cols), round(row * height / rows),
         round((col + 1) * width / cols), round((row + 1) * height / rows))
        for row in range(rows) for col in range(cols)
    ]


class Atlas:
    def __init__(self, path: pathlib.Path, side: int) -> None:
        with Image.open(path) as image:
            self.grid = image.convert("RGB").copy()
        self.boxes = proportional_boxes(self.grid.width, self.grid.height, 96, 79)
        self.side = side
        self.cache: dict[int, torch.Tensor] = {}

    def tensor(self, index: int) -> torch.Tensor:
        cached = self.cache.get(index)
        if cached is not None:
            return cached
        tile = self.grid.crop(self.boxes[index]).resize((self.side, self.side), Image.Resampling.BICUBIC)
        value = torch.from_numpy(np.asarray(tile, dtype=np.uint8).copy()).permute(2, 0, 1).float().div_(255.0)
        if len(self.cache) >= 7584:
            self.cache.clear()
        self.cache[index] = value
        return value


def pairs_for(result: dict[str, Any]) -> list[tuple[int, int]]:
    indices = [int(value) for value in result.get("indices", [])]
    pairs = list(zip(indices, indices[1:]))
    if result.get("mode") == "spectral_loop" and len(indices) > 1:
        pairs.append((indices[-1], indices[0]))
    return pairs


@torch.inference_mode()
def review(model: torch.nn.Module, atlas: Atlas, pairs: list[tuple[int, int]], batch_size: int) -> dict[str, Any]:
    residuals: list[float] = []
    mean_vectors: list[list[float]] = []
    magnitudes: list[float] = []
    for offset in range(0, len(pairs), batch_size):
        batch = pairs[offset:offset + batch_size]
        first = torch.stack([atlas.tensor(a) for a, _ in batch]).cuda(non_blocking=True)
        second = torch.stack([atlas.tensor(b) for _, b in batch]).cuda(non_blocking=True)
        first, second = first.mul(2).sub(1), second.mul(2).sub(1)
        flow = model(first, second)[-1]
        b, _, h, w = flow.shape
        yy, xx = torch.meshgrid(
            torch.arange(h, device=flow.device), torch.arange(w, device=flow.device), indexing="ij"
        )
        x = (xx[None] + flow[:, 0]) * (2.0 / max(1, w - 1)) - 1.0
        y = (yy[None] + flow[:, 1]) * (2.0 / max(1, h - 1)) - 1.0
        warped = F.grid_sample(second, torch.stack([x, y], dim=-1), align_corners=True, padding_mode="border")
        residual = (first - warped).abs().mean((1, 2, 3)).mul(0.5)
        vector = flow.mean((2, 3)) / float(atlas.side)
        magnitude = torch.linalg.vector_norm(flow, dim=1).mean((1, 2)) / float(atlas.side)
        residuals.extend(residual.cpu().tolist())
        mean_vectors.extend(vector.cpu().tolist())
        magnitudes.extend(magnitude.cpu().tolist())
    vectors = np.asarray(mean_vectors, dtype=np.float32)
    acceleration = np.linalg.norm(np.diff(vectors, axis=0), axis=1) if len(vectors) > 1 else np.zeros(1)
    neural_score = (
        0.50 * float(np.mean(residuals))
        + 0.25 * float(np.percentile(residuals, 95))
        + 0.20 * float(np.mean(acceleration))
        + 0.05 * float(np.std(magnitudes))
    )
    return {
        "schema": "tea.stallion-motion.gpu-review.v1",
        "model": "raft-small-c-t-v2",
        "pair_count": len(pairs),
        "neural_score": neural_score,
        "mean_warp_residual": float(np.mean(residuals)),
        "p95_warp_residual": float(np.percentile(residuals, 95)),
        "mean_flow_magnitude": float(np.mean(magnitudes)),
        "mean_flow_acceleration": float(np.mean(acceleration)),
        "reviewed_at": time.time(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--side", type=int, default=256)
    parser.add_argument("--batch-size", type=int, default=12)
    parser.add_argument("--poll", type=float, default=0.15)
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, stop_requested)
    signal.signal(signal.SIGINT, stop_requested)
    if not torch.cuda.is_available():
        raise SystemExit("CUDA is required for the Stallion GPU reviewer")
    torch.backends.cudnn.benchmark = True
    weights = Raft_Small_Weights.DEFAULT
    model = raft_small(weights=weights, progress=True).eval().cuda()
    atlas = Atlas(pathlib.Path(args.source), max(64, args.side // 8 * 8))
    root = pathlib.Path(args.output_root).resolve()
    runs_root = root / "runs"
    reviews_path = root / "gpu-reviews.json"
    try:
        reviews = json.loads(reviews_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        reviews = {"schema": "tea.stallion-motion.gpu-reviews.v1", "reviews": {}}
    indexed: dict[str, Any] = reviews.setdefault("reviews", {})
    while not STOP:
        stamped: list[tuple[float, pathlib.Path]] = []
        for path in runs_root.glob("*/r*.json"):
            try:
                stamped.append((path.stat().st_mtime, path))
            except FileNotFoundError:
                # Retention may displace a result while the reviewer scans.
                continue
        candidates = [path for _, path in sorted(stamped, reverse=True)]
        live_keys = {f"{path.parent.name}/{path.stem}" for path in candidates}
        changed = False
        for key in list(indexed):
            if key not in live_keys:
                del indexed[key]
                changed = True
        pending = [path for path in candidates if f"{path.parent.name}/{path.stem}" not in indexed]
        if not pending:
            if changed:
                reviews["updated_at"] = time.time()
                atomic_json(reviews_path, reviews)
            time.sleep(args.poll)
            continue
        for path in pending:
            if STOP:
                break
            try:
                result = json.loads(path.read_text(encoding="utf-8"))
                pairs = pairs_for(result)
                if not pairs:
                    continue
                evidence = review(model, atlas, pairs, max(1, args.batch_size))
                key = f"{path.parent.name}/{path.stem}"
                indexed[key] = evidence
                result["gpu_review"] = evidence
                atomic_json(path, result)
                reviews["updated_at"] = time.time()
                reviews["review_count"] = len(indexed)
                atomic_json(reviews_path, reviews)
                print(json.dumps({"key": key, "neural_score": evidence["neural_score"]}), flush=True)
            except (OSError, json.JSONDecodeError, RuntimeError) as exc:
                if "out of memory" in str(exc).lower() and args.batch_size > 1:
                    torch.cuda.empty_cache()
                    args.batch_size = max(1, args.batch_size // 2)
                print(json.dumps({"path": str(path), "error": str(exc)}), flush=True)
                time.sleep(0.2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
