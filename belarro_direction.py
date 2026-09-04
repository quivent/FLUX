"""Belarro art direction for the microgreens studio.

Variety and hue sit in the first clause so CLIP-L 77 keeps them. Life clauses
break catalog sameness. Config is `.fluxd/study_microgreens.json`.
"""
from __future__ import annotations

import json
import os

ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(ROOT, ".fluxd", "study_microgreens.json")

VARIETIES = (
    ("red-rambo", "Red Rambo radish microgreens", "deep violet plum amethyst leaves, ruby-magenta stems"),
    ("pea", "pea shoot microgreens", "vibrant green leaves, cream stems"),
    ("sunflower", "sunflower microgreens", "vivid green on thick white stems"),
    ("broccoli", "broccoli microgreens", "delicate meadow-green feather foliage"),
    ("nasturtium", "nasturtium microgreens", "deep lilypad-green round peppery leaves"),
    ("amaranth", "amaranth microgreens", "vivid magenta-red fine delicate leaves"),
)

SHOTS = (
    ("macro", "100mm macro, dew, slate ground, shallow focus"),
    ("bunch", "top-down harvest bunch on dark ceramic plate"),
    ("crudo", "cluster garnishing sea bass crudo on white porcelain"),
    ("steak", "pinch on sliced filet, charcoal stoneware, jus"),
    ("catalog", "isolated on white seamless, catalog lighting"),
)

LIFE = (
    "irregular heights, one cotyledon folded",
    "soil crumbs, harvest-wet stems, unarranged",
    "dew in motion, overlapping leaves",
    "one yellowing edge, living not catalog-perfect",
    "messy tray lip, morning window flare",
    "asymmetric cluster, beads of water",
    "roots still dusty, stems leaning",
    "crowded tray, one shoot taller than the rest",
)

DEFAULTS = {
    "varieties": [v[0] for v in VARIETIES],
    "shots": [s[0] for s in SHOTS],
    "life": 80,
    "guidance": 4.0,
    "steps": 28,
    "n": 256,
    "depth": 2,
    "seed": "random",
    "judge": True,
}


def load_config():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH) as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            cfg.update(data)
    except Exception:
        pass
    vars_ = [v for v in VARIETIES if v[0] in set(cfg.get("varieties") or DEFAULTS["varieties"])]
    shots = [s for s in SHOTS if s[0] in set(cfg.get("shots") or DEFAULTS["shots"])]
    if not vars_:
        vars_ = list(VARIETIES)
    if not shots:
        shots = list(SHOTS)
    cfg["_varieties"] = vars_
    cfg["_shots"] = shots
    try:
        cfg["life"] = max(0, min(100, int(cfg.get("life") or 0)))
    except (TypeError, ValueError):
        cfg["life"] = 80
    try:
        cfg["guidance"] = max(1.5, min(6.0, float(cfg.get("guidance") or 4.0)))
    except (TypeError, ValueError):
        cfg["guidance"] = 4.0
    return cfg


def save_config(data):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    cfg = dict(DEFAULTS)
    if isinstance(data, dict):
        for key in DEFAULTS:
            if key in data:
                cfg[key] = data[key]
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w") as handle:
        json.dump(cfg, handle, indent=2)
        handle.write("\n")
    os.replace(tmp, CONFIG_PATH)
    return load_config()


def prompt_for(i: int, cfg=None) -> str:
    cfg = cfg or load_config()
    vars_ = cfg.get("_varieties") or VARIETIES
    shots = cfg.get("_shots") or SHOTS
    name = vars_[i % len(vars_)]
    shot = shots[i % len(shots)]
    parts = [name[1], name[2], "soil-grown, no people"]
    if int(cfg.get("life") or 0) >= 40:
        parts.append(LIFE[i % len(LIFE)])
    parts.append(shot[1])
    parts.append("photoreal culinary still")
    return ", ".join(parts)


_BOOT = dict(DEFAULTS)
_BOOT["_varieties"] = VARIETIES
_BOOT["_shots"] = SHOTS
DEFAULT_PROMPT = prompt_for(0, _BOOT)
