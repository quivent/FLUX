"""The concept pool: the thing that actually evolves.

The eight cards were a hardcoded Python list, so every generation re-rendered
the same eight ideas at different pixel settings. Nothing about the *thinking*
moved. Here the concept is the unit: an idea with a lineage, competing for a
product slot, that can mutate, cross with another, branch into rivals, and die.

A concept:
    {slot, variant, character, role, subject, quote, accent, rgb, botanical,
     parent, born, status}

`slot` is the product it speaks for (sencha, saffron...). Several concepts may
live in one slot at once -- those are rivals, and the eye-gate decides.
"""
import copy
import json
import os
import pathlib
import time

POOL = pathlib.Path("/home/dev/concepts/population.json")

# Voice registers are separable from characters on purpose: crossing a register
# onto another character is one of the cheapest real concept mutations there is.
REGISTERS = {
    "laconic": "clipped, understated, says one true thing and stops",
    "boastful": "grand, theatrical, sells it like a champion",
    "conspiratorial": "leans in, lets you in on something",
    "weary": "has seen too much, means it anyway",
    "delighted": "unguarded enthusiasm, slightly too much of it",
    "imperious": "does not explain, expects you to keep up",
}

# Staging conceits: what the character is DOING. Swapping staging while keeping
# the character is the other cheap mutation, and it reads immediately.
STAGINGS = {
    "offering": "holding the product out toward the viewer, offering it",
    "mid-use": "caught in the middle of using it, unaware of the viewer",
    "guarding": "shielding the product possessively, half-turned away",
    "appraising": "holding it up to the light, judging it",
    "sharing": "pouring or passing it to someone just out of frame",
    "resting": "at ease afterwards, the work already done",
}


def _c(slot, character, role, subject, quote, accent, rgb, botanical,
       register="laconic", staging="offering", product=None, subtitle=None, family="tea"):
    return {
        "slot": slot,
        "variant": "a",
        "product": product,
        "subtitle": subtitle,
        "family": family,
        "character": character,
        "role": role,
        "subject": subject,
        "quote": quote,
        "accent": accent,
        "rgb": rgb,
        "botanical": botanical,
        "register": register,
        "staging": staging,
        "parent": None,
        "born": 0,
        "status": "alive",
    }


def founders():
    """Generation zero: the eight ideas the collection started from."""
    from collection import SPEC

    out = []
    for c in SPEC:
        out.append(
            {
                "slot": c["key"],
                "variant": "a",
                "product": c["product"],
                "subtitle": c["subtitle"],
                "family": c["family"],
                "character": c["character"],
                "role": c["role"],
                "subject": c["subject"],
                "quote": c["quote"],
                "accent": c["accent"],
                "rgb": c["rgb"],
                "botanical": c["botanical"],
                "register": "laconic",
                "staging": "offering",
                "parent": None,
                "born": 0,
                "status": "alive",
            }
        )
    return out


def load():
    if POOL.is_file():
        return json.loads(POOL.read_text())
    pop = {"generation": 0, "concepts": founders()}
    save(pop)
    return pop


def save(pop):
    POOL.parent.mkdir(parents=True, exist_ok=True)
    POOL.write_text(json.dumps(pop, indent=2))


def alive(pop):
    return [c for c in pop["concepts"] if c["status"] == "alive"]


def key_of(c):
    """Card key: the slot alone while it is unrivalled, slot-variant once it isn't."""
    return c["slot"] if c["variant"] == "a" else f"{c['slot']}-{c['variant']}"


def next_variant(pop, slot):
    used = {c["variant"] for c in pop["concepts"] if c["slot"] == slot}
    for ch in "abcdefghijklmnop":
        if ch not in used:
            return ch
    return f"v{len(used)}"


# ---------------------------------------------------------------- operators


def mutate(pop, concept, facet, value, born):
    """Change ONE facet, keep the lineage. The character survives its own edit."""
    child = copy.deepcopy(concept)
    child["variant"] = next_variant(pop, concept["slot"])
    child["parent"] = key_of(concept)
    child["born"] = born
    child["status"] = "alive"
    child[facet] = value
    if facet == "staging":
        child["subject"] = f"{concept['subject']}, {STAGINGS[value]}"
    return child


def cross(pop, a, b, born):
    """A's character wearing B's voice and staging. The cheapest real recombination."""
    child = copy.deepcopy(a)
    child["variant"] = next_variant(pop, a["slot"])
    child["parent"] = f"{key_of(a)}+{key_of(b)}"
    child["born"] = born
    child["status"] = "alive"
    child["register"] = b["register"]
    child["staging"] = b["staging"]
    child["subject"] = f"{a['subject']}, {STAGINGS[b['staging']]}"
    return child


def retire(pop, concept, why="culled"):
    concept["status"] = "retired"
    concept["retired_because"] = why
    concept["retired_at"] = time.time()


def as_spec(concepts):
    """Render the pool in the shape collection.py's card layout expects."""
    out = []
    for c in concepts:
        out.append(
            {
                "key": key_of(c),
                "product": c["product"],
                "subtitle": c["subtitle"],
                "family": c["family"],
                "accent": c["accent"],
                "rgb": tuple(c["rgb"]),
                "botanical": c["botanical"],
                "character": c["character"],
                "role": c["role"],
                "subject": c["subject"],
                "quote": c["quote"],
                "concept": {
                    "slot": c["slot"],
                    "variant": c["variant"],
                    "register": c["register"],
                    "staging": c["staging"],
                    "parent": c["parent"],
                    "born": c["born"],
                },
            }
        )
    return out
