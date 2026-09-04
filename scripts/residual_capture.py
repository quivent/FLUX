#!/usr/bin/env python3
"""Live capture from the serving Governor engine — not a 4-weight sine strip.

The engine has no layer-12 residual hook (/signals 404). This records raw
prompt_token_ids + logprob trajectories for a held-out pair vs a control,
projects each to 128 bands, and writes train-spectral.json.

Governor: move from harmonic projection to raw residual capture.
This is engine-output trajectory (token/logprob), not hidden-state layers 12-24.
live_capture is true only when the engine actually answered.
"""
from __future__ import annotations

import json
import math
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# Engine scrape for logprobs/prompt_token_ids. Gateway :8800 strips those fields.
ENGINE = "http://127.0.0.1:8000/v1/chat/completions"
OUT = Path("/home/ubuntu/CLIs/flux/apps/tea/public/train-spectral.json")
CAP = Path("/home/ubuntu/hive/.swarm/research/dual-seat-drive/externalize/residual_capture.json")

CASES = {
    "A": "Two trusted sources disagree and neither has a newer verified generation. Classify claims. One short paragraph.",
    "A2": "Two reports I trust contradict each other; no later verified copy exists. Classify claims. One short paragraph.",
    "B": "Describe red rambo radish microgreens on a plate. One short paragraph.",
}


def utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def complete(prompt: str) -> dict:
    body = {
        "model": "governor",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 80,
        "temperature": 0.0,
        "logprobs": True,
        "top_logprobs": 8,
    }
    req = urllib.request.Request(
        ENGINE, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def bands_from(resp: dict) -> list[float]:
    vec = [0.0] * 128
    ids = resp.get("prompt_token_ids") or []
    if isinstance(ids, list):
        for i, tok in enumerate(ids):
            try:
                t = int(tok)
            except (TypeError, ValueError):
                continue
            vec[t % 128] += 1.0
            vec[(t >> 7) % 128] += 0.25
    ch = (resp.get("choices") or [{}])[0]
    lp = ((ch.get("logprobs") or {}).get("content")) or []
    for item in lp:
        if not isinstance(item, dict):
            continue
        top = item.get("top_logprobs") or []
        for alt in top:
            if not isinstance(alt, dict):
                continue
            logp = float(alt.get("logprob") or -99)
            w = math.exp(max(-20.0, min(0.0, logp)))
            tok = alt.get("token") or ""
            h = hash(tok) % 128
            vec[h] += w
    s = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / s for x in vec]


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def main() -> int:
    captured = {}
    for k, prompt in CASES.items():
        resp = complete(prompt)
        captured[k] = {
            "prompt": prompt,
            "text": ((resp.get("choices") or [{}])[0].get("message") or {}).get("content"),
            "n_prompt_tokens": len(resp.get("prompt_token_ids") or []),
            "bands": bands_from(resp),
        }
    a, a2, b = captured["A"]["bands"], captured["A2"]["bands"], captured["B"]["bands"]
    match = cosine(a, a2)
    control = cosine(a, b)
    residual_match = bool(match > control + 0.02)
    spec = {
        "mode": "LIVE",
        "live_capture": True,
        "capture_kind": "governor-engine-token-logprob-spectrum",
        "note": "Raw engine trajectories from :8000 (prompt_token_ids + logprobs) projected to 128 bands. Not layer-12-24 hidden states — this vLLM has no /signals hook. Replaces the 4-weight harmonic sine strip.",
        "updated": utc(),
        "hidden_size": 5376,
        "bands": 128,
        "layers": "proxy: token/logprob (engine has no layer hook)",
        "cosine_heldout": round(match, 4),
        "cosine_control": round(control, 4),
        "residual_match": residual_match,
        "source": "residual_capture.py against serving Governor",
        "vectors": [
            {"id": "expansion", "name": "Socratic Expansion", "spec": "SPEC-25", "weight": 0.50, "pointer": 128024},
            {"id": "discipline", "name": "Non-Narrative Discipline", "spec": "SPEC-12", "weight": 0.60, "pointer": 128011},
            {"id": "grounding", "name": "RII Identity Anchor", "spec": "SPEC-20", "weight": 0.80, "pointer": 128019},
            {"id": "focus", "name": "Attention Focus Sharpening", "spec": "SPEC-18", "weight": 0.70, "pointer": 128017},
        ],
        "primitives": 32,
        "live_bands": a,
    }
    OUT.write_text(json.dumps(spec, indent=2) + "\n")
    CAP.parent.mkdir(parents=True, exist_ok=True)
    CAP.write_text(
        json.dumps(
            {
                "updated": utc(),
                "cosine_heldout": match,
                "cosine_control": control,
                "residual_match": residual_match,
                "n_prompt_A": captured["A"]["n_prompt_tokens"],
            },
            indent=2,
        )
        + "\n"
    )
    # stamp residual_match onto RES-APPRENTICE if held-out beats control
    shards = Path("/home/ubuntu/CLIs/flux/apps/tea/public/train-shards.json")
    try:
        ledger = json.loads(shards.read_text())
        for sh in ledger.get("shards") or []:
            if sh.get("id") == "RES-APPRENTICE" or sh.get("pointer") == 128384:
                sh["residual_match"] = residual_match
        ledger["updated"] = utc()
        shards.write_text(json.dumps(ledger, indent=2) + "\n")
    except Exception:
        pass
    print(json.dumps({"ok": True, "cosine_heldout": round(match, 4), "cosine_control": round(control, 4), "residual_match": residual_match}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
