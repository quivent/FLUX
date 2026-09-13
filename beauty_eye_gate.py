#!/usr/bin/env python3
"""Operator eye-gate for Beauty jury candidates.

Machine rankings order attention; they never remove a candidate. Operator words are
stored verbatim in an append-only ralpheye-compatible taste log.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

import pipeline_paths


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    if not path.is_file():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def pending_slate(output_dir: Path) -> list[dict[str, Any]]:
    """Return every latest candidate, ranked but never filtered."""
    latest: dict[str, dict[str, Any]] = {}
    for row in _read_jsonl(output_dir / "eye-gate-candidates.jsonl"):
        latest[str(row.get("job_id"))] = row
    decided = {
        str(row.get("candidate_id"))
        for row in _read_jsonl(output_dir / "taste-log.jsonl")
    }
    rows = [row for job, row in latest.items() if job not in decided]
    rows.sort(
        key=lambda row: (
            row.get("percentile_rank") is not None,
            float(row.get("percentile_rank") or -1),
            float(row.get("ts") or 0),
        ),
        reverse=True,
    )
    return rows


def record_verdict(
    output_dir: Path,
    job_id: str,
    verdict: str,
    words: str,
    delta: float,
    generation: int,
) -> dict[str, Any]:
    slate = pending_slate(output_dir)
    candidate = next((row for row in slate if str(row.get("job_id")) == job_id), None)
    if candidate is None:
        raise ValueError(f"pending candidate {job_id!r} was not found")
    rank = next(i for i, row in enumerate(slate, 1) if row is candidate)
    visual_roles = {
        str(row.get("role")) for row in candidate.get("judges", [])
        if not row.get("degraded")
    }
    record = {
        "candidate_id": job_id,
        "generation": generation,
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
        "critic_score": candidate.get("curved_score"),
        "critic_rank": rank,
        "operator_verdict": verdict,
        "operator_words": words,
        "delta": delta,
        "observed": "structure" in visual_roles and "aesthetic" in visual_roles,
        "artifact_path": candidate.get("image_path"),
        "notes": "Beauty EGRL operator eye-gate",
        "promoted_law": None,
        "named_register": None,
        "machine_recommendation": candidate.get("machine_recommendation", candidate.get("tier")),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "taste-log.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    if verdict == "crown":
        promoted = dict(candidate)
        promoted["operator_gate"] = {
            **dict(candidate.get("operator_gate") or {}),
            "status": "crowned",
            "operator_words": words,
        }
        promoted["is_masterpiece"] = True
        with (output_dir / "masterpiece_vault.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(promoted, ensure_ascii=False, default=str) + "\n")
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default=str(pipeline_paths.OUT_DIR))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("slate", help="print every pending candidate in ranked order")
    record = sub.add_parser("record", help="persist one operator verdict verbatim")
    record.add_argument("--job-id", required=True)
    record.add_argument("--verdict", required=True, choices=("kill", "crown", "note"))
    record.add_argument("--words", required=True, help="operator words, stored verbatim")
    record.add_argument("--delta", required=True, type=float, help="signed operator-vs-critic placement delta")
    record.add_argument("--generation", type=int, default=1)
    args = parser.parse_args()
    output_dir = Path(args.output_dir).expanduser().resolve()
    if args.command == "slate":
        print(json.dumps(pending_slate(output_dir), indent=2, default=str))
    else:
        print(json.dumps(record_verdict(
            output_dir, args.job_id, args.verdict, args.words,
            args.delta, args.generation,
        ), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
