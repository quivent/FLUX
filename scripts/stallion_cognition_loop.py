#!/usr/bin/env python3
"""Continuous diagnosis loop for the v2 Stallion object rubric.

This loop does not loosen gates and does not launch compute. It watches new GPU
reviews, names the dominant failure, records exactly one next hypothesis, and
asks the Governor once per distinct batch when the endpoint is healthy. That
separation prevents a judge from silently becoming an optimizer for its own
metric.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import signal
import time
import urllib.error
import urllib.request
from collections import Counter
from typing import Any

from stallion_motion_rubric import validate_adversarial_fixtures


STOP = False
GOVERNOR = "https://governor.influx.vision/v1/chat/completions"
HYPOTHESES = {
    "background_moves": "Restrict topology radius before changing any perceptual threshold.",
    "camera_moves": "Estimate and reject global homography before evaluating residual object flow.",
    "inverse_motion": "Increase the foreground/background ratio gate; never invert optical flow.",
    "foreground_static": "Require a larger silhouette phase step while preserving background gates.",
    "pose_static": "Change the candidate topology offset, not the motion score weights.",
    "pose_incoherent": "Reduce topology radius and maximum silhouette change together as one hypothesis.",
    "motion_jerk": "Add a third-order foreground-flow consistency gate.",
    "horse_mask_uncertain": "Improve or manually verify masks; do not score uncertain frames.",
    "horse_mask_too_small": "Treat the segmentation as failed; do not enlarge its image.",
    "horse_mask_too_large": "Exclude frames where the mask absorbs background.",
    "symmetry": "Reject the pose family before path search; retain the severe symmetry hinge.",
    "cumulative_background_drift": "Reduce topology radius; keep per-edge background and camera gates fixed.",
    "latent_state_revisit": "Reject repeated latent states before GPU review.",
    "phase_reversal": "Require monotonic pose-phase progression across the whole path.",
    "mask_centroid_pop": "Reject centroid-discontinuous masks before flow scoring.",
    "mask_boundary_pop": "Reject unstable silhouettes; do not smooth their masks to hide the failure.",
}


def stop_requested(_signum: int, _frame: Any) -> None:
    global STOP
    STOP = True


def atomic_json(path: pathlib.Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def load_reviews(path: pathlib.Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {
        key: review for key, review in (payload.get("reviews") or {}).items()
        if review.get("schema") == "tea.stallion-motion.gpu-review.v2"
    }


def summarize(reviews: dict[str, Any]) -> dict[str, Any]:
    failures: Counter[str] = Counter()
    qualified = 0
    for review in reviews.values():
        qualified += bool(review.get("qualified"))
        failures.update(str(value) for value in review.get("failures") or [])
    dominant = failures.most_common(1)[0][0] if failures else "none"
    return {
        "review_count": len(reviews),
        "qualified_count": qualified,
        "qualified_rate": qualified / max(1, len(reviews)),
        "failure_counts": dict(failures.most_common()),
        "dominant_failure": dominant,
        "one_next_hypothesis": HYPOTHESES.get(dominant, "Keep the rubric fixed and acquire more native evidence."),
    }


def ask_governor(summary: dict[str, Any], timeout: float) -> tuple[str, str]:
    token = (os.environ.get("GOVERNOR_TOKEN") or os.environ.get("CHORUS_GOVERNOR_TOKEN") or "").strip()
    if not token:
        return "unavailable", "Governor bearer token is not configured"
    prompt = (
        "Review one iteration of the Tea Stallion motion protocol. The immutable intent is: "
        "the horse articulates, the background and camera remain stable, native frames are stitched "
        "without interpolation, symmetry is a hard failure. Do not propose loosening a safety gate. "
        "Challenge the single next hypothesis and name one falsifying experiment. Batch summary:\n"
        + json.dumps(summary, sort_keys=True)
    )
    body = json.dumps({
        "model": "governor", "max_tokens": 500,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    request = urllib.request.Request(GOVERNOR, body, {
        "Content-Type": "application/json", "Accept": "application/json",
        "Authorization": "Bearer " + token, "User-Agent": "tea-stallion-cognition/2",
    }, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
        content = payload.get("choices", [{}])[0].get("message", {}).get("content", "")
        return ("received", str(content)) if content else ("invalid", "Governor returned no content")
    except urllib.error.HTTPError as exc:
        return "unavailable", f"Governor HTTP {exc.code}; preserved local hypothesis"
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return "unavailable", f"Governor unreachable: {exc}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--poll", type=float, default=10.0)
    parser.add_argument("--governor-timeout", type=float, default=180.0)
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, stop_requested)
    signal.signal(signal.SIGINT, stop_requested)
    root = pathlib.Path(args.output_root).resolve()
    reviews_path = root / "gpu-reviews.json"
    status_path = root / "cognition-status.json"
    prior_fingerprint = ""
    while not STOP:
        fixtures = validate_adversarial_fixtures()
        reviews = load_reviews(reviews_path)
        summary = summarize(reviews)
        fingerprint = hashlib.sha256(json.dumps(summary, sort_keys=True).encode()).hexdigest()
        status: dict[str, Any] = {
            "schema": "tea.stallion-motion.cognition.v2",
            "updated_at": time.time(), "adversarial_gate": fixtures,
            "batch": summary, "fingerprint": fingerprint,
        }
        if not fixtures["passed"]:
            status.update(state="gated", reason="adversarial rubric regression")
        elif not reviews:
            status.update(state="waiting_for_native_reviews")
        elif fingerprint != prior_fingerprint:
            governor_state, governor_guidance = ask_governor(summary, args.governor_timeout)
            status.update(
                state="diagnosed", governor_state=governor_state,
                governor_guidance=governor_guidance,
                law="Change one hypothesis, rerun fixtures, then compare a small native batch before scaling.",
            )
            prior_fingerprint = fingerprint
        else:
            try:
                previous = json.loads(status_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                previous = {}
            status.update(
                state=previous.get("state", "watching"),
                governor_state=previous.get("governor_state"),
                governor_guidance=previous.get("governor_guidance"),
                law=previous.get("law"),
            )
        atomic_json(status_path, status)
        deadline = time.time() + max(1.0, args.poll)
        while time.time() < deadline and not STOP:
            time.sleep(min(0.5, deadline - time.time()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
