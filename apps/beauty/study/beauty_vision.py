"""Vision, insight and uniqueness for the Beauty evolution loop (beauty_jury.chain_step calls in here).

The old loop hill-climbed on small craft edits: most frames came back "no visible difference", nothing said what the
collection was reaching for, and nothing it learned carried forward. This replaces the writer's step with three things:

  vision     Gemma, as art director, looks at the best frame and writes what the extraordinary version of this
             collection is: specific light, palette, body and pose, garment idea, setting, camera, emotion. Every
             edit serves it; the judge scores "closer to the vision". A stall of STALL_REVISION frames re-imagines it.
  insight    before each edit the writer names what is most alive in the best frame (keep it), what most holds it
             back from the vision, and a hypothesis: "if <change>, the image will <effect>". The next verdict
             confirms or refutes it, and the confirmed/refuted hypotheses are fed back as lessons.
  uniqueness the writer sees what the collection already tried and must not repeat it; bold moves (a new idea)
             and refinements alternate with the run of results; the judge scores originality.

Strictly linear: one writer call per frame (plus a vision call when the vision is made or remade), then one render.
The teacher's directions come verbatim from prompt.json; the model is always an adult woman.
"""
import base64, json, urllib.request
from pathlib import Path

WRITER = "http://127.0.0.1:8005/v1/chat/completions"      # Gemma 4 12B, served as "governor"
MODEL = "governor"
CRAFT_WORDS = 20          # the subject is the core (~45 CLIP tokens); the writer owns pose, light, framing, garment detail
DISTANCES = ["extreme wide shot, a tiny figure in the landscape", "wide shot, her whole body small in the frame", "full-body shot",
             "three-quarter shot from the knees up", "medium shot from the waist up"]            # far to near: one step is incremental
STEP_FLOOR = 0.12           # measured change a step must reach: tiny tweaks (0.002-0.04) were invisible to the judge
DIMENSIONS = ["garment", "pose and body", "light", "camera and framing", "palette", "setting"]   # each step changes one, never the last two's
STALL_REVISION = 8          # frames without a gain before the vision itself is re-imagined (and the seed changes)
BOLD_AFTER = 2              # frames without a gain before the writer is asked for a bold move instead of a refinement
LESSONS = 14
ADULT = "The model is an adult woman in her late twenties."


def _uri(path):
    p = Path(path)
    return "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode() if p.exists() else None


def _ask(content, max_tokens=500, temperature=0.8):
    body = {"model": MODEL, "temperature": temperature, "max_tokens": max_tokens, "response_format": {"type": "json_object"},
            "messages": [{"role": "user", "content": content}]}
    req = urllib.request.Request(WRITER, json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        raw = json.load(r)["choices"][0]["message"]["content"]
    return json.loads(raw[raw.index("{"): raw.rindex("}") + 1])


def _image(path, label):
    u = _uri(path) if path else None
    return [{"type": "text", "text": label}, {"type": "image_url", "image_url": {"url": u}}] if u else []


def make_vision(coll, direction, best_path=None):
    """The art director's vision for the collection. Remade after a stall: then it must be genuinely different."""
    old = (coll.get("vision") or {}).get("text")
    tried = [v for v in coll.get("visions_tried", [])][-4:]
    text = ("You are the art director of a fashion collection. " + ADULT + "\n"
            "Subject (fixed): %s\nThe teacher's direction (it defines better, verbatim): %s\n"
            % (coll["subject"], direction or "(none)"))
    if best_path:
        text += "Above is the collection's best frame so far. Keep what is most alive in it.\n"
    if old:
        text += "The current vision has stalled: \"%s\". Imagine a genuinely different one.\n" % old
    if tried:
        text += "Visions already tried (do not repeat): " + " | ".join(tried) + "\n"
    text += ("Write the vision: the extraordinary version of this collection that no one has shot before. Be specific and visual: "
             "light, palette, the body and pose, the garment idea, setting, camera and lens, the emotion. At most 70 words. "
             "Then the one idea at its heart in at most 10 words, and a first craft phrase for the image prompt (at most %d words) "
             "that realises it. JSON only: {\"vision\": \"...\", \"idea\": \"...\", \"craft\": \"...\", \"guidance\": 2.5-5.0}" % CRAFT_WORDS)
    d = _ask(_image(best_path, "The best frame:") + [{"type": "text", "text": text}], max_tokens=500, temperature=0.9)
    v = {"text": str(d.get("vision") or "").strip(), "idea": str(d.get("idea") or "").strip(), "at_cycle": int(coll.get("cycle", 0))}
    if not v["text"]:
        raise ValueError("no vision")
    coll["vision"] = v
    coll["visions_tried"] = (coll.get("visions_tried") or []) + [v["idea"] or v["text"][:80]]
    return {"craft": " ".join(str(d.get("craft") or "").split()[:CRAFT_WORDS]).strip(".") or coll.get("craft", ""),
            "aim": "vision", "change": "new vision: " + (v["idea"] or v["text"][:80]), "hypothesis": "the new vision: " + v["text"][:160],
            "move": "vision", "guidance": _g(d.get("guidance"), coll)}


def _g(x, coll):
    try:
        return round(min(3.6, max(2.8, float(x))), 2)   # room for variation: low guidance, never the stiff 4-5
    except Exception:
        return coll.get("guidance", 3.5)


def learn(coll, entry, gained):
    """File the last hypothesis as a lesson: held (the frame beat the best) or failed, with the judge's note."""
    hyp = entry.get("hypothesis")
    if not hyp:
        return
    les = coll.setdefault("lessons", [])
    les.append({"hypothesis": hyp[:200], "move": entry.get("move"), "held": bool(gained), "net": entry.get("net"), "away": entry.get("away"),
                "dim": (coll.get("dims") or [None])[-1],
                "saw": ((entry.get("profile") or {}).get("beauty") or {}).get("why", "")[:120], "cycle": entry.get("cycle")})
    del les[:-LESSONS * 2]


def propose(coll, best_path, latest_path, profile, note, direction, stall):
    """One writer call: insight on the best frame, then the next craft phrase in service of the vision."""
    v = coll.get("vision") or {}
    les = coll.get("lessons") or []
    held = [l for l in les if l["held"]][-6:]
    failed = [l for l in les if not l["held"]][-6:]
    tried = [c for c in (coll.get("tried") or [])][-10:]
    move = "refine"                                        # incremental steps only; exploration is the loop's new-seed frame
    import random
    recent = [a for a in (coll.get("dims") or [])][-2:]
    free = [x for x in DIMENSIONS if x not in recent]
    last_l = (coll.get("lessons") or [None])[-1]
    momentum = bool(last_l and last_l.get("held") and (last_l.get("away") or 0) > 0 and last_l.get("dim") in DIMENSIONS)
    if momentum:                                           # it moved away and was kept: push the same change further
        free = [last_l["dim"]] + [x for x in DIMENSIONS if x != last_l["dim"]]
    base_craft = coll.get("craft") or ""
    base_dist = next((x for x in DISTANCES if base_craft.startswith(x)), None)
    step_dist = bool(free) and free[0] == "camera and framing"
    change = ", ".join("%s %+.1f" % (a, x["d"]) for a, x in (profile or {}).items() if x.get("d")) or "no clear difference"
    text = (ADULT + "\nSubject (fixed, not yours to edit): %s\nThe teacher's direction (it defines better, verbatim): %s\n"
            "The vision: %s\nIts idea: %s\n"
            "The current best craft phrase: %s\n"
            "The latest frame against the best: %s. The judge saw: %s\n"
            % (coll["subject"], direction or "(none)", v.get("text", "(none yet)"), v.get("idea", ""),
               coll.get("craft") or "(empty)", change, note or "-"))
    ts = coll.get("teacher_steps") or {}
    if ts.get("recent"):                                   # the teacher's own verdicts outrank the judge's
        text += "The TEACHER scored these steps (Direction = did it move AWAY from where this line started, well: + keep going that way, - try another way; Difference = was the rate of change right; Uniqueness):\n" + "\n".join(
            "- \"%s\": direction %+d, difference (size) %+d, uniqueness %+d" % (r["change"][:90], r.get("direction", 0), r.get("difference", 0), r.get("uniqueness", 0)) for r in ts["recent"]) + "\n"
    if ts.get("target") and ts.get("now"):
        ts = dict(ts); ts["target"] = max(float(ts["target"]), STEP_FLOOR)   # never smaller than a step the judge can see
        ratio = ts["now"] / ts["target"]
        text += ("Step SIZE: the teacher rewards steps that change the picture about %.2f (measured); your recent steps changed it %.2f, so make this step %s.\n"
                 % (ts["target"], ts["now"], "clearly BIGGER" if ratio < 0.7 else "clearly SMALLER" if ratio > 1.4 else "about the same size"))
    base_key = str((coll.get("best") or {}).get("file") or "")
    tried_here = (coll.get("tried_by_base") or {}).get(base_key, [])
    if tried_here:
        text += "Already tried from THIS exact frame (do NOT repeat any of these, choose something else):\n" + "\n".join("- " + t for t in tried_here[-12:]) + "\n"
    if coll.get("base_origin") is not None:
        text += ("This line started at the teacher's anchor; the current frame is %.2f away from it (measured). Every step must move FURTHER from "
                 "that start, in any direction, one increment at a time. Never drift back toward where it began.\n" % float(coll["base_origin"]))
    last = (coll.get("lessons") or [])[-1:] 
    if last:                                               # direction of change: keep going if it helped, turn back if it hurt
        l = last[0]
        text += ("Your LAST change worked (%s). Push it one step FURTHER in the same direction.\n" % l["hypothesis"][:160]) if l["held"] else \
                ("Your LAST change did not help (%s). Go the OTHER way on that same thing, or change something else.\n" % l["hypothesis"][:160])
    if held:
        text += "Hypotheses that HELD (build on what they teach):\n" + "\n".join("- " + l["hypothesis"] for l in held) + "\n"
    if failed:
        text += "Hypotheses that FAILED (learn from them, don't repeat them):\n" + "\n".join("- %s (judge: %s)" % (l["hypothesis"], l["saw"] or "no gain") for l in failed) + "\n"
    if tried:
        text += "Craft already tried in this collection (be different):\n" + "\n".join("- " + t for t in tried) + "\n"
    text += ("INCREMENTAL: the next frame keeps the same seed as the best frame, so change ONE thing and keep everything else word for word. "
             "Don't write any distance or shot size yourself.\n")
    if momentum:
        text += ("MOMENTUM: your last change (%s) moved the picture away from where this line started and was kept. "
                 "Push that SAME change clearly further in the same direction this step.\n" % last_l["hypothesis"][:140])
    text += ("This step must change the %s (or, if you judge another dimension matters more, one of: %s). "
             "The last steps changed: %s. Unless told MOMENTUM above, don't touch those again this step. Make the change CLEARLY visible (not a tweak). "
             "a small, clear step: one thing a viewer notices when the two frames sit side by side, everything else the same.\n" % (free[0], ", ".join(free[1:]), ", ".join(recent) or "nothing yet"))
    text += (("Make a BOLD move: a new idea toward the vision (change the light, the pose, the garment's concept, the camera), not a tweak.\n"
              if move == "bold" else "Make a REFINEMENT: the best frame is working; push the one thing that most holds it back.\n")
             + "First look at the best frame. Name what is most alive in it (keep that), what most holds it back from the vision, "
             "and your hypothesis: \"if <change>, the image will <effect>\". Then write the new craft phrase (at most %d words; "
             "it replaces the current one, keep what works). JSON only: {\"alive\": \"...\", \"holds_back\": \"...\", "
             "\"hypothesis\": \"...\", \"dimension\": \"<the one you changed>\", \"craft\": \"...\", \"change\": \"what you changed, in a few words\", \"guidance\": 2.5-5.0}" % CRAFT_WORDS)
    import re
    i = DISTANCES.index(base_dist) if base_dist in DISTANCES else 2
    dist = DISTANCES[max(0, min(len(DISTANCES) - 1, i + random.choice((-1, 1))))] if step_dist else DISTANCES[i]

    def normalize(raw):
        c = " ".join(str(raw or "").split()).strip().strip(".")      # never truncated: an edit appended at the end must survive
        for x in DISTANCES:                                # an anchor's craft carries its own distance: only this frame's leads
            c = c.replace(x + ", ", "").replace(x, "").strip(", ")
        c = re.sub(r"(?i)\b(extreme |very )?(wide|full[- ]body|three[- ]quarter|medium|close[- ]up|low[- ]angle full[- ]body) shot( from [a-z ]+?)?(,|$)", "", c)
        c = re.sub(r"(?i)\b(her whole body small in the frame|a tiny figure in the landscape)(,|$)", "", c)
        c = re.sub(r"\s*,\s*,+", ",", c).strip(" ,")
        return dist + (", " + c if c else "") if c else ""

    d, craft, extra, why = None, "", "", "no reply"
    for attempt in range(3):                               # retry: an unreadable reply, a repeat, or words identical to the current ones
        try:
            d = _ask(_image(best_path, "The best frame:") + [{"type": "text", "text": text + extra}], max_tokens=450, temperature=0.6 + 0.15 * attempt)
        except Exception:
            d, why = None, "no reply"
            continue
        craft = normalize(d.get("craft"))
        ch = str(d.get("change") or "").strip().lower()
        if not craft:
            why, extra = "no words", "\nYour craft was empty. Write the full craft phrase with your one change applied.\n"
        elif len(craft.split()) - len(dist.split()) > 30:
            why, extra = "too long", "\nYour craft phrase is too long (%d words). Keep your change, and drop or merge older words to stay under 30.\n" % (len(craft.split()) - len(dist.split()))
        elif craft == (coll.get("craft") or ""):
            why, extra = "unchanged", "\nYour craft phrase was IDENTICAL to the current one: you described a change but did not apply it. Write the phrase WITH the change.\n"
        elif ch in [t.lower() for t in tried_here] or craft in (coll.get("crafts_by_base") or {}).get(base_key, []):
            why, extra = "repeat", "\nYou just proposed an edit already tried from this frame (\"%s\"). Choose a DIFFERENT change.\n" % (ch or craft)[:100]
        else:
            break
        craft = ""
    if not d or not craft:
        coll["writer_fail"] = why
        return None
    tb = coll.setdefault("tried_by_base", {}); tb[base_key] = (tb.get(base_key, []) + [str(d.get("change") or craft)[:100]])[-20:]
    cb = coll.setdefault("crafts_by_base", {}); cb[base_key] = (cb.get(base_key, []) + [craft])[-20:]
    for k in list(tb)[:-60]:
        tb.pop(k, None); cb.pop(k, None)
    coll["distances"] = ((coll.get("distances") or []) + [dist])[-6:]
    coll["tried"] = ((coll.get("tried") or []) + [str(d.get("change") or craft)[:120]])[-30:]
    dim = str(d.get("dimension") or "").strip().lower()
    coll["dims"] = ((coll.get("dims") or []) + [dim if dim in DIMENSIONS else free[0]])[-6:]
    coll["insight"] = {"alive": str(d.get("alive") or "")[:200], "holds_back": str(d.get("holds_back") or "")[:200],
                       "hypothesis": (str(d.get("hypothesis") or "").strip() or "if " + str(d.get("change") or craft)[:160] + ", the image moves toward the vision")[:240],
                       "move": move}
    return {"craft": craft, "aim": "vision", "change": str(d.get("change") or "")[:160], "hypothesis": coll["insight"]["hypothesis"],
            "move": move, "guidance": _g(d.get("guidance"), coll)}
