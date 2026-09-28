"""The Beauty jury at work on the wall: judges the newest finished FLUX render with the real jury (moj_evaluator).

What it judges is set on beauty.influx.vision/control ("Judging"), stored in ~/FLUX/.fluxd/judging.json:
  wall  every new render on the wall, newest first (a render finished while the jury was busy is skipped, not queued)
  off   nothing
When a collection is active (/control), each verdict on a render of its prompt files the render into
~/Models/flux-output/collections/<name>/ and moves the prompt one small step (Qwen reads the critiques and the teacher's directions).
Weights come from the jury config the control page writes; a seat at 0 is not called at all (moj_evaluator.judge_enabled).
Event-driven: inotify on the FLUX jobs ledger. State for the jury page (live feed) goes where the Beauty server reads it:
  ~/Models/flux-output/collections/relative-beauty/jury-runtime.json
Run with the FLUX venv from ~/FLUX (it imports jury_evaluator).
"""
import json, os, subprocess, sys, threading, time
from pathlib import Path

H = Path("/home/ubuntu")
FLUX = H / "FLUX"
LEDGER = FLUX / ".fluxd" / "jobs.jsonl"
MODE = FLUX / ".fluxd" / "judging.json"
STATE = H / "Models" / "flux-output" / "collections" / "relative-beauty" / "jury-runtime.json"
sys.path.insert(0, str(FLUX))
os.chdir(FLUX)
import jury_evaluator
import beauty_vision  # noqa: E402

COLL = FLUX / ".fluxd" / "collection.json"      # the active collection (set on /control): one subject, one evolving prompt
DIRECTIONS = FLUX / ".fluxd" / "prompt.json"     # the teacher's directions, folded into every evolution step
COLLECTIONS = H / "Models" / "flux-output" / "collections"
WITNESS = "http://127.0.0.1:8001/v1/chat/completions"
WORSE_BY = 3.0                                   # a score this far under the best sends the next step back to the best prompt

try:
    import beauty_diff  # noqa: F401  -- champion/challenger scoring of the diff (beauty.influx.vision/scoring)
    DIFF = True
except Exception:
    DIFF = False        # without it, collections keep the absolute-score evolution below

state = {"status": "running", "station": "starting", "seen": []}


def read_json(p, default):
    try:
        return json.loads(Path(p).read_text())
    except Exception:
        return default


def write_json(p, obj):
    p = Path(p); p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp"); tmp.write_text(json.dumps(obj, indent=1)); os.replace(tmp, p)


def champion_uri(coll):
    """The champion frame as a data URI (the PNG as rendered), so the edit step can see what it is improving."""
    import base64
    ch = coll.get("champion") or {}
    p = COLLECTIONS / coll.get("name", "") / ch.get("file", "")
    if not ch.get("file") or not p.exists():
        return None
    return "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode()


WRITER = "http://127.0.0.1:8005/v1/chat/completions"     # Gemma 4 12B writes the edits; Qwen only judges


def ask_writer(messages, max_tokens=300):
    import urllib.request
    body = {"model": "governor", "temperature": 0.4, "max_tokens": max_tokens, "messages": messages}
    req = urllib.request.Request(WRITER, json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)["choices"][0]["message"]["content"]


def ask_witness(messages, max_tokens=400):
    import urllib.request
    body = {"model": "visual-witness", "temperature": 0.3, "max_tokens": max_tokens,
            "chat_template_kwargs": {"enable_thinking": False}, "messages": messages}
    req = urllib.request.Request(WITNESS, json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)["choices"][0]["message"]["content"]


ACCEPT_OTHERS_MIN = -1      # an accepted step may cost another aspect at most "slightly"
DRIFT = 0.02                # subject-hold drop (SigLIP cosine) that counts as leaving the subject
NO_TAKE = 0.015             # DINOv2 distance to the champion under which the edit "didn't take" (edits measured 0.02-0.13)
SAME_FRAME = 0.003          # under this the challenger is the champion's own frame: not a step
AIM_MIN = 0.5               # the aimed-at aspect must improve by at least half a step


def jury_weights():
    try:
        cfg = jury_evaluator.load_active_config() or {}
        w = cfg.get("weights") or {}
        return {"pixtral": float(w.get("pixtral", 0.35)), "qwen": float(w.get("qwen", 0.35)) + float(w.get("decoder", 0) or 0)}
    except Exception:
        return {"pixtral": 0.35, "qwen": 0.5}


CRAFT_WORDS = 40
LAMBDA = 1                  # strictly linear: one image, judged, then the next (no batches)
GAIN = 0.5                  # a candidate must be at least this much better (net) to replace the champion
ORIGIN_EVERY = 10           # every N cycles the champion is judged against the collection's first frame
BIAS_WINDOW = 20            # frames in the running position-bias estimate
BIAS_MIN_EACH = 2           # frames of each order before any candidate may be accepted


STUDY_CFG = FLUX / ".fluxd" / "study_config.json"     # the teacher's settings for the study loop (Collection page)
STUDY_DEFAULTS = {"weights": {"beauty": 3.0, "original": 2.0, "change": 2.0, "anatomy and hands": 1.0, "defects": 1.0},
                  "gain": 0.5, "min_change": 0.12, "bold_after": 2, "revision_after": 8, "reseed_after": 12, "judge_calls": 2, "size": 768, "steps": 18,
                  "vision": "", "writer": "on"}
STEPS = 18


def study_config():
    """Read the teacher's settings and apply them to the loop (weights, thresholds, calls, size, steps, the vision, the writer)."""
    global GAIN, STALL_RESEED, STEPS
    cfg = json.loads(json.dumps(STUDY_DEFAULTS))
    got = read_json(STUDY_CFG, {})
    for k, v in got.items():
        if k == "weights" and isinstance(v, dict):
            cfg["weights"].update({a: float(x) for a, x in v.items() if a in cfg["weights"]})
        elif k in cfg:
            cfg[k] = v
    import beauty_diff
    beauty_diff.WEIGHTS = dict(cfg["weights"])
    GAIN, STALL_RESEED, STEPS = float(cfg["gain"]), int(cfg["reseed_after"]), int(cfg["steps"])
    beauty_vision.BOLD_AFTER, beauty_vision.STALL_REVISION = int(cfg["bold_after"]), int(cfg["revision_after"])
    return cfg


def anchors(folder):
    """The teacher's anchors: every frame of this study marked + on the collection page, in order."""
    marks = read_json(folder / "marks.json", {})
    gone = set(read_json(folder / "removed.json", []))    # a removed photo is never an anchor
    marks = {k: v for k, v in marks.items() if int(k) not in gone}
    out = []
    try:
        for line in (folder / "history.jsonl").read_text().splitlines():
            e = json.loads(line)
            if e.get("file") and int(marks.get(str(e.get("cycle")), 0)) > 0 and (folder / e["file"]).exists():
                out.append({"cycle": e["cycle"], "file": e["file"], "craft": e.get("craft", ""), "guidance": e.get("guidance", 3.2),
                            "score": min(20, int(marks[str(e["cycle"])]))})
    except Exception:
        pass
    return out


def disliked(folder):
    """Frames the teacher scored below zero: their craft, most disliked first."""
    marks = read_json(folder / "marks.json", {})
    out = []
    try:
        for line in (folder / "history.jsonl").read_text().splitlines():
            e = json.loads(line)
            if e.get("file") and int(marks.get(str(e.get("cycle")), 0)) < 0:
                out.append((int(marks[str(e["cycle"])]), e.get("craft", "")))
    except Exception:
        pass
    return [c for _, c in sorted(out)]


def teacher_steps(folder):
    """The teacher's Difference and Uniqueness scores, joined to each scored frame's measured step and its change.
    Returns the step size the teacher rewards (score-weighted mean DINOv2 distance of steps scored up on Difference;
    None until one is) and the latest scored steps, newest last, for the writer."""
    sc = read_json(folder / "scores.json", {})
    rows = []
    try:
        for line in (folder / "history.jsonl").read_text().splitlines():
            e = json.loads(line)
            s = sc.get(str(e.get("cycle")))
            if e.get("file") and s:
                rows.append({"cycle": e["cycle"], "d": (e.get("measured") or {}).get("d_champ"), "change": e.get("change") or "",
                             "difference": int(s.get("difference", 0)), "uniqueness": int(s.get("uniqueness", 0)), "direction": int(s.get("direction", 0))})
    except Exception:
        pass
    pos = [(r["d"], r["difference"]) for r in rows if r["difference"] > 0 and r["d"] is not None]
    target = sum(d * w for d, w in pos) / sum(w for _, w in pos) if pos else None
    too = {"small": sum(1 for r in rows if r["difference"] < 0 and r["d"] is not None and target and r["d"] < target),
           "big": sum(1 for r in rows if r["difference"] < 0 and r["d"] is not None and target and r["d"] > target)}
    return {"target": target, "wrong": too, "recent": rows[-6:]}


LINE_PATIENCE = 4           # frames without a gain before the loop moves to another anchor's line


def pick_anchor(coll, anc):
    """Smooth weighted round-robin: over any run of frames each anchor is built from in proportion to the teacher's score
    (a 5 five times as often as a 1), the picks are spread evenly, and a score change takes effect on the next frame."""
    cw = {str(k): v for k, v in (coll.get("anchor_cw") or {}).items()}
    live = {str(a["cycle"]): a for a in anc}
    cw = {k: v for k, v in cw.items() if k in live}
    total = sum(a["score"] for a in anc)
    for k, a in live.items():
        cw[k] = cw.get(k, 0) + a["score"]
    best = max(live, key=lambda k: (cw[k], live[k]["score"]))
    cw[best] -= total
    coll["anchor_cw"] = cw
    return live[best]


def teacher_vision(coll, cfg):
    """A vision the teacher wrote replaces the director's, and is never re-imagined by the loop."""
    t = str(((cfg.get("vision_by_study") or {}).get(coll.get("name")) or cfg.get("vision") or "")).strip()
    if t:
        coll["vision"] = {"text": t, "idea": t[:80], "by": "teacher", "at_cycle": int(coll.get("cycle", 0))}
    elif (coll.get("vision") or {}).get("by") == "teacher":
        coll.pop("vision", None)
    return bool(t)


def direction_text():
    return "; ".join(d["text"] for d in (read_json(DIRECTIONS, {}).get("directions") or []))


def compose(subject, craft):
    """The rendered prompt: the fixed subject, the evolving craft phrase, then the teacher's directions verbatim."""
    dirs = [d["text"] for d in (read_json(DIRECTIONS, {}).get("directions") or [])]
    # the reference form: "<subject>. <direction>; <direction>; ...", then the craft (if any); the preset's words follow from --preset
    # the craft goes next to the subject, inside CLIP's 77-token window (subject + directions alone fill it, and a craft
    # phrase after them reached only T5: rewordings didn't move the image); the directions follow, verbatim
    return subject + (", " + craft if craft else "") + (". " + "; ".join(dirs) if dirs else "")


def propose_batch(coll, base, context, n=LAMBDA):
    """n different craft edits, each aimed at a different aspect. The subject and the directions are never the model's to edit."""
    import beauty_diff
    aspects = [a for seat in beauty_diff.JURORS.values() for a in seat[2]]
    mem = coll.get("memory") or []
    won = sorted([x for x in mem if x.get("kept")], key=lambda x: -x["net"])[:5]
    lost = sorted([x for x in mem if not x.get("kept") and x["net"] <= -5], key=lambda x: x["net"])[:5]
    last = mem[-1] if mem else None
    step = ""
    if last and last["net"] <= -10:
        step = "The last edit badly hurt the image (net %+.1f). Make a SMALL edit this time: one phrase.\n" % last["net"]
    elif last and abs(last["net"]) < 1 and last.get("dist", 1) < 0.02:
        step = "The last edit barely changed the image. Make a BOLDER edit: swap a whole element.\n"
    memo = ""
    if won:
        memo += "Edits that WON (build on these kinds):\n" + "\n".join("- %s (%s): %+.1f" % (x["change"], x["aim"], x["net"]) for x in won) + "\n"
    if lost:
        memo += "Edits that LOST badly (don't repeat these kinds):\n" + "\n".join("- %s (%s): %+.1f" % (x["change"], x["aim"], x["net"]) for x in lost) + "\n"
    weak = ""
    prog = (coll.get("progress") or {}).get("aspects") or {}
    if prog:
        weak = "Aspects that have gained least so far: " + ", ".join(sorted(aspects, key=lambda a: prog.get(a, 0))[:4]) + "\n"
    raw = ask_witness([
        {"role": "system", "content": "You evolve the craft phrase of a FLUX image prompt for a collection with a fixed subject. The craft phrase covers "
         "lighting, color, composition and pose, garment cut and construction, fabric, finish, camera. Propose %d DIFFERENT edits, each aimed at a "
         "DIFFERENT aspect, each a clearly visible change (swap an element, not a word), each at most %d words. Reply as JSON only: "
         '{"edits": [{"aim": "<aspect>", "craft": "...", "guidance": number 2.5-5.0, "change": "what you changed, in a few words"}]}' % (n, CRAFT_WORDS)},
        {"role": "user", "content": ([{"type": "text", "text": "The current champion image:"}, {"type": "image_url", "image_url": {"url": champion_uri(coll)}}]
                                     if champion_uri(coll) else []) + [{"type": "text", "text":
         "Look at the image first: find the ONE visible flaw or weakness that most holds it back, and aim the edit at it.\n" + step + memo +
         "Subject (fixed, not yours to edit): %s\nChampion craft phrase: %s\nGuidance: %s\nAspects: %s\n%s"
         "What the last cycle showed (candidate vs champion, -3..+3 per aspect):\n%s"
         % (coll["subject"], base or "(empty: write first ones)", coll.get("guidance", 3.5), ", ".join(aspects), weak, context or "- (first cycle)")}]}],
        max_tokens=900)
    d = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
    out, aims = [], set()
    for e in d.get("edits") or []:
        craft = str(e.get("craft") or "").strip().strip(".")
        aim = str(e.get("aim") or "")
        if not craft or craft == (base or "") or len(craft.split()) > CRAFT_WORDS or any(craft == o["craft"] for o in out):
            continue
        try:
            g = round(min(5.0, max(2.5, float(e.get("guidance", coll.get("guidance", 3.5))))), 2)
        except Exception:
            g = coll.get("guidance", 3.5)
        out.append({"craft": craft, "aim": aim if aim in aspects else None, "change": str(e.get("change") or ""), "guidance": g})
        aims.add(aim)
    return out[:n]


def render(prompt, guidance, seed, size, preset):
    """Submit one frame; returns its job id."""
    import re
    out = subprocess.run(["./flux", "render", prompt, "--preset", preset, "--guidance", str(float(guidance)), "--seed", str(int(seed)),
                          "--width", str(int(size)), "--height", str(int(size)), "--steps", str(int(STEPS)), "--async"],
                         cwd=str(FLUX), env={**os.environ, "FLUX_HOME": str(FLUX)}, capture_output=True, text=True, timeout=60)
    m = re.search(r"JOB\S*\s+(\S+)", re.sub(r"\x1b\[[0-9;]*m", "", out.stdout))
    return m.group(1) if m else None


EXPLORE_EVERY = 6


def seed_of(rel):
    import re
    m = re.search(r"seed-(\d+)", str(rel or ""))
    return int(m.group(1)) if m else None


def submit(coll):
    """Render the cycle: the first frame alone, then each cycle's candidates, all with the champion's seed."""
    import random
    champ = coll.get("champion")
    if coll.get("mode", "chain") == "chain" and (coll.get("best") or coll.get("previous")):
        # incremental: the base frame's own seed, so the one edit is the only difference (and the judge can see its effect);
        # every EXPLORE_EVERY-th frame explores with a new seed on the best, for uniqueness
        if coll.get("base_seed") and int(coll.get("cycle", 0)) % EXPLORE_EVERY != 0:
            seed = int(coll["base_seed"])
        else:
            seed = random.randrange(1, 2**31 - 1)
    else:
        seed = int((champ or {}).get("seed") or coll.get("seed") or random.randrange(1, 2**31 - 1))
    coll["seed"] = seed
    if coll.get("mode", "chain") == "chain":
        batch = coll.get("batch") or [{"craft": coll.get("craft", ""), "aim": None, "change": "first frame", "guidance": coll.get("guidance", 3.5)}]
    elif not champ:
        batch = [{"craft": coll.get("craft", ""), "aim": None, "change": "first frame", "guidance": coll.get("guidance", 3.5)}]
    else:
        batch = coll.get("batch") or []
    seen_renders = set(coll.get("rendered") or [])
    for c in batch:
        c["prompt"] = compose(coll["subject"], c["craft"])
        if "%s|%s" % (seed, c["prompt"]) in seen_renders:     # never render the same words with the same seed twice: a repeat is impossible
            seed = random.randrange(1, 2**31 - 1)
            coll["seed"] = seed
        coll["rendered"] = ((coll.get("rendered") or []) + ["%s|%s" % (seed, c["prompt"])])[-400:]
        c["job"] = render(c["prompt"], c["guidance"], seed, coll.get("size", 512), coll.get("preset", "hero"))
    coll["batch"], coll["pending"] = batch, [c["job"] for c in batch if c.get("job")]
    coll["pending_at"] = time.time()
    write_json(COLL, coll)
    save(station="rendering %d" % len(coll["pending"]), job_id=",".join(coll["pending"]))


def kick():
    """Start the loop when a collection is active and nothing is in flight."""
    coll = read_json(COLL, {})
    if coll.get("active") and DIFF and not busy.is_set():
        stale = coll.get("pending") and time.time() - float(coll.get("pending_at") or 0) > 600
        if not coll.get("pending") or stale:
            if coll.get("mode", "chain") == "chain":
                cfg = study_config(); teacher_vision(coll, cfg); coll["size"] = int(cfg["size"])
                if not coll.get("vision") and not coll.get("previous") and not coll.get("best"):
                    try:
                        nv = beauty_vision.make_vision(coll, direction_text())
                        coll["batch"], coll["craft"], coll["guidance"] = [nv], "", nv["guidance"]
                    except Exception as ex:
                        coll["last_change"] = "vision failed: %s" % repr(ex)[:120]
                if coll.get("previous") and not coll.get("batch"):
                    nxt = propose_next(coll, coll.get("craft", ""), None, "")
                    coll["batch"] = [nxt] if nxt else None
            elif coll.get("champion") and not coll.get("batch"):
                coll["batch"] = propose_batch(coll, coll["champion"].get("craft", ""), "")
            submit(coll)


def combine(by_seat, weights):
    profile = {}
    for asp in {a for p in by_seat.values() for a in p}:
        ops = [(seat, p[asp]) for seat, p in by_seat.items() if asp in p]
        nz = [(seat, v) for seat, v in ops if v["d"] != 0]
        if not nz or len({v["d"] > 0 for _, v in nz}) > 1:
            d = 0.0
        else:
            w = sum(weights.get(seat, 0) for seat, _ in nz)
            d = sum(weights.get(seat, 0) * v["d"] for seat, v in nz) / w
        lead = max(ops, key=lambda sv: abs(sv[1]["d"]))
        profile[asp] = {"d": round(d, 2), "why": lead[1]["why"], "seat": lead[0], "by_seat": {seat: v["raw"] for seat, v in ops}}
    return profile


def compare_debiased(coll, champ_path, chall_path):
    """k judging calls per frame (coll["judge_calls"], default 1), one after another, alternating the order
    (+1: champion A, challenger B; -1: the reverse). Every call feeds a running position-bias estimate (the mean raw
    score over recent calls, where real effects cancel across the two orders); a frame's score is the mean over its
    calls of order * (raw - bias). Also records how much the calls disagreed."""
    import beauty_diff
    dirs = [d["text"] for d in (read_json(DIRECTIONS, {}).get("directions") or [])]
    start = COLLECTIONS / coll.get("name", "") / str(coll.get("anchor_file") or "")
    m = beauty_diff.measure(str(champ_path), str(chall_path), str(start) if coll.get("anchor_file") and start.exists() else None, coll["subject"], dirs)
    try:                                                   # its own file, read fresh each frame: the loop rewrites collection.json
        k = max(1, int(study_config()["judge_calls"]))
    except Exception:
        k = max(1, int(coll.get("judge_calls", 1)))
    beauty_diff.DIRECTION = "; ".join(d["text"] for d in (read_json(DIRECTIONS, {}).get("directions") or []))
    beauty_diff.VISION = (coll.get("vision") or {}).get("text", "")
    order = int(coll.get("next_order", 1))
    hist = coll.setdefault("bias_hist", [])
    calls, broken, why = [], False, ""
    for n in range(k):
        o = order if n % 2 == 0 else -order
        first, second = (champ_path, chall_path) if o == 1 else (chall_path, champ_path)
        raw, br = beauty_diff.judge("qwen", str(first), str(second), coll["subject"])
        broken = broken or (br and o == 1)
        if o == 1 and not why:                             # the note is only read where B is the new frame
            why = next(iter(raw.values()))["why"]
        r = {a: v["d"] for a, v in raw.items()}
        calls.append((o, r))
        hist.append({"order": o, "raw": r})
    del hist[:-BIAS_WINDOW * max(1, k)]
    counts = {o: sum(1 for h in hist if h["order"] == o) for o in (1, -1)}
    aspects = list(calls[0][1])
    bias = {a: sum(h["raw"].get(a, 0) for h in hist) / len(hist) for a in aspects}
    per_call = [{a: o * (r[a] - bias[a]) for a in aspects} for o, r in calls]
    def agreed(a):
        # the two orders must agree on which frame is better; a judge that marks whichever image is second as worse
        # contradicts itself across orders, and the mean of a contradiction is not a gain (c7 of emerald-vision)
        vals = [c[a] for c in per_call]
        if k > 1 and any(x > 0 for x in vals) and any(x < 0 for x in vals):
            return 0.0
        return sum(vals) / k
    profile = {a: {"d": round(agreed(a), 2), "why": why, "seat": "qwen",
                   "calls": [[o, r[a]] for o, r in calls], "bias": round(bias[a], 2)} for a in aspects}
    if "change" in profile:                                # change is symmetric (the same whichever frame is A): measure it, don't debias it
        try:
            mc = float(read_json(STUDY_CFG, {}).get("min_change", 0.12))
        except Exception:
            mc = 0.12
        profile["change"]["d"] = round(max(0.0, min(3.0, (m["d_champ"] - mc) / 0.1)), 2)
        profile["change"]["why"] = "measured: DINOv2 distance %.3f to the frame it was built from" % m["d_champ"]
    nets = [sum(c.values()) for c in per_call]
    spread = round((max(nets) - min(nets)), 2) if k > 1 else None
    coll["next_order"] = -order if k % 2 == 1 else order    # keeps the two orders balanced over time
    coll["bias"] = {a: round(b, 2) for a, b in bias.items()}
    ready = min(counts.values()) >= BIAS_MIN_EACH
    net = round(sum(beauty_diff.WEIGHTS.get(a, 1.0) * v["d"] for a, v in profile.items() if a != "change"), 2)   # change is a gate, not a gain   # the teacher's direction weighs 3x
    m["judge_calls"], m["call_nets"], m["call_spread"] = k, [round(x, 2) for x in nets], spread
    return m, profile, broken, net, ready


def compare(coll, a_path, b_path):
    """Profile of b against a from every juror with influence, plus the measured diff."""
    import beauty_diff
    dirs = [d["text"] for d in (read_json(DIRECTIONS, {}).get("directions") or [])]
    m = beauty_diff.measure(str(a_path), str(b_path), None, coll["subject"], dirs)
    weights, by_seat, broken = jury_weights(), {}, False
    for seat in beauty_diff.JURORS:                       # one juror at a time
        if weights.get(seat, 0) <= 0:
            continue
        prof, br = beauty_diff.judge(seat, str(a_path), str(b_path), coll["subject"])
        by_seat[seat], broken = prof, broken or br
    profile = combine(by_seat, weights)
    return m, profile, broken, round(sum(v["d"] for v in profile.values()), 2)


def frame_uri(coll, rel):
    import base64
    p = COLLECTIONS / coll.get("name", "") / (rel or "")
    return "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode() if rel and p.exists() else None


def propose_next(coll, craft, profile, note):
    """The next edit in the chain. It sees only the most recent image and how that image changed from the one before."""
    import beauty_diff
    aspects = [a for seat in beauty_diff.JURORS.values() for a in seat[2]]
    change = "\n".join("- %s: %+.1f" % (a, v["d"]) for a, v in (profile or {}).items() if v["d"]) or "- no visible change on any aspect"
    uri = frame_uri(coll, coll.get("previous"))
    content = ([{"type": "text", "text": "The most recent image:"}, {"type": "image_url", "image_url": {"url": uri}}] if uri else []) + [{"type": "text", "text":
        "Subject (fixed, not yours to edit): %s\nIts craft phrase: %s\nGuidance: %s\n"
        "How it changed from the image before it (+ better, - worse, -3..+3): %s\nWhat changed, in a few words: %s\n"
        "Aspects: %s\nThe teacher's direction comes first: make ONE edit to the craft phrase that carries the teacher's direction further in the next "
        "image, while keeping it beautiful. Don't polish craft for its own sake. Replace or reword one part; at most %d words. Reply as JSON only: "
        '{"aim": "<aspect>", "craft": "...", "guidance": number 2.5-5.0, "change": "what you changed, in a few words"}'
        % (coll["subject"], craft or "(empty: write a first one)", coll.get("guidance", 3.5), "\n" + change, note or "-", ", ".join(aspects), CRAFT_WORDS)}]
    for attempt in range(2):
        raw = ask_writer([{"role": "user", "content": content}], max_tokens=300)
        d = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
        nc = str(d.get("craft") or "").strip().strip(".")
        if nc and nc != (craft or "") and len(nc.split()) <= max(CRAFT_WORDS, len((craft or "").split()) + 8):
            aim = str(d.get("aim") or "")
            try:
                g = round(min(5.0, max(2.5, float(d.get("guidance", coll.get("guidance", 3.5))))), 2)
            except Exception:
                g = coll.get("guidance", 3.5)
            return {"craft": nc, "aim": aim if aim in aspects else None, "change": str(d.get("change") or ""), "guidance": g}
    return None


MARK = FLUX / ".fluxd" / "mark.json"                      # the teacher's +/- on a frame, from the collection page


def take_mark(coll):
    """Apply an unapplied teacher mark. A + on a frame of any study makes it the running study's basis (copied in when it
    comes from another study); a - on a frame of this study returns to the frame it was built from. Returns a line for the editor."""
    import shutil
    mk = read_json(MARK, {})
    if not mk or mk.get("applied") or int(mk.get("mark", 0)) == 0:
        return ""
    src_folder = COLLECTIONS / str(mk.get("name") or "")
    e = None
    try:
        for line in (src_folder / "history.jsonl").read_text().splitlines():
            x = json.loads(line)
            if x.get("cycle") == int(mk["cycle"]) and x.get("file"):
                e = x
    except Exception:
        pass
    mk["applied"] = time.time(); write_json(MARK, mk)
    if not e:
        return ""
    folder = COLLECTIONS / coll["name"]
    if int(mk["mark"]) > 0:
        rel = e["file"]
        if mk.get("name") != coll["name"]:                   # a basis from another study: bring the frame in
            rel = "renders/basis-%s-c%04d-%s" % (mk["name"], e["cycle"], Path(e["file"]).name)
            (folder / "renders").mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src_folder / e["file"], folder / rel)
        coll["previous"], coll["craft"] = rel, e.get("craft", coll.get("craft", ""))
        coll["guidance"] = e.get("guidance", coll.get("guidance"))
        seed = (read_json(src_folder / "collection.json", {}).get("seed")) if mk.get("name") != coll["name"] else None
        if e.get("job"):
            try:
                seed = int([json.loads(l) for l in open(LEDGER) if e["job"] in l][-1].get("seed") or seed)
            except Exception:
                pass
        if seed:
            coll["seed"] = seed                               # the basis's own seed keeps the pairing exact
        return "The teacher marked image %s of %s as the RIGHT direction: it is now the basis; build on it." % (e["cycle"], mk["name"])
    if mk.get("name") == coll["name"] and e.get("base"):
        coll["previous"], coll["craft"] = e["base"], e.get("base_craft", coll.get("craft", ""))
    return "The teacher marked the change \"%s\" (image %s) as the WRONG direction: it has been undone; go a different way." % (e.get("change") or "", e["cycle"])


STALL_LATERAL = 6           # frames without a gain before an equally good, different frame may become the best
STALL_RESEED = 12           # frames without a gain before the best's craft is re-rendered with a new seed
SAME_RUN = 3                # near-identical frames in a row before the best gets a new seed (the images must keep moving)


PICTURES = H / "Models" / "flux-output" / "influx-outputs"   # synced to governor/outputs/, where influx.pictures lists its albums


def stage_for_site(coll, path, step, at=None):
    """File a study frame under influx.pictures' name grammar, protocol-<album>-stream-<YYYYMMDD>-<HHMMSS>-<NNN>.png
    (gallery lib/r2.mjs readProtocol), as a hardlink: no copy; the sync uploads it."""
    album = coll.get("album")
    if not album:
        return
    try:
        PICTURES.mkdir(parents=True, exist_ok=True)
        name = "protocol-%s-stream-%s-%03d.png" % (album, time.strftime("%Y%m%d-%H%M%S", time.gmtime(at or time.time())), int(step) % 1000)
        if not (PICTURES / name).exists():
            os.link(path, PICTURES / name)
    except Exception:
        pass


def chain_step(coll, finished):
    """Build from the best. Each frame is judged against the best so far (relative change, two calls, both orders);
    it replaces the best only if the jury scores it better. The edit step sees only the most recent image and how it
    compared. Progression never stops: after STALL_LATERAL frames without a gain an equally good frame may move the
    best sideways, after STALL_RESEED the best's craft gets a new seed. The teacher's +/- overrides the jury."""
    import random
    cfg = study_config(); taught = teacher_vision(coll, cfg); coll["size"] = int(cfg["size"])
    folder = COLLECTIONS / coll["name"]
    job = finished[0]
    try:
        if any(json.loads(l).get("job") == job.get("id") for l in (folder / "history.jsonl").read_text().splitlines()[-20:]):
            coll.pop("pending", None); coll["batch"] = None      # already filed (a restart after judging it): move on, never stall
            write_json(COLL, coll)
            submit(coll)
            return
    except Exception:
        pass
    src = Path(job.get("output") or "")
    step = int(coll.get("cycle", 0)) + 1
    dst = folder / "renders" / ("c%04d-%s" % (step, src.name))
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(src.read_bytes())
    rel = str(dst.relative_to(folder))
    stage_for_site(coll, dst, step)
    cand = (coll.get("batch") or [{}])[0]
    best = coll.get("best") or ({"file": coll["previous"], "craft": coll.get("craft", ""), "guidance": coll.get("guidance")} if coll.get("previous") else None)
    entry = {"cycle": step, "job": job["id"], "prompt": job.get("prompt"), "craft": cand.get("craft", coll.get("craft", "")), "aim": cand.get("aim"),
             "change": cand.get("change"), "guidance": cand.get("guidance", coll.get("guidance")), "file": rel, "at": time.time(),
             "base": (best or {}).get("file"), "base_craft": (best or {}).get("craft", ""),
             "hypothesis": cand.get("hypothesis"), "move": cand.get("move"), "vision": (coll.get("vision") or {}).get("idea")}
    prof, note, stall = None, "", int(coll.get("stall", 0))
    if not best:
        entry.update(outcome="first frame", net=0.0)
        best = {"file": rel, "craft": entry["craft"], "guidance": entry["guidance"]}
        coll.setdefault("origin", {"cycle": step, "file": rel})
        coll.setdefault("progress", {"net": 0.0, "steps": 0, "aspects": {}})
        stall = 0
    else:
        m, prof, broken, net, ready = compare_debiased(coll, folder / best["file"], dst)   # best as A: + means this frame beats the best
        note = next((v["why"] for v in prof.values() if v.get("why")), "")
        # leaving the subject's words is drift only when it doesn't carry the teacher's direction further
        # (the direction dissolves the gown: SigLIP's "gown" similarity falls exactly when the frame follows it)
        drift = bool(cfg.get("drift_gate", False)) and m["subject_chall"] < m["subject_champ"] - DRIFT and (prof.get("teacher's direction") or {}).get("d", 0) <= 0
        entry.update(measured=m, profile=prof, net=net)
        if m.get("d_prev") is not None:
            entry["d_origin"] = m["d_prev"]                  # distance from the line's start (the teacher's anchor)
            entry["away"] = round(m["d_prev"] - float(coll.get("base_origin") or 0.0), 4)
        if broken or drift:
            entry["outcome"] = "not kept: %s (net %+.1f)" % ("broken" if broken else "subject drift", net)
            stall += 1
        elif m.get("d_prev") is not None and coll.get("base_origin") is not None and m["d_prev"] <= float(coll["base_origin"]):
            entry["outcome"] = "not kept: moved back toward the start (%.3f from it, the base was %.3f; net %+.1f)" % (m["d_prev"], float(coll["base_origin"]), net)
            stall += 1
        elif m["d_champ"] < max(0.08, float(cfg.get("min_change", 0.12))):   # a barely-changed frame reads as a repeat: never kept   # the strict monitor on change (DINOv2 distance to the best)
            entry["outcome"] = "not kept: too little change (%.3f < %.2f, net %+.1f)" % (m["d_champ"], float(cfg.get("min_change", 0.12)), net)
            stall += 1
        elif net >= GAIN and ready and (prof.get("beauty") or {}).get("d", 0) >= 0:
            entry["outcome"] = "accepted: better than the best (net %+.1f)" % net
            p = coll.setdefault("progress", {"net": 0.0, "steps": 0, "aspects": {}})
            p["net"] = round(p["net"] + net, 2); p["steps"] += 1
            for a, v in prof.items():
                p["aspects"][a] = round(p["aspects"].get(a, 0.0) + v["d"], 2)
            best, stall = {"file": rel, "craft": entry["craft"], "guidance": entry["guidance"]}, 0
            if coll.get("anchor") is not None:          # the gain compounds: the anchor's line now continues from this frame
                coll.setdefault("heads", {})[str(coll["anchor"])] = {"file": rel, "craft": entry["craft"], "guidance": entry["guidance"], "cycle": step,
                                                                      "d_origin": entry.get("d_origin", 0.0)}
        elif stall + 1 >= STALL_LATERAL and net >= 0 and m["d_champ"] >= NO_TAKE:
            entry["outcome"] = "accepted: sideways after %d frames without a gain (net %+.1f)" % (stall + 1, net)
            best, stall = {"file": rel, "craft": entry["craft"], "guidance": entry["guidance"]}, 0
        else:
            entry["outcome"] = "not kept (net %+.1f)" % net
            stall += 1
    beauty_vision.learn(coll, entry, str(entry.get("outcome", "")).startswith("accepted: better"))
    tsc = read_json(folder / "scores.json", {})
    for l in coll.get("lessons") or []:                   # the teacher's Direction score on a step outranks the judge's verdict
        dv = int((tsc.get(str(l.get("cycle"))) or {}).get("direction", 0))
        if dv:
            l["held"], l["by"] = dv > 0, "teacher"
    with open(folder / "history.jsonl", "a") as f:
        f.write(json.dumps(entry) + "\n")
    coll["best"], coll["previous"], coll["stall"] = best, rel, stall     # previous: the most recent image, what the editor sees
    d = (entry.get("measured") or {}).get("d_champ")
    coll["same_run"] = (int(coll.get("same_run", 0)) + 1) if (d is not None and d < NO_TAKE) else 0
    coll["craft"], coll["guidance"] = best["craft"], best.get("guidance", coll.get("guidance"))
    h = coll.setdefault("health", {})
    h["gains"] = ((h.get("gains") or []) + [1 if str(entry["outcome"]).startswith("accepted: better") else 0])[-10:]
    h["cycles_since_gain"] = stall
    g = sum(h["gains"])
    h["verdict"] = ("improving: %d gains in the last %d images" % (g, len(h["gains"]))) if g else ("searching: %d images since the last gain" % stall)
    h["updated_at"] = time.time()
    coll["cycle"], coll["generation"] = step, int(coll.get("generation", 0)) + 1
    coll["last_outcome"], coll["last_profile"] = entry["outcome"], prof
    coll.pop("pending", None)
    teacher = take_mark(coll)
    if teacher:
        with open(folder / "history.jsonl", "a") as f:
            f.write(json.dumps({"cycle": step, "outcome": "teacher mark", "note": teacher, "at": time.time()}) + "\n")
        coll["best"] = {"file": coll["previous"], "craft": coll["craft"], "guidance": coll.get("guidance")}
        stall, coll["stall"] = 0, 0
    anc = anchors(folder)
    if anc:                                               # the teacher's anchors lead: rotate through them, one per frame
        # one line at a time: stay on the current anchor's line while it keeps gaining, so each frame follows the one before;
        # switch (by the teacher's Beauty scores) only after LINE_PATIENCE frames without a gain, or if that anchor is gone
        gained = str(entry.get("outcome", "")).startswith("accepted: better")
        coll["line_miss"] = 0 if gained else int(coll.get("line_miss", 0)) + 1
        cur = next((x for x in anc if x["cycle"] == coll.get("anchor")), None)
        if cur is None or coll["line_miss"] >= LINE_PATIENCE:
            a = pick_anchor(coll, anc)
            coll["line_miss"] = 0
        else:
            a = cur
        coll["anchor"] = a["cycle"]
        head = (coll.get("heads") or {}).get(str(a["cycle"])) or a   # build from the line's latest improvement, not the anchor again
        coll["anchor_file"], coll["base_origin"] = a["file"], float(head.get("d_origin", 0.0) or 0.0)
        coll["best"] = {"file": head["file"], "craft": head["craft"], "guidance": head["guidance"]}
        coll["craft"], coll["guidance"] = head["craft"], head["guidance"]
    else:
        coll.pop("anchor", None)
        o = (coll.get("origin") or {}).get("file")          # no anchors yet: the study's own first frame is where the line starts
        if o and (folder / o).exists():
            coll["anchor_file"] = o
            bf = (coll.get("best") or {}).get("file")
            coll["base_origin"] = 0.0
            try:
                for line in (folder / "history.jsonl").read_text().splitlines():
                    e = json.loads(line)
                    if e.get("file") == bf and e.get("d_origin") is not None:
                        coll["base_origin"] = float(e["d_origin"])
            except Exception:
                pass
    by_dist = {}
    marks_now = read_json(folder / "marks.json", {})
    try:
        for line in (folder / "history.jsonl").read_text().splitlines():
            e = json.loads(line)
            sc = int(marks_now.get(str(e.get("cycle")), 0)) if e.get("file") else 0
            if sc:
                dname = next((x for x in beauty_vision.DISTANCES if (e.get("craft") or "").startswith(x)), None)
                if dname:
                    by_dist[dname] = by_dist.get(dname, 0) + sc
    except Exception:
        pass
    coll["taste_distances"] = by_dist
    ts = teacher_steps(folder)
    ds = [(json.loads(l).get("measured") or {}).get("d_champ") for l in (folder / "history.jsonl").read_text().splitlines()[-8:]]
    ds = [x for x in ds if x is not None]
    ts["now"] = sum(ds) / len(ds) if ds else None
    coll["teacher_steps"] = ts
    if ts["target"]:                                      # the change gate follows the step size the teacher rewards
        cfg["min_change"] = max(0.08, round(0.5 * ts["target"], 3))
    coll["taste"] = {"liked": [(a["score"], a["craft"]) for a in sorted(anc, key=lambda a: -a["score"])[:5]], "disliked": disliked(folder)[:5]}
    if stall >= beauty_vision.STALL_REVISION and not taught and cfg["writer"] != "off":   # the vision stalled: re-imagine it from the best frame
        try:
            nv = beauty_vision.make_vision(coll, direction_text(), folder / (coll.get("best") or {}).get("file", rel))
            coll["seed"], coll["stall"], coll["same_run"], stall = random.randrange(1, 2**31 - 1), 0, 0, 0
            coll["batch"], coll["aim"], coll["last_change"] = [nv], "vision", nv["change"]
            coll["champion"] = {"file": (coll.get("best") or {}).get("file"), "craft": coll["craft"], "seed": coll.get("seed")}
            coll["prompt"], coll["updated_at"] = compose(coll["subject"], coll["craft"]), time.time()
            write_json(folder / "collection.json", coll)
            fresh = read_json(COLL, {})
            if fresh.get("name") == coll["name"] and fresh.get("active"):
                write_json(COLL, coll)
                submit(coll)
            save(station="rendering", collection=coll["name"], last_outcome=entry["outcome"], health=h)
            return
        except Exception as ex:
            coll["last_change"] = "vision failed: %s" % repr(ex)[:120]
    if not anc and (stall >= STALL_RESEED or int(coll.get("same_run", 0)) >= SAME_RUN):   # progression never stops, and the images keep moving
        coll["seed"], coll["stall"] = random.randrange(1, 2**31 - 1), 0
        why = ("%d near-identical images in a row" % coll.get("same_run", 0)) if int(coll.get("same_run", 0)) >= SAME_RUN else ("%d images without a gain" % stall)
        coll["same_run"] = 0
        coll["batch"], coll["last_change"] = [{"craft": coll["craft"], "aim": None, "change": "new seed on the best", "guidance": coll.get("guidance", 3.5)}], "reseeded after " + why
        coll["best"] = None                                 # the reseeded frame becomes the new best; progress and origin are kept
        coll["previous"] = None
    else:
        told = ("This image %s the best so far." % ("beat" if str(entry["outcome"]).startswith("accepted: better") else "did not beat")) if prof else ""
        try:
            if cfg["writer"] == "off":
                raise RuntimeError("writer off (teacher's setting): same prompt, new seed")
            nxt = beauty_vision.propose(coll, folder / (coll.get("best") or {}).get("file", rel), folder / rel, prof,
                                        " ".join(x for x in (teacher, told, note) if x), direction_text(), stall)
        except Exception as ex:
            nxt, coll["last_change"] = None, "proposal failed: %s" % repr(ex)[:120]
        if nxt:
            coll["batch"], coll["aim"], coll["last_change"] = [nxt], nxt["aim"], nxt["change"]
        else:
            coll["batch"] = [{"craft": coll["craft"], "aim": None, "change": "writer failed: same words, new seed", "guidance": coll.get("guidance", 3.5)}]
            coll["base_seed"] = None                        # a failed writer never re-renders the base frame: a new seed instead
    coll["champion"] = {"file": (coll.get("best") or {}).get("file"), "craft": coll["craft"], "seed": coll.get("seed")}
    if (coll.get("batch") or [{}])[0].get("change", "").startswith("writer failed"):
        coll["base_seed"] = None
    else:
        coll["base_seed"] = seed_of((coll.get("best") or {}).get("file"))
    coll["prompt"] = compose(coll["subject"], coll["craft"])
    coll["updated_at"] = time.time()
    write_json(folder / "collection.json", coll)
    fresh = read_json(COLL, {})
    if fresh.get("name") == coll["name"] and fresh.get("active"):
        write_json(COLL, coll)
        submit(coll)
    save(station="rendering", collection=coll["name"], last_outcome=entry["outcome"], health=h)


def cycle(coll, finished):
    """One cycle: file every candidate, compare each with the champion, keep the best real gain, update health, render the next cycle."""
    folder = COLLECTIONS / coll["name"]
    if not finished:                                       # every render in the cycle failed: render the cycle again
        coll.pop("pending", None); write_json(COLL, coll); submit(coll); return
    if coll.get("mode", "chain") == "chain":               # the operator's method: most recent image only, relative change
        return chain_step(coll, finished)
    cyc = int(coll.get("cycle", 0)) + 1
    files = {}
    for job in finished:
        src = Path(job.get("output") or "")
        if src.exists():
            dst = folder / "renders" / ("c%04d-%s" % (cyc, src.name))
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(src.read_bytes())
            files[job["id"]] = dst
    champ = coll.get("champion")
    entries, best = [], None
    if not champ:
        job = finished[0]
        coll["champion"] = {"cycle": cyc, "file": str(files[job["id"]].relative_to(folder)), "craft": coll.get("craft", ""),
                            "guidance": coll.get("guidance"), "seed": coll.get("seed")}
        reseed = bool(coll.get("origin"))                  # a reseed keeps the collection's progress and its first frame
        if not reseed:
            coll["origin"] = dict(coll["champion"])
            coll["progress"] = {"net": 0.0, "steps": 0, "aspects": {}}
        entries.append({"cycle": cyc, "job": job["id"], "outcome": "reseeded: same craft, new seed" if reseed else "first champion",
                        "craft": coll.get("craft", ""), "file": coll["champion"]["file"], "at": time.time()})
        context = ""
    else:
        champ_path = folder / champ["file"]
        by_job = {c.get("job"): c for c in coll.get("batch") or []}
        for job in finished:                              # one candidate at a time
            if job["id"] not in files:
                continue
            cand = by_job.get(job["id"], {})
            m, profile, broken, net, ready = compare_debiased(coll, champ_path, files[job["id"]])
            aim_d = profile.get(cand.get("aim"), {}).get("d") if cand.get("aim") else None
            worst = min([v["d"] for v in profile.values()] or [0])
            drift = m["subject_chall"] < m["subject_champ"] - DRIFT
            eligible = ready and not broken and not drift and net >= GAIN and worst >= ACCEPT_OTHERS_MIN
            why = "broken" if broken else "subject drift" if drift else ("gain" if eligible else
                  "bias still being measured" if not ready and net >= GAIN else "no real gain")
            e = {"cycle": cyc, "job": job["id"], "craft": cand.get("craft"), "aim": cand.get("aim"), "change": cand.get("change"),
                 "guidance": cand.get("guidance"), "file": str(files[job["id"]].relative_to(folder)), "measured": m,
                 "profile": profile, "net": net, "aim_d": aim_d, "eligible": eligible, "verdict": why, "at": time.time()}
            entries.append(e)
            if eligible and (best is None or (net, aim_d or 0) > (best["net"], best["aim_d"] or 0)):
                best = e
        for e in entries:
            e["outcome"] = ("accepted: net %+.1f" % e["net"]) if e is best else \
                ("not kept: another candidate won (net %+.1f)" % e["net"] if e["eligible"] else "not kept: %s (net %+.1f)" % (e["verdict"], e["net"]))
        if best:
            coll["champion"] = {"cycle": cyc, "file": best["file"], "craft": best["craft"], "guidance": best["guidance"], "seed": champ.get("seed")}
            prog = coll.setdefault("progress", {"net": 0.0, "steps": 0, "aspects": {}})
            prog["net"] = round(prog["net"] + best["net"], 2); prog["steps"] += 1
            for a, v in best["profile"].items():
                prog["aspects"][a] = round(prog["aspects"].get(a, 0.0) + v["d"], 2)
        context = "\n".join("- %s (aim %s): net %+.1f; %s" % (e.get("change"), e.get("aim"), e["net"],
                            ", ".join("%s %+.1f" % (a, v["d"]) for a, v in e["profile"].items() if v["d"])) for e in entries)
    with open(folder / "history.jsonl", "a") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")
    mem = coll.setdefault("memory", [])                    # what each edit did, for the next proposals
    for e in entries:
        if "net" in e and e.get("change"):
            mem.append({"change": e["change"][:80], "aim": e.get("aim"), "net": e["net"], "kept": e is best,
                        "dist": (e.get("measured") or {}).get("d_champ", 1)})
    del mem[:-40]
    # health: whether it is improving, known at all times
    h = coll.setdefault("health", {"gains": [], "best_nets": []})
    h["gains"] = (h["gains"] + [1 if best else 0])[-10:]
    h["best_nets"] = (h["best_nets"] + [max([e.get("net", 0) for e in entries if "net" in e] or [0])])[-10:]
    h["cycles_since_gain"] = 0 if best else int(h.get("cycles_since_gain", 0)) + (1 if champ else 0)
    if champ and coll.get("origin") and cyc % ORIGIN_EVERY == 0 and coll["champion"]["file"] != coll["origin"]["file"]:
        try:
            _, op, _, onet = compare(coll, folder / coll["origin"]["file"], folder / coll["champion"]["file"])
            h["vs_origin"] = {"cycle": cyc, "net": onet, "aspects": {a: v["d"] for a, v in op.items() if v["d"]}}
        except Exception as ex:
            h["vs_origin"] = {"cycle": cyc, "error": repr(ex)[:120]}
    g = sum(h["gains"])
    h["verdict"] = ("improving: %d gains in the last %d cycles" % (g, len(h["gains"]))) if g else \
                   ("stalled: %d cycles without a gain" % h["cycles_since_gain"])
    h["updated_at"] = time.time()
    coll["cycle"], coll["generation"] = cyc, int(coll.get("generation", 0)) + len(entries)
    coll["last_outcome"] = entries[0]["outcome"] if not champ else ("accepted: net %+.1f" % best["net"] if best else "no candidate beat the champion")
    coll["last_profile"] = (best or (entries[0] if entries else {})).get("profile")
    coll["aim"] = best.get("aim") if best else None
    coll["craft"] = coll["champion"]["craft"]
    coll["prompt"] = compose(coll["subject"], coll["craft"])
    coll.pop("pending", None)
    try:
        coll["batch"] = propose_batch(coll, coll["champion"]["craft"], context)
    except Exception as ex:
        coll["batch"], coll["last_change"] = [], "proposal failed: %s" % repr(ex)[:120]
    if not coll["batch"]:                                  # no usable edits: fresh seed on the champion's craft, new baseline
        import random
        coll.update(champion=None, seed=random.randrange(1, 2**31 - 1), last_change="no usable edits: reseeding the champion's craft")
    else:
        coll["last_change"] = " | ".join("%s (%s)" % (c["change"], c["aim"]) for c in coll["batch"])
    coll["updated_at"] = time.time()
    write_json(folder / "collection.json", coll)
    fresh = read_json(COLL, {})
    if fresh.get("name") == coll["name"] and fresh.get("active"):
        write_json(COLL, coll)
        submit(coll)
    save(station="rendering", collection=coll["name"], last_outcome=coll["last_outcome"], health=h,
         last_receipt={"judges": [{"model": "jury", "role": a, "score": None, "critique": "%s %+.1f · %s" % (a, v["d"], v["why"])}
                                  for a, v in (coll.get("last_profile") or {}).items()], "curved_score": None, "outcome": coll["last_outcome"]})


def evolve(coll, job, receipt):
    """One mild step: file the judged render into the collection, update the best, then change the prompt a little."""
    # The curved score is a percentile against recent history and saturates in a young collection (99.9 on every frame);
    # uniqueness penalises exactly the similarity a collection is after. Evolution climbs the jurors' own weighted score.
    score = receipt.get("raw_composite_pre_uniqueness")
    if score is None:
        score = receipt.get("raw_composite")
    gen = int(coll.get("generation", 0)) + 1
    folder = COLLECTIONS / coll["name"]
    judges = [{"model": j.get("model"), "role": j.get("role"), "score": j.get("score"), "critique": j.get("critique")}
              for j in receipt.get("judges") or [] if not j.get("degraded")]
    entry = {"generation": gen, "job": job["id"], "prompt": job.get("prompt"), "used_prompt": coll.get("prompt"),
             "guidance": coll.get("guidance"), "score": score, "curved": receipt.get("curved_score"), "tier": receipt.get("tier"),
             "uniqueness": receipt.get("uniqueness_adjustment"), "judges": judges, "at": time.time()}
    src = Path(job.get("output") or "")
    if src.exists():
        dst = folder / "renders" / ("gen-%04d-%s-%s" % (gen, "na" if score is None else "%05.1f" % score, src.name))
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())
        entry["file"] = str(dst.relative_to(folder))
    with open(folder / "history.jsonl", "a") as f:
        f.write(json.dumps(entry) + "\n")
    coll["generation"] = gen
    best = coll.get("best") or {}
    if score is not None and (best.get("score") is None or score >= best["score"]):
        coll["best"] = {"score": score, "prompt": coll["prompt"], "guidance": coll.get("guidance"), "generation": gen, "file": entry.get("file")}
    base = coll["prompt"]
    if score is not None and coll.get("best") and score < coll["best"]["score"] - WORSE_BY:
        base, coll["guidance"] = coll["best"]["prompt"], coll["best"].get("guidance", coll.get("guidance"))
        coll["last_change"] = "scored %.1f, under the best %.1f: stepping from the best prompt" % (score, coll["best"]["score"])
    notes = "\n".join("- %s (%s): %s" % (j["model"], j["score"], j["critique"]) for j in judges) or "- (no surviving critique)"
    dirs = "\n".join("- " + d["text"] for d in (read_json(DIRECTIONS, {}).get("directions") or [])) or "- (none)"
    if score is not None:
        try:
            raw = ask_witness([
                {"role": "system", "content": "You evolve one FLUX image prompt for a collection with a fixed subject. Make ONE small change that answers the jury's "
                 "critique: replace or reword one phrase, or swap one detail for another. Don't append lists of quality words; the prompt must not get "
                 "longer than 60 words. Keep everything else as it is; never change the subject; keep the teacher's directions. Reply as JSON only: "
                 '{"prompt": "...", "guidance": number between 2.5 and 5.0, "change": "what you changed, in a few words"}'},
                {"role": "user", "content": "Subject (fixed): %s\nCurrent prompt: %s\nCurrent guidance: %s\nJury (score out of 100):\n%s\nTeacher's directions:\n%s"
                 % (coll["subject"], base, coll.get("guidance", 3.5), notes, dirs)}])
            nxt = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
            prompt = str(nxt.get("prompt") or "").strip()
            if prompt and len(prompt.split()) > 60:
                coll["last_change"] = "the proposed prompt ran past 60 words; kept the current one"
            elif prompt and coll["subject"].lower() in prompt.lower():
                coll["prompt"] = prompt
                g = float(nxt.get("guidance", coll.get("guidance", 3.5)))
                coll["guidance"] = round(min(5.0, max(2.5, g)), 2)
                coll.setdefault("last_change", "")
                coll["last_change"] = (coll["last_change"] + " · " if coll["last_change"].startswith("scored") else "") + str(nxt.get("change") or "")
            else:
                coll["last_change"] = "the proposed prompt dropped the subject; kept the current one"
        except Exception as e:
            coll["last_change"] = "evolution step failed: %s" % repr(e)[:160]
    coll["updated_at"] = time.time()
    write_json(folder / "collection.json", {k: v for k, v in coll.items()})
    fresh = read_json(COLL, {})
    if fresh.get("name") == coll["name"] and fresh.get("active"):
        write_json(COLL, coll)
seen, busy, lock = set(), threading.Event(), threading.Lock()


def mode():
    try:
        return json.loads(MODE.read_text()).get("mode", "wall")
    except Exception:
        return "wall"


def save(**kw):
    state.update(kw, updated_at=time.time(), mode=mode())
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1))
    os.replace(tmp, STATE)


def jobs():
    """Latest record per job id from the tail of the ledger (it is appended as jobs progress)."""
    try:
        with open(LEDGER, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 400_000))
            lines = f.read().decode("utf-8", "ignore").splitlines()[1:]
    except Exception:
        return []
    latest = {}
    for line in lines:
        try:
            j = json.loads(line)
        except Exception:
            continue
        if isinstance(j, dict) and j.get("id"):
            latest[j["id"]] = {**latest.get(j["id"], {}), **j}
    return list(latest.values())


def judge(job):
    """job is one wall frame, or the list of a collection cycle's finished candidates."""
    try:
        coll = read_json(COLL, {})
        if isinstance(job, list):
            save(station="judging %d" % len(job), job_id=",".join(j["id"] for j in job))
            seen.update(j["id"] for j in job)
            cycle(coll, job)
            return
        save(station="judging", job_id=job["id"])
        receipt = jury_evaluator.score_frame(job)
        seen.add(job["id"])
        save(station="verdict-ready", last_job_id=job["id"], last_score=receipt.get("curved_score"),
             last_receipt=receipt, error="", seen=list(seen)[-500:])
    except Exception as e:
        save(station="error", error=repr(e)[-400:])
    finally:
        busy.clear()
        consider()


def consider():
    with lock:
        if busy.is_set():
            return
        if mode() != "wall":
            save(station="off")
            return
        coll = read_json(COLL, {})
        if coll.get("active") and DIFF:
            # a collection cycle: wait until every candidate in flight has finished, then judge them together
            pending = coll.get("pending") or []
            if not pending:
                return
            latest = {j["id"]: j for j in jobs()}
            got = [latest[p] for p in pending if p in latest]
            if len(got) < len(pending) or any(j.get("status") not in ("done", "error", "cancelled", "failed") for j in got):
                return
            finished = [j for j in got if j.get("status") == "done"]
            busy.set()
            threading.Thread(target=judge, args=(finished,), daemon=True).start()
            return
        done = [j for j in jobs() if j.get("status") == "done" and j["id"] not in seen]
        if not done:
            save(station="waiting-for-frame")
            return
        job = max(done, key=lambda j: float(j.get("finished") or j.get("updated_at") or 0))
        seen.update(j["id"] for j in done)        # newest only: older finished renders are skipped, not queued
        busy.set()
    threading.Thread(target=judge, args=(job,), daemon=True).start()


def main():
    seen.update(j["id"] for j in jobs())          # history is not re-judged on start
    save(station="waiting-for-frame")
    consider()          # a cycle may have finished while the service was down
    kick()
    watch = subprocess.Popen(["inotifywait", "-m", "-q", "-e", "modify,close_write,create,moved_to",
                              "--format", "%f", str(LEDGER.parent)], stdout=subprocess.PIPE, text=True)
    for name in watch.stdout:
        if name.strip() in (LEDGER.name, MODE.name, COLL.name, MARK.name):
            consider()
            kick()


if __name__ == "__main__":
    main()
