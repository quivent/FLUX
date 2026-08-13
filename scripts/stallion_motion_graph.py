#!/usr/bin/env python3
"""Mine motion paths from native Tea Stallion cells.

This program deliberately refuses contact sheets and atlas composites.  A
composite is an index/preview, not a frame corpus; slicing and enlarging it was
the failure mode that produced the invalid blurry baseline.  Every encoded
frame is now a literal native ``cell_*.png`` image.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageOps

from stallion_motion_rubric import topology_neighbors


MODES = ("spectral_loop", "continuity", "kinetic")
STOP = False


def stop_requested(_signum: int, _frame: Any) -> None:
    global STOP
    STOP = True


def atomic_json(path: pathlib.Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def update_status(path: pathlib.Path, **fields: Any) -> dict[str, Any]:
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        current = {}
    current.update(fields, updated_at=time.time())
    atomic_json(path, current)
    print(json.dumps({"phase": current.get("phase"), "progress": current.get("progress"), "message": current.get("message")}, separators=(",", ":")), flush=True)
    return current


@dataclass
class Corpus:
    images: np.ndarray
    source_kind: str
    original_size: tuple[int, int]
    source_paths: tuple[pathlib.Path, ...] = ()

    def render_image(self, index: int) -> Image.Image:
        if self.source_paths:
            with Image.open(self.source_paths[index]) as image:
                return image.convert("RGB").copy()
        return Image.fromarray(self.images[index])


def load_corpus(source: pathlib.Path, cfg: dict[str, Any], status: pathlib.Path) -> Corpus:
    size = int(cfg["features"]["analysis_size"])
    if not source.is_dir():
        raise RuntimeError(
            "source-integrity gate: source must be a directory of native "
            "cell_*.png files; atlas/contact-sheet images are prohibited"
        )
    paths = sorted(source.glob("cell_*.png"))
    contract = cfg["source_contract"]
    minimum_cells = int(contract.get("minimum_cells", 8))
    minimum_side = int(contract.get("minimum_native_side", 256))
    if len(paths) < minimum_cells:
        raise RuntimeError(
            f"source-integrity gate: found {len(paths)} native cells, require at least {minimum_cells}"
        )
    images = np.empty((len(paths), size, size, 3), dtype=np.uint8)
    original: tuple[int, int] | None = None
    for index, path in enumerate(paths):
        if STOP:
            raise InterruptedError("stop requested")
        with Image.open(path) as image:
            if image.width < minimum_side or image.height < minimum_side:
                raise RuntimeError(
                    f"source-integrity gate: {path.name} is {image.width}x{image.height}; "
                    f"minimum native side is {minimum_side}px"
                )
            if original is None:
                original = image.size
            elif image.size != original:
                raise RuntimeError(
                    f"source-integrity gate: {path.name} is {image.size}, expected uniform {original}"
                )
            images[index] = np.asarray(
                ImageOps.fit(image.convert("RGB"), (size, size), Image.Resampling.LANCZOS),
                dtype=np.uint8,
            )
        if index % 256 == 0:
            update_status(
                status, phase="extract", progress=index / len(paths), current=index,
                total=len(paths), message="Reading native Stallion cells",
            )
    assert original is not None
    return Corpus(images, "native_cells", original, tuple(paths))


def normalize_rows(values: np.ndarray) -> np.ndarray:
    values = values.astype(np.float32, copy=False)
    values -= values.mean(axis=0, keepdims=True)
    scale = values.std(axis=0, keepdims=True)
    return values / np.maximum(scale, 1e-5)


def resize_batch(images: np.ndarray, side: int) -> np.ndarray:
    result = np.empty((len(images), side, side, images.shape[-1]), dtype=np.float32)
    for i, image in enumerate(images):
        result[i] = np.asarray(Image.fromarray(image).resize((side, side), Image.Resampling.BILINEAR), dtype=np.float32) / 255.0
    return result


def features(images: np.ndarray, cfg: dict[str, Any], status: pathlib.Path) -> dict[str, np.ndarray]:
    rgb = images.astype(np.float32) / 255.0
    gray = rgb @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    n, side, _, _ = rgb.shape
    border = max(2, int(cfg["features"]["background_border"]))
    border_mask = np.ones((side, side), dtype=bool)
    border_mask[border:-border, border:-border] = False
    background = rgb[:, border_mask].reshape(n, -1, 3)
    palette = np.concatenate([rgb.mean((1, 2)), rgb.std((1, 2)), background.mean(1), background.std(1)], axis=1)

    small = resize_batch(images, 8)
    identity = small.reshape(n, -1)
    background_shape = np.concatenate([
        small[:, :2].reshape(n, -1), small[:, -2:].reshape(n, -1),
        small[:, 2:-2, :2].reshape(n, -1), small[:, 2:-2, -2:].reshape(n, -1),
    ], axis=1)

    crop = cfg["features"]["pose_crop"]
    x0, y0, x1, y1 = int(crop[0] * side), int(crop[1] * side), int(crop[2] * side), int(crop[3] * side)
    horse = gray[:, y0:y1, x0:x1]
    horse_imgs = np.empty((n, 16, 16), dtype=np.float32)
    for i in range(n):
        horse_imgs[i] = np.asarray(Image.fromarray(np.uint8(np.clip(horse[i] * 255, 0, 255))).resize((16, 16), Image.Resampling.BILINEAR), dtype=np.float32) / 255.0
    gx = np.diff(horse_imgs, axis=2, append=horse_imgs[:, :, -1:])
    gy = np.diff(horse_imgs, axis=1, append=horse_imgs[:, -1:, :])
    edges = np.sqrt(gx * gx + gy * gy)
    pose = np.concatenate([horse_imgs[:, ::2, ::2].reshape(n, -1), edges[:, ::2, ::2].reshape(n, -1)], axis=1)

    # Composition proxy: weighted centroid and spread of local contrast. This
    # tracks the horse mass more reliably than raw luminance on the pink field.
    contrast = np.abs(horse_imgs - np.median(horse_imgs, axis=(1, 2), keepdims=True)) + edges
    yy, xx = np.mgrid[0:16, 0:16].astype(np.float32)
    mass = np.maximum(contrast.sum((1, 2)), 1e-6)
    cx = (contrast * xx).sum((1, 2)) / mass / 15.0
    cy = (contrast * yy).sum((1, 2)) / mass / 15.0
    sx = np.sqrt(np.maximum((contrast * (xx[None] - cx[:, None, None] * 15) ** 2).sum((1, 2)) / mass, 1e-6)) / 15.0
    sy = np.sqrt(np.maximum((contrast * (yy[None] - cy[:, None, None] * 15) ** 2).sum((1, 2)) / mass, 1e-6)) / 15.0
    composition = np.stack([cx, cy, sx, sy, mass / 256.0], axis=1)

    lap = np.diff(gray, n=2, axis=1, append=gray[:, -2:]) + np.diff(gray, n=2, axis=2, append=gray[:, :, -2:])
    sharpness = np.mean(lap * lap, axis=(1, 2))
    quality = np.stack([sharpness, gray.std((1, 2)), 1.0 - np.abs(gray.mean((1, 2)) - 0.52)], axis=1)
    update_status(status, phase="features", progress=1.0, current=n, total=n, message="Identity, pose, background, composition, and quality descriptors ready")
    return {
        "identity": normalize_rows(identity),
        "pose": normalize_rows(pose),
        "background": normalize_rows(background_shape),
        "palette": normalize_rows(palette),
        "composition_raw": composition.astype(np.float32),
        "composition": normalize_rows(composition),
        "quality": normalize_rows(quality),
        "quality_raw": quality.astype(np.float32),
    }


def pca(values: np.ndarray, components: int) -> tuple[np.ndarray, np.ndarray]:
    centered = values - values.mean(axis=0, keepdims=True)
    _, _, vt = np.linalg.svd(centered, full_matrices=False)
    axes = vt[:components]
    return centered @ axes.T, axes


def kmeans(values: np.ndarray, groups: int, seed: int, iterations: int = 18) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = len(values)
    centers = [values[int(rng.integers(n))]]
    nearest = np.full(n, np.inf, dtype=np.float32)
    for _ in range(1, groups):
        dist = np.sum((values - centers[-1]) ** 2, axis=1)
        nearest = np.minimum(nearest, dist)
        probability = nearest / max(float(nearest.sum()), 1e-9)
        centers.append(values[int(rng.choice(n, p=probability))])
    centers = np.asarray(centers, dtype=np.float32)
    labels = np.zeros(n, dtype=np.int32)
    for _ in range(iterations):
        distance = np.sum((values[:, None] - centers[None]) ** 2, axis=2)
        labels = np.argmin(distance, axis=1).astype(np.int32)
        for group in range(groups):
            members = values[labels == group]
            if len(members):
                centers[group] = members.mean(axis=0)
    return labels


def unit(values: np.ndarray) -> np.ndarray:
    return values / np.maximum(np.linalg.norm(values, axis=1, keepdims=True), 1e-7)


def percentile_scale(values: np.ndarray, high: float = 90.0) -> np.ndarray:
    return np.clip(values / max(float(np.percentile(values, high)), 1e-6), 0.0, 2.0)


def family_model(feat: dict[str, np.ndarray], cfg: dict[str, Any], seed: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    family_input, _ = pca(np.concatenate([feat["identity"], feat["background"], feat["palette"]], axis=1), 20)
    labels = kmeans(normalize_rows(family_input), int(cfg["features"]["family_count"]), seed)
    phase = np.zeros(len(labels), dtype=np.float32)
    pose_projection = np.zeros((len(labels), 4), dtype=np.float32)
    for family in np.unique(labels):
        members = np.flatnonzero(labels == family)
        projection, _ = pca(feat["pose"][members], 4)
        pose_projection[members, :projection.shape[1]] = projection
        angle = np.arctan2(projection[:, 1], projection[:, 0])
        phase[members] = ((angle + 2 * np.pi) % (2 * np.pi) / (2 * np.pi)).astype(np.float32)
    return labels, phase, normalize_rows(pose_projection)


def topology_layout(count: int, cfg: dict[str, Any]) -> tuple[int, int]:
    for layout in cfg["topology"]["layouts"]:
        if int(layout["cells"]) == count:
            return int(layout["rows"]), int(layout["columns"])
    raise RuntimeError(
        f"topology gate: no declared atlas layout for {count} cells; "
        "refusing to infer adjacency from image similarity"
    )


def candidate_graph(feat: dict[str, np.ndarray], labels: np.ndarray, phase: np.ndarray, pose_projection: np.ndarray, cfg: dict[str, Any], status: pathlib.Path) -> dict[str, np.ndarray]:
    n = len(labels)
    k = int(cfg["features"]["candidate_neighbors"])
    neighbors = np.full((n, k), -1, dtype=np.int32)
    costs = np.full((n, k), np.inf, dtype=np.float32)
    components = np.zeros((n, k, 7), dtype=np.float32)
    weights = cfg["edge_weights"]
    gates = cfg["hard_gates"]
    rows, columns = topology_layout(n, cfg)
    offsets = [tuple(map(int, value)) for value in cfg["topology"]["candidate_offsets"]]
    completed = 0
    for index in range(n):
            candidates = np.asarray(topology_neighbors(index, rows, columns, offsets), dtype=np.int32)
            if not len(candidates):
                completed += 1
                continue
            ident = percentile_scale(np.mean((feat["identity"][candidates] - feat["identity"][index]) ** 2, axis=1))
            pose_delta = np.linalg.norm(pose_projection[candidates] - pose_projection[index], axis=1)
            pose_cost = np.abs(percentile_scale(pose_delta) - 0.55)
            background = percentile_scale(np.mean((feat["background"][candidates] - feat["background"][index]) ** 2, axis=1))
            palette = percentile_scale(np.mean((feat["palette"][candidates] - feat["palette"][index]) ** 2, axis=1))
            composition_delta = np.abs(feat["composition_raw"][candidates] - feat["composition_raw"][index])
            composition = percentile_scale(composition_delta.mean(axis=1))
            flow = percentile_scale(np.linalg.norm(composition_delta[:, :2], axis=1))
            q = feat["quality_raw"][candidates, 0]
            quality = 1.0 - np.clip(q / max(float(np.percentile(feat["quality_raw"][:, 0], 85)), 1e-7), 0.0, 1.0)
            phase_advance = (phase[candidates] - phase[index]) % 1.0
            visual = 0.45 * ident + 0.30 * background + 0.15 * composition + 0.10 * palette
            valid = (
                (visual >= float(gates["duplicate_distance_min"]))
                & (visual <= float(gates["visual_jump_max"]))
                & (composition_delta[:, :2].max(axis=1) <= float(gates["centroid_jump_max"]))
                & (np.abs(np.log(np.maximum(feat["composition_raw"][candidates, 2], 1e-4) / max(float(feat["composition_raw"][index, 2]), 1e-4))) <= float(gates["scale_log_jump_max"]))
                & (phase_advance >= float(gates["phase_advance_min"]))
                & (phase_advance <= float(gates["phase_advance_max"]))
            )
            score = (
                float(weights["identity"]) * ident
                + float(weights["pose_progression"]) * pose_cost
                + float(weights["background"]) * background
                + float(weights["flow"]) * flow
                + float(weights["composition"]) * composition
                + float(weights["quality"]) * quality
                + float(weights["palette"]) * palette
            )
            accepted = np.flatnonzero(valid)
            # Fail closed. A node with no admissible topological transition is
            # a dead end, not permission to use the least-bad invalid edge.
            accepted = accepted[np.argsort(score[accepted])[:k]]
            count = min(k, len(accepted))
            neighbors[index, :count] = candidates[accepted[:count]]
            costs[index, :count] = score[accepted[:count]]
            components[index, :count] = np.stack([ident, pose_cost, background, flow, composition, quality, palette], axis=1)[accepted[:count]]
            completed += 1
            if index % 256 == 0:
                update_status(status, phase="graph", progress=completed / n, current=completed, total=n, message="Scoring declared atlas-topology transitions")
    return {"neighbors": neighbors, "costs": costs, "components": components}


def edge_lookup(graph: dict[str, np.ndarray], source: int, target: int) -> tuple[float, np.ndarray] | None:
    positions = np.flatnonzero(graph["neighbors"][source] == target)
    if not len(positions):
        return None
    pos = int(positions[0])
    return float(graph["costs"][source, pos]), graph["components"][source, pos]


def direct_visual_jump(feat: dict[str, np.ndarray], source: int, target: int) -> float:
    """Comparable fallback for loop seams that are outside the directed graph."""
    def bounded_rms(name: str) -> float:
        rms = float(np.sqrt(np.mean((feat[name][source] - feat[name][target]) ** 2)))
        return rms / (1.0 + rms)

    composition = float(np.mean(np.abs(feat["composition_raw"][source] - feat["composition_raw"][target])))
    return 0.45 * bounded_rms("identity") + 0.30 * bounded_rms("background") + 0.15 * min(composition * 3.0, 1.0) + 0.10 * bounded_rms("palette")


def beam_path(mode: str, labels: np.ndarray, phase: np.ndarray, pose: np.ndarray, graph: dict[str, np.ndarray], feat: dict[str, np.ndarray], cfg: dict[str, Any], frames: int, seed: int) -> list[int]:
    rng = np.random.default_rng(seed)
    mode_cfg = cfg["modes"][mode]
    seq_cfg = cfg["sequence_weights"]
    family_counts = np.bincount(labels)
    family_order = np.argsort(-family_counts)
    family = int(family_order[seed % min(5, len(family_order))])
    members = np.flatnonzero(labels == family)
    quality = feat["quality_raw"][members, 0]
    start_pool = members[np.argsort(-quality)[: min(96, len(members))]]
    start = int(rng.choice(start_pool))
    beam: list[tuple[float, list[int], float]] = [(0.0, [start], 0.0)]
    beam_width = 180
    target_motion = float(mode_cfg["motion_target"])
    tolerance = float(mode_cfg["jump_tolerance"])
    for _step in range(1, frames):
        expanded: list[tuple[float, list[int], float]] = []
        for total_cost, path, last_visual in beam:
            current = path[-1]
            for pos, target in enumerate(graph["neighbors"][current]):
                target = int(target)
                if target < 0 or target in path:
                    continue
                edge_cost = float(graph["costs"][current, pos])
                comp = graph["components"][current, pos]
                visual = float(0.45 * comp[0] + 0.30 * comp[2] + 0.15 * comp[4] + 0.10 * comp[6])
                if visual > tolerance:
                    continue
                phase_step = float((phase[target] - phase[current]) % 1.0)
                motion_penalty = abs(phase_step - target_motion)
                acceleration = abs(visual - last_visual) if len(path) > 1 else 0.0
                pose_acceleration = 0.0
                reversal = 0.0
                if len(path) > 1:
                    a, b = path[-2], current
                    v1, v2 = pose[b] - pose[a], pose[target] - pose[b]
                    pose_acceleration = float(np.linalg.norm(v2 - v1)) / max(1.0, math.sqrt(pose.shape[1]))
                    reversal = 1.0 if float(np.dot(v1, v2)) < 0 else 0.0
                cost = total_cost + float(seq_cfg["transition"]) * edge_cost + motion_penalty
                cost += float(seq_cfg["pose_acceleration"]) * pose_acceleration
                cost += float(seq_cfg["visual_acceleration"]) * acceleration
                cost += float(seq_cfg["direction_reversal"]) * reversal
                expanded.append((cost, path + [target], visual))
        if not expanded:
            break
        expanded.sort(key=lambda row: row[0])
        beam = expanded[:beam_width]
    if not beam:
        return [start]
    if bool(mode_cfg["loop"]):
        reranked = []
        for cost, path, visual in beam:
            seam = edge_lookup(graph, path[-1], path[0])
            if seam is None:
                continue
            seam_cost = seam[0]
            reranked.append((cost + float(seq_cfg["loop_seam"]) * seam_cost, path, visual))
        if not reranked:
            return [start]
        beam = sorted(reranked, key=lambda row: row[0])
    return beam[0][1]


def sequence_metrics(path: list[int], phase: np.ndarray, pose: np.ndarray, graph: dict[str, np.ndarray], feat: dict[str, np.ndarray], loop: bool) -> dict[str, Any]:
    edges = []
    visuals = []
    reversals = 0
    for a, b in zip(path, path[1:]):
        found = edge_lookup(graph, a, b)
        if found:
            cost, comp = found
            visual = float(0.45 * comp[0] + 0.30 * comp[2] + 0.15 * comp[4] + 0.10 * comp[6])
            visuals.append(visual)
            edges.append({"from": a, "to": b, "cost": cost, "visual_jump": visual, "phase_advance": float((phase[b] - phase[a]) % 1.0), "components": {name: float(value) for name, value in zip(("identity", "pose", "background", "flow", "composition", "quality", "palette"), comp)}})
    for a, b, c in zip(path, path[1:], path[2:]):
        if float(np.dot(pose[b] - pose[a], pose[c] - pose[b])) < 0:
            reversals += 1
    seam = edge_lookup(graph, path[-1], path[0]) if loop and len(path) > 1 else None
    seam_visual = None
    if seam:
        _, comp = seam
        seam_visual = float(0.45 * comp[0] + 0.30 * comp[2] + 0.15 * comp[4] + 0.10 * comp[6])
    scored_visuals = visuals + ([seam_visual] if seam_visual is not None else [])
    selection_score = (
        (float(np.max(scored_visuals)) if scored_visuals else 1.0) * 0.45
        + (float(np.percentile(scored_visuals, 95)) if scored_visuals else 1.0) * 0.25
        + (float(np.mean(scored_visuals)) if scored_visuals else 1.0) * 0.20
        + min(reversals / max(1, len(path) - 2), 1.0) * 0.10
    )
    return {
        "frames": len(path),
        "unique_frames": len(set(path)),
        "mean_visual_jump": float(np.mean(visuals)) if visuals else 0.0,
        "p95_visual_jump": float(np.percentile(visuals, 95)) if visuals else 0.0,
        "worst_visual_jump": float(np.max(visuals)) if visuals else 0.0,
        "direction_reversals": reversals,
        "loop_seam_jump": seam_visual,
        "selection_score": selection_score,
        "edges": edges,
    }


def display_frame(image: np.ndarray | Image.Image, index: int) -> Image.Image:
    """Return source pixels unchanged; labels belong in HTML, not the film."""
    base = image if isinstance(image, Image.Image) else Image.fromarray(image)
    return base.convert("RGB").copy()


def write_video(corpus: Corpus, path: list[int], out: pathlib.Path, fps: int, loop: bool = False) -> bool:
    frames_dir = out.parent / (out.stem + "-frames")
    frames_dir.mkdir(parents=True, exist_ok=True)
    video_path = path + [path[0]] if loop and path else path
    for position, index in enumerate(video_path):
        display_frame(corpus.render_image(index), index).save(frames_dir / f"frame_{position:05d}.png")
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-framerate", str(fps), "-i", str(frames_dir / "frame_%05d.png"), "-c:v", "libx264", "-preset", "medium", "-crf", "12", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)]
    try:
        subprocess.run(cmd, check=True, timeout=240)
        # Keep the first frame as the gallery poster; the remaining JPEGs are
        # encoding intermediates and would dominate an open-ended run's disk.
        for frame in frames_dir.glob("frame_*.png"):
            if frame.name != "frame_00000.png":
                frame.unlink(missing_ok=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def write_contact_sheet(corpus: Corpus, paths: dict[str, list[int]], out: pathlib.Path) -> None:
    thumb, cols = 150, 8
    rows = sum(math.ceil(len(path) / cols) + 1 for path in paths.values())
    sheet = Image.new("RGB", (cols * thumb, rows * thumb), (247, 245, 240))
    draw = ImageDraw.Draw(sheet)
    row = 0
    for mode, path in paths.items():
        draw.text((12, row * thumb + 16), mode.replace("_", " ").upper(), fill=(67, 54, 42))
        row += 1
        for pos, index in enumerate(path):
            tile = corpus.render_image(index).resize((thumb, thumb), Image.Resampling.LANCZOS)
            x, y = (pos % cols) * thumb, (row + pos // cols) * thumb
            sheet.paste(tile, (x, y))
            draw.rectangle((x, y + thumb - 20, x + 70, y + thumb), fill=(25, 21, 18))
            draw.text((x + 5, y + thumb - 17), f"{index:05d}", fill=(245, 237, 225))
        row += math.ceil(len(path) / cols)
    sheet.save(out, quality=91)


def ranked_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = sorted(results, key=lambda row: row["selection_score"])
    for rank, result in enumerate(ranked, start=1):
        result["rank"] = rank
    return ranked


def public_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{
        "mode": row["mode"], "round": row["round"], "rank": row["rank"],
        "family": row["family"], "description": row["description"],
        "selection_score": row["selection_score"], "video": row["video"],
        "poster": row["poster"], "metrics": {key: value for key, value in row["metrics"].items() if key != "edges"},
    } for row in results]


def prune_results(run_dir: pathlib.Path, results: list[dict[str, Any]], retain: int) -> list[dict[str, Any]]:
    ranked = ranked_results(results)
    for result in ranked[retain:]:
        if video := str(result.get("video", "")):
            pathlib.Path(run_dir / video).unlink(missing_ok=True)
        pathlib.Path(run_dir / f"r{int(result['round']):02d}-{result['mode']}.json").unlink(missing_ok=True)
        shutil.rmtree(run_dir / f"r{int(result['round']):02d}-{result['mode']}-frames", ignore_errors=True)
    return ranked[:retain]


def checkpoint_run(run_dir: pathlib.Path, status: pathlib.Path, protocol: dict[str, Any], corpus: Corpus,
                   run_id: str, results: list[dict[str, Any]], completed_rounds: int,
                   contact_sheet: str, continuous: bool, state: str = "running") -> list[dict[str, Any]]:
    ranked = ranked_results(results)
    compact = public_results(ranked)
    manifest = {
        "schema": "tea.stallion-motion.results.v1",
        "run_id": run_id,
        "state": state,
        "continuous": continuous,
        "completed_rounds": completed_rounds,
        "source_kind": corpus.source_kind,
        "source_count": len(corpus.images),
        "source_dimensions": list(corpus.original_size),
        "analysis_resolution": [int(corpus.images.shape[2]), int(corpus.images.shape[1])],
        "delivery_resolution": list(corpus.original_size),
        "protocol": protocol,
        "results": compact,
        "contact_sheet": contact_sheet,
        "updated_at": time.time(),
    }
    atomic_json(run_dir / "manifest.json", manifest)
    update_status(
        status, state=state, phase="search" if state == "running" else state, progress=1.0,
        completed_rounds=completed_rounds, result_count=len(ranked), results=compact,
        contact_sheet=contact_sheet, source_kind=corpus.source_kind,
        message=f"Round {completed_rounds} complete · {len(ranked)} candidates ranked" + (" · continuing" if continuous and state == "running" else ""),
    )
    return ranked


def main() -> int:
    parser = argparse.ArgumentParser(description="Tea Stallion motion graph experiment")
    parser.add_argument("--source", required=True)
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--modes", default=",".join(MODES))
    parser.add_argument("--frames", type=int, default=24)
    parser.add_argument("--fps", type=int, default=10)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument("--continuous", action="store_true")
    parser.add_argument("--round-pause", type=float, default=1.0)
    parser.add_argument("--retain", type=int, default=256)
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, stop_requested)
    signal.signal(signal.SIGINT, stop_requested)
    protocol = json.loads(pathlib.Path(args.protocol).read_text(encoding="utf-8"))
    modes = [mode.strip() for mode in args.modes.split(",") if mode.strip() in MODES]
    if not modes:
        raise SystemExit("no valid modes selected")
    frames = min(96, max(8, args.frames))
    rounds = min(12, max(1, args.rounds))
    continuous = bool(args.continuous)
    retain = min(2048, max(len(modes), args.retain))
    run_id = args.run_id.strip() or time.strftime("stallion-motion-%Y%m%d-%H%M%S")
    root = pathlib.Path(args.output_root).expanduser().resolve()
    run_dir = root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    status = run_dir / "status.json"
    pid_path = run_dir / "pid"
    pid_path.write_text(str(os.getpid()) + "\n", encoding="utf-8")
    atomic_json(root / "latest.json", {"run_id": run_id, "status_url": f"runs/{run_id}/status.json"})
    update_status(status, schema="tea.stallion-motion.run.v1", run_id=run_id, state="running", phase="start", progress=0.0, started_at=time.time(), source=str(pathlib.Path(args.source).name), protocol=protocol["schema"], modes=modes, frames=frames, fps=args.fps, rounds=0 if continuous else rounds, continuous=continuous, message="Starting continuous motion graph experiment" if continuous else "Starting motion graph experiment")
    try:
        corpus = load_corpus(pathlib.Path(args.source), protocol, status)
        if STOP:
            raise InterruptedError("stop requested")
        feat = features(corpus.images, protocol, status)
        update_status(status, phase="families", progress=0.0, message="Separating coherent shot families and recovering gait phase")
        labels, phase, pose_projection = family_model(feat, protocol, args.seed)
        update_status(status, phase="families", progress=1.0, message=f"Recovered {len(np.unique(labels))} scene families")
        graph = candidate_graph(feat, labels, phase, pose_projection, protocol, status)
        if STOP:
            raise InterruptedError("stop requested")
        selected: dict[str, list[int]] = {}
        results: list[dict[str, Any]] = []
        seen_paths: set[str] = set()
        candidate_count = 0
        round_index = 0
        while continuous or round_index < rounds:
            round_paths: dict[str, list[int]] = {}
            for mode_index, mode in enumerate(modes):
                if STOP:
                    raise InterruptedError("stop requested")
                stem = f"r{round_index + 1:02d}-{mode}"
                round_label = f"Round {round_index + 1}" if continuous else f"Round {round_index + 1}/{rounds}"
                update_status(status, phase="search", progress=mode_index / len(modes), current=mode_index, total=len(modes), current_round=round_index + 1, message=f"{round_label} · optimizing {mode.replace('_', ' ')}")
                path = beam_path(mode, labels, phase, pose_projection, graph, feat, protocol, frames, args.seed + round_index * 1009 + mode_index * 97)
                if len(path) != frames:
                    update_status(
                        status, phase="search", message=(
                            f"Rejected {mode.replace('_', ' ')} proposal: topology/gates produced "
                            f"{len(path)} of {frames} required frames"
                        ),
                    )
                    continue
                selected[stem] = path
                round_paths[stem] = path
                candidate_count += 1
                fingerprint = hashlib.sha1(np.asarray(path, dtype=np.int32).tobytes()).hexdigest()
                if fingerprint in seen_paths:
                    continue
                seen_paths.add(fingerprint)
                loop = bool(protocol["modes"][mode]["loop"])
                metrics = sequence_metrics(path, phase, pose_projection, graph, feat, loop)
                # Proposals are metadata only. The GPU object rubric publishes
                # a literal native-cell film after, and only after, it passes.
                result = {"schema": "tea.stallion-motion.proposal.v2", "protocol": protocol["schema"], "mode": mode, "round": round_index + 1, "family": int(labels[path[0]]), "fingerprint": fingerprint, "description": protocol["modes"][mode]["description"], "indices": path, "selection_score": metrics["selection_score"], "metrics": metrics, "video": "", "poster": "", "fps": args.fps, "loop": loop}
                atomic_json(run_dir / f"{stem}.json", result)
                results.append(result)
            round_index += 1
            results = prune_results(run_dir, results, retain)
            sheet_name = "latest-round-contact-sheet.jpg" if continuous else "contact-sheet.jpg"
            write_contact_sheet(corpus, round_paths if continuous else selected, run_dir / sheet_name)
            ranked = checkpoint_run(run_dir, status, protocol, corpus, run_id, results, round_index, sheet_name, continuous)
            update_status(status, candidate_count=candidate_count, retained_count=len(ranked), retain_limit=retain)
            if shutil.disk_usage(run_dir).free < 2 * 1024**3:
                raise RuntimeError("continuous run stopped before free disk fell below 2 GiB")
            if continuous:
                deadline = time.time() + max(0.0, args.round_pause)
                while time.time() < deadline and not STOP:
                    time.sleep(max(0.0, min(0.2, deadline - time.time())))
        ranked = checkpoint_run(run_dir, status, protocol, corpus, run_id, results, round_index, "contact-sheet.jpg", False, state="complete")
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        manifest["completed_at"] = time.time()
        atomic_json(run_dir / "manifest.json", manifest)
        update_status(status, state="complete", phase="complete", progress=1.0, completed_at=time.time(), message=f"Completed and ranked {len(ranked)} motion candidates")
        return 0
    except InterruptedError as exc:
        update_status(status, state="stopped", phase="stopped", message=str(exc), stopped_at=time.time())
        return 130
    except Exception as exc:
        update_status(status, state="error", phase="error", message=str(exc), error=repr(exc), failed_at=time.time())
        raise


if __name__ == "__main__":
    sys.exit(main())
