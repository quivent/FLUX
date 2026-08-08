#!/home/dev/venv/bin/python
"""The watcher: notice when beauty changes, and say which change caused it.

Today the output got good and then regressed to wallpaper, and nobody noticed for
an hour. Not because the drop was subtle -- it was obvious the moment a human
looked -- but because nothing was looking. Resolution, step count, prompt set,
treatment mix and GPU contention all changed in that window, unmeasured, so even
after noticing there was no way to say which one did it.

So this measures continuously and, more importantly, ATTRIBUTES. Every card is
tagged with the config that produced it (the run label carries it), and scores are
tracked per config. The output is not "quality fell" -- which is useless -- but
"config X scores 0.09 below config Y on novelty over n=40". That is a sentence you
can act on.

On the signal, honestly: CLIP cannot see beauty. It can see three things that are
worth having anyway --

  novelty   distance from the centroid of "generic AI art" text probes. The
            governor's suggestion, and it directly measures the failure mode we
            actually hit: drift to the middle of the distribution.
  craft     zero-shot contrast between competence probes and failure probes.
            Catches gross breakage, not taste.
  vacancy   fraction of the frame that is low-detail. Not a quality measure at
            all, but the aurora-wallpaper regression was dense edge-to-edge
            decoration, and the good images had somewhere for the eye to rest.

None of these is beauty. They are a smoke alarm, not a critic. The operator's
keep/retire verdicts are the only ground truth, and where they exist this reports
how well each proxy CORRELATES with them -- so we find out whether the alarm is
worth listening to instead of assuming it.
"""
import json
import math
import pathlib
import sys
import time

sys.path.insert(0, "/home/dev")

HOME = pathlib.Path("/home/dev")
RUNS = HOME / "runs"
STATE = HOME / "watch_state.json"
LOG = HOME / "watch.jsonl"
SEEN = HOME / "watch_seen.json"

WINDOW = 40          # cards per rolling window
MIN_N = 12           # do not call a regression on a handful
DROP = 0.02          # novelty drop that counts as a regression

GENERIC = [
    "a generic AI generated digital painting",
    "a stock digital illustration, average quality",
    "trending on artstation, typical concept art",
    "a default anime wallpaper, glowing, oversaturated",
    "a symmetrical fantasy landscape with aurora and glowing flowers",
]
GOOD = [
    "a confident illustration with a clear point of view",
    "a photograph-like composition with deliberate framing and negative space",
    "an image with a specific idea in it",
]
BAD = [
    "a malformed drawing, broken anatomy, garbled shapes",
    "a muddy low quality render, smeared detail",
    "an over-processed image, deep fried, artifacts",
]

_model = _proc = None


def clip():
    global _model, _proc
    if _model is None:
        import torch
        from transformers import CLIPModel, CLIPProcessor
        _proc = CLIPProcessor.from_pretrained("openai/clip-vit-base-patch32")
        _model = (CLIPModel.from_pretrained("openai/clip-vit-base-patch32",
                                            dtype=torch.float16)
                  .to("cuda").eval())
    return _model, _proc


def _embed(paths, texts):
    """One forward pass: projected image and text embeddings in the shared space.

    transformers 5.x returns a ModelOutput from get_*_features; the full forward
    is the call that works on this node (triaged.py relies on it), and it hands
    back both embedding sets already projected and comparable.
    """
    import torch
    from PIL import Image
    m, p_ = clip()
    ims = []
    for q in paths:
        try:
            ims.append(Image.open(q).convert("RGB"))
        except Exception:
            ims.append(Image.new("RGB", (64, 64)))
    with torch.no_grad():
        inp = p_(text=texts, images=ims, return_tensors="pt",
                 padding=True, truncation=True).to("cuda")
        inp["pixel_values"] = inp["pixel_values"].half()
        out = m(**inp)
        ie = out.image_embeds.float()
        te = out.text_embeds.float()
        ie = ie / ie.norm(dim=-1, keepdim=True)
        te = te / te.norm(dim=-1, keepdim=True)
    return ie, te, ims


def vacancy(im):
    """Fraction of the frame that is flat. Not quality -- but the regression was
    dense edge-to-edge decoration with nowhere for the eye to rest."""
    import numpy as np
    a = np.asarray(im.convert("L").resize((96, 144)), dtype="float32")
    gx = np.abs(np.diff(a, axis=1)).mean(axis=1)
    gy = np.abs(np.diff(a, axis=0)).mean(axis=0)
    edge = (gx.mean() + gy.mean()) / 2
    flat = float((np.abs(np.diff(a, axis=1)) < 3).mean())
    return flat, float(edge)


def score(paths):
    """novelty = distance from the generic-AI-art centroid; craft = competence
    minus failure probes; flat = how much of the frame is quiet."""
    texts = GENERIC + GOOD + BAD
    ie, te, ims = _embed(paths, texts)
    ng, ngd, nb = len(GENERIC), len(GOOD), len(BAD)
    gen = te[:ng].mean(0, keepdim=True)
    gen = gen / gen.norm()
    good = te[ng:ng + ngd]
    bad = te[ng + ngd:]
    out = []
    for i, q in enumerate(paths):
        e = ie[i:i + 1]
        novelty = 1.0 - float((e @ gen.T).item())
        gs = float((e @ good.T).mean().item())
        bs = float((e @ bad.T).mean().item())
        craft = 1.0 / (1.0 + math.exp(-(gs - bs) * 40))
        flat, edge = vacancy(ims[i])
        out.append({"path": str(q), "novelty": round(novelty, 4),
                    "craft": round(craft, 4), "flat": round(flat, 4),
                    "edge": round(edge, 3)})
    return out


def config_of(label):
    """The run label carries the config that produced the card."""
    if not label:
        return "unknown"
    for tag in ("vision", "grid", "dream", "blast", "interp", "perpetual"):
        if label.startswith(tag):
            return tag
    return label.split()[0]


def scan(seen):
    """New cards since last pass, with their config and any human verdict."""
    fresh = []
    for d in sorted(RUNS.glob("gen-*")):
        rj = d / "run.json"
        if not rj.is_file():
            continue
        try:
            man = json.loads(rj.read_text())
        except Exception:
            continue
        label = man.get("label", "")
        vd = {}
        vf = d / "verdicts.json"
        if vf.is_file():
            try:
                vd = json.loads(vf.read_text())
            except Exception:
                vd = {}
        for c in man.get("cards", []) or []:
            k = c.get("key")
            if not k or f"{d.name}/{k}" in seen:
                continue
            p = d / "cards" / f"{k}.png"
            if not p.is_file():
                continue
            rec = vd.get(k) or {}
            fresh.append({"gen": d.name, "key": k, "path": p,
                          "config": config_of(label), "label": label,
                          "verdict": rec.get("verdict") if rec.get("critic")
                          not in ("qwen2.5-vl triage", "smoke-test") else None})
    return fresh


def main():
    interval = float(sys.argv[1]) if len(sys.argv) > 1 else 60.0
    seen = set(json.loads(SEEN.read_text())) if SEEN.is_file() else set()
    hist = {}
    print(f"[watch] watching every {interval:.0f}s; {len(seen)} cards already seen",
          flush=True)

    while True:
        fresh = scan(seen)[:120]
        if fresh:
            try:
                sc = score([f["path"] for f in fresh])
            except Exception as e:
                print(f"[watch] SCORING BROKEN — measuring nothing: {e!r}",
                      flush=True)
                pathlib.Path("/home/dev/watch_state.json").write_text(json.dumps(
                    {"at": time.time(), "broken": repr(e),
                     "note": "the watcher is not measuring; do not trust any "
                             "quiet period as evidence of stability"}, indent=2))
                sc = []
            for f, s in zip(fresh, sc):
                rec = {**{k: v for k, v in f.items() if k != "path"}, **s,
                       "at": time.time()}
                rec.pop("path", None)
                hist.setdefault(f["config"], []).append(rec)
                with LOG.open("a") as fh:
                    fh.write(json.dumps(rec) + "\n")
                seen.add(f"{f['gen']}/{f['key']}")
            SEEN.write_text(json.dumps(sorted(seen)[-6000:]))

        report, alarms = {}, []
        for cfg, rows in hist.items():
            r = rows[-WINDOW:]
            if not r:
                continue
            nov = sum(x["novelty"] for x in r) / len(r)
            cra = sum(x["craft"] for x in r) / len(r)
            fl = sum(x["flat"] for x in r) / len(r)
            base = rows[:WINDOW]
            bnov = sum(x["novelty"] for x in base) / len(base) if base else nov
            report[cfg] = {"n": len(rows), "novelty": round(nov, 4),
                           "craft": round(cra, 4), "flat": round(fl, 4),
                           "baseline_novelty": round(bnov, 4),
                           "delta": round(nov - bnov, 4)}
            if len(rows) >= MIN_N and nov < bnov - DROP:
                alarms.append(f"{cfg}: novelty {nov:.3f} vs baseline {bnov:.3f} "
                              f"({nov-bnov:+.3f}) over n={len(r)}")

        # does any proxy actually agree with the operator?
        agree = {}
        for cfg, rows in hist.items():
            keeps = [x for x in rows if x.get("verdict") == "keep"]
            rets = [x for x in rows if x.get("verdict") == "retire"]
            if len(keeps) >= 3 and len(rets) >= 3:
                for m in ("novelty", "craft", "flat"):
                    k = sum(x[m] for x in keeps) / len(keeps)
                    r = sum(x[m] for x in rets) / len(rets)
                    agree[f"{cfg}.{m}"] = round(k - r, 4)

        STATE.write_text(json.dumps({
            "at": time.time(), "configs": report, "alarms": alarms,
            "verdict_separation": agree,
            "note": "positive verdict_separation = the proxy agrees with the "
                    "operator; near zero = the proxy is not measuring what they see",
        }, indent=2))

        if alarms:
            for a in alarms:
                print(f"[watch] REGRESSION  {a}", flush=True)
        else:
            line = "  ".join(f"{c}:nov {v['novelty']:.3f}({v['delta']:+.3f}) n={v['n']}"
                             for c, v in sorted(report.items()))
            print(f"[watch] {line or 'no cards yet'}", flush=True)

        time.sleep(interval)


if __name__ == "__main__":
    main()
