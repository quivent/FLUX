#!/usr/bin/env python3
"""Named artistic eras for Chorus.

An era is a proposition with a bounded visual vocabulary.  It steers the
existing generator; it does not replace Drift, Hive, Sentinel, or their
evidence.  The JSON is deliberately data, so a new movement can begin without
editing the resident renderer.
"""
import json
import pathlib


REQUIRED_LISTS = ("subjects", "details", "surfaces", "moods", "title_motifs")


def load(reference, repo_root=None):
    if not reference:
        return None
    path = pathlib.Path(str(reference)).expanduser()
    if not path.is_absolute():
        root = pathlib.Path(repo_root or pathlib.Path(__file__).resolve().parent.parent)
        path = root / "chorus" / "eras" / str(reference)
        if path.suffix != ".json":
            path = path.with_suffix(".json")
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise ValueError(f"era must be an object: {path}")
    for key in ("id", "title", "collection", "proposition"):
        if not str(payload.get(key) or "").strip():
            raise ValueError(f"era is missing {key}: {path}")
    for key in REQUIRED_LISTS:
        if not isinstance(payload.get(key), list) or not payload[key]:
            raise ValueError(f"era is missing non-empty {key}: {path}")
    for surface in payload["surfaces"]:
        if not isinstance(surface, dict) or not {
            "medium", "medium_clause", "family", "tonal"
        }.issubset(surface):
            raise ValueError(f"invalid era surface: {surface!r}")
    payload["_path"] = str(path)
    return payload


def new_style(rng, spec):
    surface = dict(rng.choice(spec["surfaces"]))
    surface["mood"] = rng.choice(spec["moods"])
    return surface


def apply_sequence(rng, sequence, spec):
    sequence = dict(sequence)
    sequence["world"] = "era:" + spec["id"]
    sequence["subject"] = rng.choice(spec["subjects"])
    sequence["era_id"] = spec["id"]
    sequence["collection"] = spec["collection"]
    return sequence


def apply_variation(variation, spec, index):
    variation = dict(variation)
    variation["detail"] = spec["details"][index % len(spec["details"])]
    variation["era_id"] = spec["id"]
    variation["collection"] = spec["collection"]
    return variation


def title_motifs(spec):
    return tuple(str(value) for value in spec.get("title_motifs") or ())
