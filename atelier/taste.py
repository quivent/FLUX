"""The Ghost of Taste: the fitness landscape the loop climbs when nobody is watching.

The Governor's architecture:

    "We stop treating the human as a gate and start treating the human as a
     fitness landscape. The system does not wait for a verdict to move; it moves
     based on the gradient of previous verdicts."

So: every image the human marks Keep becomes an ANCHOR. Fitness is closeness to
the anchor cluster, pulled toward the most recent Keep. Every Retire becomes a
REPULSOR, and anything sitting too close to a known failure is purged.

One honest substitution: the order named Qwen3-Embedding for this, but that is a
TEXT embedder -- it cannot embed a picture. Image fitness needs an image encoder,
so this uses the CLIP vision tower already resident on this node. Same role in
the architecture, correct tool for the modality.
"""
import json
import pathlib
import time

import torch

STORE = pathlib.Path("/home/dev/taste/anchors.json")
RUNS = pathlib.Path("/home/dev/runs")

# How much the newest Keep dominates the centroid. The Governor asked for
# fitness "weighted by the most recent Keep": recency is what lets the human
# steer with one verdict instead of needing to re-label the whole history.
RECENCY_WEIGHT = 0.4

# A candidate closer than this to a Retired image is purged outright.
REPULSION_RADIUS = 0.93

_model = _proc = None


def _clip():
    global _model, _proc
    if _model is None:
        from transformers import CLIPModel, CLIPProcessor

        _proc = CLIPProcessor.from_pretrained("openai/clip-vit-base-patch32")
        _model = (CLIPModel.from_pretrained("openai/clip-vit-base-patch32",
                                            dtype=torch.float16)
                  .to("cuda").eval())
    return _model, _proc


@torch.no_grad()
def embed(paths):
    """L2-normalised image embeddings, so a dot product is a cosine."""
    from PIL import Image

    model, proc = _clip()
    ims = [Image.open(p).convert("RGB") for p in paths]
    inputs = proc(images=ims, return_tensors="pt").to("cuda")
    out = model.get_image_features(**inputs)
    if not torch.is_tensor(out):
        out = getattr(out, "image_embeds", None)
        if out is None:
            v = model.vision_model(pixel_values=inputs["pixel_values"])
            out = model.visual_projection(v.pooler_output)
    out = out.float()
    return out / out.norm(dim=-1, keepdim=True)


def load():
    if STORE.is_file():
        try:
            return json.loads(STORE.read_text())
        except json.JSONDecodeError:
            pass
    return {"anchors": [], "repulsors": [], "updated": 0}


def save(store):
    STORE.parent.mkdir(parents=True, exist_ok=True)
    store["updated"] = time.time()
    STORE.write_text(json.dumps(store, indent=2))


def add_anchor(store, gen, key, path, provisional=False, note=""):
    """A Keep becomes an attractor. Provisional anchors are machine-seeded and
    are discarded the moment a real human Keep arrives."""
    vec = embed([path])[0].tolist()
    store["anchors"].append({
        "gen": gen, "key": key, "path": str(path), "vec": vec,
        "at": time.time(), "provisional": provisional, "note": note,
    })
    return store


def add_repulsor(store, gen, key, path, note=""):
    """A Retire becomes a repulsion zone: the Correction Wave."""
    vec = embed([path])[0].tolist()
    store["repulsors"].append({
        "gen": gen, "key": key, "path": str(path), "vec": vec,
        "at": time.time(), "note": note,
    })
    return store


def promote_real(store):
    """Once a genuine human Keep exists, provisional anchors stop counting."""
    if any(not a.get("provisional") for a in store["anchors"]):
        before = len(store["anchors"])
        store["anchors"] = [a for a in store["anchors"] if not a.get("provisional")]
        return before - len(store["anchors"])
    return 0


def landscape(store):
    """(centroid, latest_anchor_vec, repulsor_matrix) as tensors, or None if empty."""
    if not store["anchors"]:
        return None, None, None
    A = torch.tensor([a["vec"] for a in store["anchors"]], device="cuda")
    centroid = A.mean(0)
    centroid = centroid / centroid.norm()
    latest = torch.tensor(
        max(store["anchors"], key=lambda a: a["at"])["vec"], device="cuda")
    R = (torch.tensor([r["vec"] for r in store["repulsors"]], device="cuda")
         if store["repulsors"] else None)
    return centroid, latest, R


def score(store, paths):
    """Fitness for each path: closeness to the Gold Standard, pulled toward the
    most recent Keep, minus anything sitting on a known failure.

    Returns dicts with the components exposed, because a single number nobody can
    take apart is how a loop drifts without anyone noticing.
    """
    centroid, latest, R = landscape(store)
    E = embed(paths)
    out = []
    for i, p in enumerate(paths):
        e = E[i]
        if centroid is None:
            out.append({"path": str(p), "fitness": None, "cold_start": True})
            continue
        to_centroid = float(e @ centroid)
        to_latest = float(e @ latest)
        fit = (1 - RECENCY_WEIGHT) * to_centroid + RECENCY_WEIGHT * to_latest
        purged, worst = False, 0.0
        if R is not None and len(R):
            sims = (R @ e)
            worst = float(sims.max())
            purged = worst >= REPULSION_RADIUS
        out.append({
            "path": str(p), "fitness": round(fit, 4),
            "to_centroid": round(to_centroid, 4), "to_latest": round(to_latest, 4),
            "nearest_failure": round(worst, 4), "purged": purged,
            "cold_start": False,
        })
    return out


def human_verdicts(gen):
    """Verdicts a person wrote. The triage officer's rows are not taste."""
    f = RUNS / gen / "verdicts.json"
    if not f.is_file():
        return {}
    try:
        v = json.loads(f.read_text())
    except json.JSONDecodeError:
        return {}
    return {k: d for k, d in v.items()
            if d.get("critic", "") not in ("qwen2.5-vl triage", "smoke-test")}


def harvest(store):
    """Sweep every generation for human verdicts not yet folded into the landscape.

    This is the Correction Wave: verdicts arrive whenever the human happens to
    look, and steer everything produced in the meantime.
    """
    known = {(a["gen"], a["key"]) for a in store["anchors"]}
    known |= {(r["gen"], r["key"]) for r in store["repulsors"]}
    added = {"keep": 0, "retire": 0}
    for d in sorted(RUNS.glob("gen-*")):
        gen = d.name
        for key, v in human_verdicts(gen).items():
            if (gen, key) in known:
                continue
            path = d / "cards" / f"{key}.png"
            if not path.is_file():
                continue
            verdict = (v.get("verdict") or "").lower()
            sc = v.get("score")
            keep = verdict == "keep" or (sc is not None and sc >= 7.5)
            retire = verdict in ("retire", "reroll") or (sc is not None and sc < 5)
            if keep:
                add_anchor(store, gen, key, path, provisional=False,
                           note=v.get("note", ""))
                added["keep"] += 1
            elif retire:
                add_repulsor(store, gen, key, path, note=v.get("note", ""))
                added["retire"] += 1
    if added["keep"]:
        dropped = promote_real(store)
        if dropped:
            print(f"[taste] {dropped} provisional anchor(s) discarded — "
                  f"real taste has arrived", flush=True)
    return added
