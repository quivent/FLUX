#!/usr/bin/env python3
"""Object-centric GPU gate for native Stallion motion candidates.

RAFT is measured separately inside and outside a semantic horse mask.  Raw
background translation is reported as camera motion, residual background flow
is reported independently, and only articulated foreground motion can pass.
This reviewer refuses the presentation atlas and never renders frames.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import signal
import subprocess
import time
from typing import Any

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image
from torchvision.models.optical_flow import Raft_Small_Weights, raft_small
from torchvision.models.segmentation import (
    DeepLabV3_MobileNet_V3_Large_Weights,
    deeplabv3_mobilenet_v3_large,
)

from stallion_motion_rubric import PairEvidence, evaluate_sequence


STOP = False


def stop_requested(_signum: int, _frame: Any) -> None:
    global STOP
    STOP = True


def atomic_json(path: pathlib.Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".gpu.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


class NativeCorpus:
    def __init__(self, path: pathlib.Path, side: int, minimum_side: int = 256) -> None:
        if not path.is_dir():
            raise RuntimeError(
                "source-integrity gate: GPU review requires native cell_*.png files, not an atlas image"
            )
        self.paths = tuple(sorted(path.glob("cell_*.png")))
        if not self.paths:
            raise RuntimeError(f"no native cell_*.png files under {path}")
        with Image.open(self.paths[0]) as first:
            self.native_size = first.size
        if min(self.native_size) < minimum_side:
            raise RuntimeError(
                f"source-integrity gate: native cells are {self.native_size}, require at least {minimum_side}px"
            )
        self.side = side
        self.cache: dict[int, torch.Tensor] = {}

    def image(self, index: int) -> Image.Image:
        if not 0 <= index < len(self.paths):
            raise IndexError(f"cell index {index} outside native corpus of {len(self.paths)}")
        with Image.open(self.paths[index]) as image:
            if image.size != self.native_size:
                raise RuntimeError(
                    f"source-integrity gate: {self.paths[index].name} is {image.size}, expected {self.native_size}"
                )
            return image.convert("RGB").copy()

    def tensor(self, index: int) -> torch.Tensor:
        cached = self.cache.get(index)
        if cached is not None:
            return cached
        image = self.image(index).resize((self.side, self.side), Image.Resampling.BICUBIC)
        value = torch.from_numpy(np.asarray(image, dtype=np.uint8).copy()).permute(2, 0, 1).float().div_(255.0)
        if len(self.cache) >= 512:
            self.cache.clear()
        self.cache[index] = value
        return value


class HorseSegmenter:
    def __init__(self, side: int) -> None:
        weights = DeepLabV3_MobileNet_V3_Large_Weights.DEFAULT
        categories = [str(value).lower() for value in weights.meta["categories"]]
        if "horse" not in categories:
            raise RuntimeError("semantic segmentation weights do not expose a horse class")
        self.horse_class = categories.index("horse")
        self.transform = weights.transforms()
        self.model = deeplabv3_mobilenet_v3_large(weights=weights).eval().cuda()
        self.side = side
        self.cache: dict[int, tuple[torch.Tensor, float]] = {}

    @torch.inference_mode()
    def masks(self, corpus: NativeCorpus, indices: list[int]) -> tuple[torch.Tensor, list[float]]:
        missing = [index for index in dict.fromkeys(indices) if index not in self.cache]
        for offset in range(0, len(missing), 16):
            batch_indices = missing[offset:offset + 16]
            batch = torch.stack([self.transform(corpus.image(index)) for index in batch_indices]).cuda()
            logits = self.model(batch)["out"]
            probability = logits.softmax(1)[:, self.horse_class:self.horse_class + 1]
            probability = F.interpolate(
                probability, (self.side, self.side), mode="bilinear", align_corners=False,
            )[:, 0]
            masks = probability >= 0.30
            for index, mask, confidence_map in zip(batch_indices, masks, probability):
                confidence = float(confidence_map[mask].mean().item()) if bool(mask.any()) else 0.0
                self.cache[index] = (mask.cpu(), confidence)
            if len(self.cache) > 2048:
                keep = {index: self.cache[index] for index in indices if index in self.cache}
                self.cache = keep
        masks = torch.stack([self.cache[index][0] for index in indices]).cuda(non_blocking=True)
        confidence = [self.cache[index][1] for index in indices]
        return masks, confidence


def pairs_for(result: dict[str, Any]) -> list[tuple[int, int]]:
    indices = [int(value) for value in result.get("indices", [])]
    pairs = list(zip(indices, indices[1:]))
    if result.get("mode") == "spectral_loop" and len(indices) > 1:
        pairs.append((indices[-1], indices[0]))
    return pairs


def is_v2_candidate(path: pathlib.Path) -> bool:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return (
        value.get("schema") == "tea.stallion-motion.proposal.v2"
        and value.get("protocol") == "tea.stallion-motion.v2"
    )


def masked_median(values: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
    selected = values[:, mask]
    if selected.shape[1] == 0:
        return torch.zeros(values.shape[0], device=values.device, dtype=values.dtype)
    return selected.median(dim=1).values


def mirror_iou(mask: torch.Tensor) -> float:
    points = torch.nonzero(mask, as_tuple=False)
    if not len(points):
        return 1.0
    y0, x0 = points.min(0).values.tolist()
    y1, x1 = points.max(0).values.tolist()
    crop = mask[y0:y1 + 1, x0:x1 + 1]
    mirror = crop.flip(1)
    union = (crop | mirror).sum().clamp_min(1)
    return float((crop & mirror).sum().float().div(union).item())


def mask_centroid(mask: torch.Tensor) -> torch.Tensor:
    points = torch.nonzero(mask, as_tuple=False).float()
    if not len(points):
        return torch.zeros(2, device=mask.device)
    return points.mean(0)


def mask_perimeter(mask: torch.Tensor) -> float:
    value = mask.float()[None, None]
    eroded = -F.max_pool2d(-value, 3, stride=1, padding=1)
    return float((value - eroded).clamp_min(0).sum().item())


@torch.inference_mode()
def review(
    flow_model: torch.nn.Module,
    segmenter: HorseSegmenter,
    corpus: NativeCorpus,
    result: dict[str, Any],
    pairs: list[tuple[int, int]],
    batch_size: int,
) -> dict[str, Any]:
    raw: list[dict[str, float]] = []
    foreground_vectors: list[np.ndarray] = []
    for offset in range(0, len(pairs), batch_size):
        batch_pairs = pairs[offset:offset + batch_size]
        flat_indices = [value for pair in batch_pairs for value in pair]
        masks, confidences = segmenter.masks(corpus, flat_indices)
        first_masks, second_masks = masks[0::2], masks[1::2]
        union_masks = first_masks | second_masks
        # Keep segmentation uncertainty out of the background measurement.
        dilated_masks = F.max_pool2d(union_masks[:, None].float(), 11, stride=1, padding=5)[:, 0] > 0
        background_masks = ~dilated_masks

        first = torch.stack([corpus.tensor(a) for a, _ in batch_pairs]).cuda(non_blocking=True)
        second = torch.stack([corpus.tensor(b) for _, b in batch_pairs]).cuda(non_blocking=True)
        flow = flow_model(first.mul(2).sub(1), second.mul(2).sub(1))[-1]
        for position in range(len(batch_pairs)):
            bg = background_masks[position]
            fg = union_masks[position]
            camera = masked_median(flow[position], bg)
            compensated = flow[position] - camera[:, None, None]
            magnitude = torch.linalg.vector_norm(compensated, dim=0) / float(corpus.side)
            background_motion = float(masked_median(magnitude[None], bg)[0].item())
            foreground_motion = float(masked_median(magnitude[None], fg)[0].item())
            foreground_vector = masked_median(compensated, fg).div(float(corpus.side))
            foreground_vectors.append(foreground_vector.cpu().numpy())
            intersection = (first_masks[position] & second_masks[position]).sum().float()
            union = (first_masks[position] | second_masks[position]).sum().clamp_min(1).float()
            silhouette_change = 1.0 - float((intersection / union).item())
            centroid_shift = float(
                torch.linalg.vector_norm(mask_centroid(first_masks[position]) - mask_centroid(second_masks[position]))
                .div(float(corpus.side)).item()
            )
            perimeter_first = mask_perimeter(first_masks[position])
            perimeter_second = mask_perimeter(second_masks[position])
            boundary_change = abs(perimeter_first - perimeter_second) / max(perimeter_first, perimeter_second, 1.0)
            raw.append({
                "foreground_motion": foreground_motion,
                "background_motion": background_motion,
                "camera_motion": float(torch.linalg.vector_norm(camera).div(float(corpus.side)).item()),
                "silhouette_change": silhouette_change,
                "mask_confidence": min(confidences[position * 2:position * 2 + 2]),
                "mask_area": float((first_masks[position].float().mean() + second_masks[position].float().mean()).mul(0.5).item()),
                "mirror_symmetry": max(mirror_iou(first_masks[position]), mirror_iou(second_masks[position])),
                "camera_dx": float(camera[0].div(float(corpus.side)).item()),
                "camera_dy": float(camera[1].div(float(corpus.side)).item()),
                "mask_centroid_shift": centroid_shift,
                "mask_boundary_change": boundary_change,
            })

    for index, row in enumerate(raw):
        acceleration = 0.0
        if index:
            acceleration = float(np.linalg.norm(foreground_vectors[index] - foreground_vectors[index - 1]))
        row["foreground_acceleration"] = acceleration
    evidence = [PairEvidence(**row) for row in raw]
    indices = [int(value) for value in result["indices"]]
    declared_reversals = int((result.get("metrics") or {}).get("direction_reversals", 0))
    rubric = evaluate_sequence(
        evidence, indices=indices, declared_pose_reversals=declared_reversals,
    )
    return {
        "schema": "tea.stallion-motion.gpu-review.v2",
        "models": ["raft-small-c-t-v2", "deeplabv3-mobilenet-v3-large-horse"],
        "source_kind": "native_cells",
        "source_dimensions": list(corpus.native_size),
        "measurement_resolution": [corpus.side, corpus.side],
        "pair_count": len(pairs),
        "qualified": rubric["qualified"],
        "neural_score": rubric["score"],
        "failures": rubric["failures"],
        "qualified_fraction": rubric["qualified_fraction"],
        "object_rubric": rubric,
        "reviewed_at": time.time(),
    }


def publish_native_video(corpus: NativeCorpus, result: dict[str, Any], result_path: pathlib.Path) -> tuple[str, str]:
    """Encode only accepted source cells; never resize, interpolate, or annotate."""
    stem = result_path.stem
    frames_dir = result_path.parent / f"{stem}-native-frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    for prior in frames_dir.glob("frame_*.png"):
        prior.unlink(missing_ok=True)
    indices = [int(value) for value in result["indices"]]
    if bool(result.get("loop")) and indices:
        indices.append(indices[0])
    for position, index in enumerate(indices):
        os.symlink(corpus.paths[index].resolve(), frames_dir / f"frame_{position:05d}.png")
    video_name = f"{stem}.mp4"
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-framerate", str(max(1, int(result.get("fps", 10)))),
        "-i", str(frames_dir / "frame_%05d.png"),
        "-c:v", "libx264", "-preset", "medium", "-crf", "12",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        str(result_path.parent / video_name),
    ]
    subprocess.run(command, check=True, timeout=240)
    # Preserve one literal source symlink for the poster; remove the remaining
    # indexing links. The source corpus itself is never copied or modified.
    for frame in frames_dir.glob("frame_*.png"):
        if frame.name != "frame_00000.png":
            frame.unlink(missing_ok=True)
    return video_name, f"{frames_dir.name}/frame_00000.png"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--side", type=int, default=256)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--poll", type=float, default=0.5)
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, stop_requested)
    signal.signal(signal.SIGINT, stop_requested)
    if not torch.cuda.is_available():
        raise SystemExit("CUDA is required for the Stallion object reviewer")
    torch.backends.cudnn.benchmark = True
    side = max(128, args.side // 8 * 8)
    corpus = NativeCorpus(pathlib.Path(args.source), side)
    segmenter = HorseSegmenter(side)
    flow_weights = Raft_Small_Weights.DEFAULT
    flow_model = raft_small(weights=flow_weights, progress=True).eval().cuda()
    root = pathlib.Path(args.output_root).resolve()
    runs_root = root / "runs"
    reviews_path = root / "gpu-reviews.json"
    try:
        reviews = json.loads(reviews_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        reviews = {"schema": "tea.stallion-motion.gpu-reviews.v2", "reviews": {}}
    indexed: dict[str, Any] = reviews.setdefault("reviews", {})
    while not STOP:
        candidates = sorted(
            (path for path in runs_root.glob("*/r*.json") if is_v2_candidate(path)),
            key=lambda path: path.stat().st_mtime, reverse=True,
        )
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
                evidence = review(flow_model, segmenter, corpus, result, pairs, max(1, args.batch_size))
                key = f"{path.parent.name}/{path.stem}"
                indexed[key] = evidence
                if evidence["qualified"]:
                    video, poster = publish_native_video(corpus, result, path)
                    result["video"] = video
                    result["poster"] = poster
                result["gpu_review"] = evidence
                atomic_json(path, result)
                reviews["updated_at"] = time.time()
                reviews["review_count"] = len(indexed)
                atomic_json(reviews_path, reviews)
                print(json.dumps({"key": key, "qualified": evidence["qualified"], "score": evidence["neural_score"]}), flush=True)
            except (OSError, json.JSONDecodeError, RuntimeError, IndexError, subprocess.SubprocessError) as exc:
                if "out of memory" in str(exc).lower() and args.batch_size > 1:
                    torch.cuda.empty_cache()
                    args.batch_size = max(1, args.batch_size // 2)
                print(json.dumps({"path": str(path), "error": str(exc)}), flush=True)
                time.sleep(0.5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
