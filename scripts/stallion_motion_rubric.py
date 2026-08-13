#!/usr/bin/env python3
"""Fail-closed rubric and topology primitives for Stallion motion.

The values consumed here are normalized by frame diagonal.  The rubric is
deliberately asymmetric: foreground articulation is required, while raw
background/camera motion is always a defect.  A local step on the latent atlas
does not excuse a visually discontinuous result; it only explains where the
candidate came from.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable, Sequence


@dataclass(frozen=True)
class PairEvidence:
    foreground_motion: float
    background_motion: float
    camera_motion: float
    silhouette_change: float
    foreground_acceleration: float = 0.0
    mask_confidence: float = 1.0
    mask_area: float = 0.30
    mirror_symmetry: float = 0.30


@dataclass(frozen=True)
class PairVerdict:
    qualified: bool
    score: float
    foreground_background_ratio: float
    failures: tuple[str, ...]
    evidence: PairEvidence

    def json(self) -> dict[str, object]:
        value = asdict(self)
        value["failures"] = list(self.failures)
        return value


DEFAULT_GATES = {
    "foreground_motion_min": 0.014,
    "foreground_motion_max": 0.180,
    "foreground_motion_target": 0.060,
    "background_motion_max": 0.010,
    "camera_motion_max": 0.008,
    "foreground_background_ratio_min": 2.50,
    "silhouette_change_min": 0.035,
    "silhouette_change_max": 0.380,
    "foreground_acceleration_max": 0.055,
    "mask_confidence_min": 0.72,
    "mask_area_min": 0.06,
    "mask_area_max": 0.72,
    "mirror_symmetry_max": 0.76,
}


def evaluate_pair(evidence: PairEvidence, gates: dict[str, float] | None = None) -> PairVerdict:
    g = {**DEFAULT_GATES, **(gates or {})}
    ratio = evidence.foreground_motion / max(evidence.background_motion, 1e-5)
    failures: list[str] = []
    checks = (
        (evidence.foreground_motion < g["foreground_motion_min"], "foreground_static"),
        (evidence.foreground_motion > g["foreground_motion_max"], "foreground_jump"),
        (evidence.background_motion > g["background_motion_max"], "background_moves"),
        (evidence.camera_motion > g["camera_motion_max"], "camera_moves"),
        (ratio < g["foreground_background_ratio_min"], "inverse_motion"),
        (evidence.silhouette_change < g["silhouette_change_min"], "pose_static"),
        (evidence.silhouette_change > g["silhouette_change_max"], "pose_incoherent"),
        (evidence.foreground_acceleration > g["foreground_acceleration_max"], "motion_jerk"),
        (evidence.mask_confidence < g["mask_confidence_min"], "horse_mask_uncertain"),
        (evidence.mask_area < g["mask_area_min"], "horse_mask_too_small"),
        (evidence.mask_area > g["mask_area_max"], "horse_mask_too_large"),
        (evidence.mirror_symmetry > g["mirror_symmetry_max"], "symmetry"),
    )
    failures.extend(label for failed, label in checks if failed)

    # Lower is better.  Background and camera motion dominate the objective;
    # symmetry receives an intentionally severe hinge penalty.
    score = (
        24.0 * evidence.background_motion
        + 28.0 * evidence.camera_motion
        + 5.0 * abs(evidence.foreground_motion - g["foreground_motion_target"])
        + 2.0 * abs(evidence.silhouette_change - 0.16)
        + 5.0 * evidence.foreground_acceleration
        + 18.0 * max(0.0, g["foreground_background_ratio_min"] - ratio)
        + 30.0 * max(0.0, evidence.mirror_symmetry - g["mirror_symmetry_max"])
        + 100.0 * len(failures)
    )
    return PairVerdict(not failures, float(score), float(ratio), tuple(failures), evidence)


def evaluate_sequence(pairs: Sequence[PairEvidence], gates: dict[str, float] | None = None) -> dict[str, object]:
    if not pairs:
        return {"qualified": False, "score": float("inf"), "failures": ["no_pairs"], "pairs": []}
    verdicts = [evaluate_pair(pair, gates) for pair in pairs]
    failed = sorted({failure for verdict in verdicts for failure in verdict.failures})
    scores = sorted(verdict.score for verdict in verdicts)
    p95_index = min(len(scores) - 1, max(0, round(0.95 * (len(scores) - 1))))
    # One broken transition can ruin a film, so p95 and worst are explicit.
    score = 0.45 * (sum(scores) / len(scores)) + 0.35 * scores[p95_index] + 0.20 * scores[-1]
    qualified_fraction = sum(verdict.qualified for verdict in verdicts) / len(verdicts)
    qualified = qualified_fraction == 1.0
    return {
        "schema": "tea.stallion-motion.object-rubric.v2",
        "qualified": qualified,
        "qualified_fraction": qualified_fraction,
        "score": score,
        "failures": failed,
        "pairs": [verdict.json() for verdict in verdicts],
    }


def serpentine_coordinate(index: int, columns: int) -> tuple[int, int]:
    """Map a row-major storage index onto a row-serpentine atlas coordinate."""
    row, stored_col = divmod(index, columns)
    col = stored_col if row % 2 == 0 else columns - 1 - stored_col
    return row, col


def serpentine_index(row: int, col: int, columns: int) -> int:
    stored_col = col if row % 2 == 0 else columns - 1 - col
    return row * columns + stored_col


def topology_neighbors(
    index: int,
    rows: int,
    columns: int,
    offsets: Iterable[tuple[int, int]],
) -> tuple[int, ...]:
    row, col = serpentine_coordinate(index, columns)
    found: list[int] = []
    for dr, dc in offsets:
        rr, cc = row + dr, col + dc
        if 0 <= rr < rows and 0 <= cc < columns:
            found.append(serpentine_index(rr, cc, columns))
    return tuple(found)


def classify_projection_step(topology_distance: float, verdict: PairVerdict) -> str:
    """Keep topology and perceptual continuity as separate facts."""
    local = topology_distance <= 1.5
    if local and verdict.qualified:
        return "local_and_coherent"
    if local:
        return "projection_local_but_visually_discontinuous"
    if verdict.qualified:
        return "visually_coherent_topology_shortcut"
    return "nonlocal_and_discontinuous"


ADVERSARIAL_FIXTURES = {
    "static_horse_moving_background": PairEvidence(0.003, 0.050, 0.002, 0.008, mirror_symmetry=0.30),
    "moving_horse_stable_background": PairEvidence(0.060, 0.004, 0.001, 0.150, 0.012, mirror_symmetry=0.34),
    "everything_static": PairEvidence(0.002, 0.002, 0.001, 0.006, mirror_symmetry=0.32),
    "camera_pan": PairEvidence(0.060, 0.045, 0.043, 0.140, 0.010, mirror_symmetry=0.35),
    "incoherent_horse": PairEvidence(0.240, 0.004, 0.002, 0.520, 0.120, mirror_symmetry=0.30),
    "coherent_asymmetric_gait": PairEvidence(0.068, 0.003, 0.001, 0.180, 0.014, mirror_symmetry=0.28),
    "symmetric_pose": PairEvidence(0.060, 0.003, 0.001, 0.140, 0.010, mirror_symmetry=0.92),
}


def validate_adversarial_fixtures() -> dict[str, object]:
    verdicts = {name: evaluate_pair(value) for name, value in ADVERSARIAL_FIXTURES.items()}
    expected_pass = {"moving_horse_stable_background", "coherent_asymmetric_gait"}
    observed_pass = {name for name, verdict in verdicts.items() if verdict.qualified}
    return {
        "passed": observed_pass == expected_pass,
        "expected_pass": sorted(expected_pass),
        "observed_pass": sorted(observed_pass),
        "verdicts": {name: verdict.json() for name, verdict in verdicts.items()},
    }
