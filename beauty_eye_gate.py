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

try:
    import pipeline_paths  # needs Python 3.11+ (tomllib). Optional: only used for defaults.
    _PP_OUT_DIR = str(pipeline_paths.OUT_DIR)
    _PP_ROOT = getattr(pipeline_paths, "ROOT", str(Path.cwd()))
except Exception:  # pragma: no cover - older interpreters / missing deps
    pipeline_paths = None
    _PP_OUT_DIR = os.environ.get("FLUX_OUT_DIR", str(Path.cwd() / "outputs"))
    _PP_ROOT = os.environ.get("FLUX_HOME", str(Path.cwd()))


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


def submit_to_worker(
    output_dir: Path,
    socket_path: str,
    job_id: str | None,
    brief: str | None,
    steps: int,
    guidance: float,
    width: int,
    height: int,
    seed: str | None,
) -> dict[str, Any]:
    """Final submission: push an approved brief back to the FLUX worker.

    This is the step that closes the loop when the operator is not continuously
    present. A crowned candidate's prompt (or an explicit brief) is submitted to
    the resident worker as the next generation's brief, and the submission is
    recorded so the ledger shows what actually re-entered production.
    """
    prompt = (brief or "").strip()
    seed_val = seed
    source = "brief"
    if not prompt and job_id:
        # Pull the prompt/seed from the crowned candidate in the vault or slate.
        source = "crown"
        candidate = None
        for row in _read_jsonl(output_dir / "masterpiece_vault.jsonl"):
            if str(row.get("job_id")) == job_id:
                candidate = row
                break
        if candidate is None:
            candidate = next(
                (r for r in pending_slate(output_dir) if str(r.get("job_id")) == job_id),
                None,
            )
        if candidate is None:
            raise ValueError(f"no crowned/pending candidate {job_id!r} to submit")
        prompt = str(candidate.get("prompt") or "").strip()
        if seed_val is None and candidate.get("seed") is not None:
            seed_val = str(candidate.get("seed"))
    if not prompt:
        raise ValueError("submit needs either --brief or a --job-id with a recoverable prompt")

    payload = {
        "op": "submit", "backend": "cuda", "prompt": prompt,
        "steps": steps, "guidance": guidance, "width": width, "height": height,
    }
    if seed_val is not None and str(seed_val).strip():
        payload["seed"] = str(seed_val)

    submitted_id = None
    error = None
    try:
        import socket as _socket
        with _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM) as conn:
            conn.settimeout(10.0)
            conn.connect(socket_path)
            conn.sendall((json.dumps(payload) + "\n").encode())
            buf = b""
            while b"\n" not in buf:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                buf += chunk
        reply = json.loads(buf.decode().splitlines()[0]) if buf.strip() else {}
        submitted_id = (reply.get("job") or {}).get("id")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        error = str(exc)

    record = {
        "kind": "final_submission",
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source": source,
        "from_job_id": job_id,
        "prompt": prompt,
        "seed": seed_val,
        "submitted_job_id": submitted_id,
        "socket": socket_path,
        "ok": error is None and submitted_id is not None,
        "error": error,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "submission-ledger.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def compute_metrics(output_dir: Path) -> dict[str, Any]:
    """EGRL tractability metrics, derived from the append-only logs.

    Everything here is computed from taste-log.jsonl, eye-gate-candidates.jsonl,
    masterpiece_vault.jsonl and submission-ledger.jsonl — so the loop can be
    audited without the operator being in it.
    """
    taste = _read_jsonl(output_dir / "taste-log.jsonl")
    candidates = _read_jsonl(output_dir / "eye-gate-candidates.jsonl")
    vault = _read_jsonl(output_dir / "masterpiece_vault.jsonl")
    submissions = _read_jsonl(output_dir / "submission-ledger.jsonl")

    verdicts = [str(r.get("operator_verdict")) for r in taste]
    crowns = verdicts.count("crown")
    kills = verdicts.count("kill")
    notes = verdicts.count("note")
    decided = len(taste)

    # Critic–Operator agreement: a crown of a top-ranked candidate, or a kill of
    # a low-ranked one, is agreement. |delta| small => critic and operator agree.
    deltas = [abs(float(r.get("delta") or 0)) for r in taste]
    agree = sum(1 for d in deltas if d <= 1.0)
    agreement_rate = (agree / decided) if decided else None

    # Override yield: overrides that produced a promoted law or a named register.
    overrides = [r for r in taste if abs(float(r.get("delta") or 0)) >= 2.0]
    mined = sum(1 for r in overrides if r.get("promoted_law") or r.get("named_register"))
    override_yield = (mined / len(overrides)) if overrides else None

    # Blind submission rate: candidates that reached the gate without observation.
    observed_flags = [bool(r.get("observed")) for r in taste]
    blind = sum(1 for o in observed_flags if not o)
    blind_rate = (blind / len(observed_flags)) if observed_flags else None

    # Generation score trend: mean critic score of crowned pieces per generation.
    by_gen: dict[int, list[float]] = {}
    for r in taste:
        if r.get("operator_verdict") == "crown" and r.get("critic_score") is not None:
            by_gen.setdefault(int(r.get("generation") or 1), []).append(float(r["critic_score"]))
    gen_trend = {g: round(sum(v) / len(v), 2) for g, v in sorted(by_gen.items())}

    submit_ok = sum(1 for s in submissions if s.get("ok"))

    return {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "candidates_seen": len({str(c.get("job_id")) for c in candidates}),
        "decided": decided,
        "pending": len(pending_slate(output_dir)),
        "crowns": crowns,
        "kills": kills,
        "notes": notes,
        "crowned_in_vault": len(vault),
        "critic_operator_agreement_rate": round(agreement_rate, 3) if agreement_rate is not None else None,
        "override_count": len(overrides),
        "override_yield": round(override_yield, 3) if override_yield is not None else None,
        "blind_submission_rate": round(blind_rate, 3) if blind_rate is not None else None,
        "generation_score_trend": gen_trend,
        "final_submissions": len(submissions),
        "final_submissions_ok": submit_ok,
        "targets": {
            "critic_operator_agreement_rate": ">= 0.70 by generation 3",
            "override_yield": "> 0.50",
            "blind_submission_rate": "== 0.0",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default=_PP_OUT_DIR)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("slate", help="print every pending candidate in ranked order")
    record = sub.add_parser("record", help="persist one operator verdict verbatim")
    record.add_argument("--job-id", required=True)
    record.add_argument("--verdict", required=True, choices=("kill", "crown", "note"))
    record.add_argument("--words", required=True, help="operator words, stored verbatim")
    record.add_argument("--delta", required=True, type=float, help="signed operator-vs-critic placement delta")
    record.add_argument("--generation", type=int, default=1)

    submit = sub.add_parser("submit", help="final submission: push an approved brief back to the FLUX worker")
    submit.add_argument("--job-id", help="crowned/pending candidate whose prompt+seed to resubmit")
    submit.add_argument("--brief", help="explicit prompt to submit instead of a candidate's")
    submit.add_argument("--socket", default=os.path.join(_PP_ROOT, ".fluxd", "flux.sock"))
    submit.add_argument("--steps", type=int, default=18)
    submit.add_argument("--guidance", type=float, default=3.5)
    submit.add_argument("--width", type=int, default=512)
    submit.add_argument("--height", type=int, default=512)
    submit.add_argument("--seed", default=None)

    sub.add_parser("metrics", help="print EGRL tractability metrics computed from the logs")

    args = parser.parse_args()
    output_dir = Path(args.output_dir).expanduser().resolve()
    if args.command == "slate":
        print(json.dumps(pending_slate(output_dir), indent=2, default=str))
    elif args.command == "submit":
        print(json.dumps(submit_to_worker(
            output_dir, args.socket, args.job_id, args.brief,
            args.steps, args.guidance, args.width, args.height, args.seed,
        ), indent=2, default=str))
    elif args.command == "metrics":
        print(json.dumps(compute_metrics(output_dir), indent=2, default=str))
    else:
        print(json.dumps(record_verdict(
            output_dir, args.job_id, args.verdict, args.words,
            args.delta, args.generation,
        ), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
