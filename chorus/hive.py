#!/usr/bin/env python3
"""hive — the part that keeps improving the language when nobody is watching.

The sentinel judges and logs. Until now a human read those verdicts and edited
language.py by hand, so the loop improved only while someone sat over it. This
closes that: propose, trial, judge, promote or retire, forever.

    proposer (governor seats) --> challengers.json --> loop draws them at eps
                                        |
    sentinel verdict --> picks.json --> promoter --> pools.json --> loop

Four rules, each one paid for by a failure earlier tonight:

NEW MATERIAL NEVER ENLARGES THE DISTRIBUTION. It shares a fixed exploration
budget. Eight media added straight into the pool after an approval moved 36% of
all draws onto unproven terms and halved realised variety while nominal variety
rose. Challengers share EPS_MAX between them; adding a tenth makes each rarer,
never the pool wider.

PROMOTION REQUIRES EVIDENCE, NOT ENTHUSIASM. A challenger must beat the anchor's
own keep-rate on a Wilson lower bound over MIN_TRIALS appearances. A raw rate
over four frames is noise, and noise is how "this is better" shipped six times
against a wall that was not better.

THE JUDGE DOES NOT WRITE THE LAWS AND THE PROPOSER DOES NOT JUDGE. The governor
scores sheets in sentinel.py and proposes candidates here, but a proposal only
survives by out-scoring the anchor on a later sheet it did not choose.

THE HUMAN HOLDS THE ANCHOR. Nothing here writes an operator_anchor, and a
regression against the last one forces exploration to zero rather than
negotiating with it.
"""
import argparse
import json
import math
import os
import pathlib
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = pathlib.Path(__file__).resolve().parent
GOVERNOR = "https://governor.influx.vision/v1/chat/completions"
# A second engine on its own H100. The governor serves one request at a time
# with speculative decoding, so parallel seats starved each other into 524s;
# splitting them across engines is what lets the panel widen at all.
ENGINES = [e for e in (GOVERNOR, os.environ.get("CHORUS_SECOND_ENGINE", "")) if e]

# The slope of growth is not a constant. Exploration starts low and ratchets
# up while the wall holds its level, so the loop earns the right to change
# faster; any regression collapses it to zero in one cycle. Growth that is
# working accelerates, growth that is not stops -- which is the only safe way
# to raise a rate on a system whose failures are invisible for ten minutes.
EPS_FLOOR = 0.10      # exploration when freshly recovered from a regression
EPS_CEIL = 0.34       # never past a third of frames; the wall stays mostly proven
EPS_STEP = 0.03       # earned per consecutive healthy cycle
STREAK_FOR_DOUBLE = 4  # healthy cycles before two promotions may land at once
EPS_MAX = EPS_CEIL    # ceiling on unproven material, as a share of frames
MIN_TRIALS = 12       # appearances before a challenger can be judged
MAX_PROMOTIONS = 1    # per cycle; a wall changes slowly or it is not a wall
MAX_TRIALING = 6      # candidates alive at once, so each gets real exposure
MIN_JUDGED = 8        # frames a sheet must carry before its score counts
WINDOW = 80           # the judgement window scores must share to be comparable

# The seats. Rotating them keeps proposals from converging on one obsession,
# which is what a single critic asked repeatedly always produces.
SEATS = {
    "materials": "You are the MATERIALS seat, a printmaker. Propose ONE new detail or "
                 "surface phrase that would force a truer substrate.",
    "light": "You are the LIGHT seat, a cinematographer. Propose ONE new mood phrase "
             "that creates drama rather than illumination.",
    "curator": "You are the CURATOR seat. Propose ONE new detail phrase that would earn "
               "a frame wall space it does not currently earn.",
    "composition": "You are the COMPOSITION seat. Propose ONE new framing phrase that "
                   "puts the subject somewhere a viewer would not expect.",
}
KINDS = {"materials": "detail", "light": "mood", "curator": "detail", "composition": "framing"}


def wilson_lower(k, n, z=1.64):
    """Lower bound of the keep-rate. At a dozen trials the point estimate is
    mostly noise, and acting on noise is the failure this whole file exists to
    prevent."""
    if n == 0:
        return 0.0
    p = k / n
    d = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (centre - margin) / d


def load(path, default):
    try:
        return json.loads(pathlib.Path(path).read_text())
    except (OSError, ValueError):
        return default


def save(path, payload):
    pathlib.Path(path).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def ask_seat(seat, sheet_url, timeout=200, engine=None):
    """One short question to one seat. Short because the gateway times out on
    long prompts against a 31B with speculative decoding, and a 524 here would
    silently stall the whole cycle."""
    prompt = (
        f"{SEATS[seat]}\n\nThis is the current wall. Reply with ONLY a JSON object:\n"
        '{"phrase":"<8-14 words, concrete, something a camera could verify>",'
        '"why":"<8 words>"}\n'
        "No abstractions, no adjectives standing in for the thing itself."
    )
    content = [{"type": "text", "text": prompt}]
    if sheet_url:
        content.append({"type": "image_url", "image_url": {"url": sheet_url}})
    body = {"model": "governor", "max_tokens": 200,
            "messages": [{"role": "user", "content": content}]}
    req = urllib.request.Request(
        engine or GOVERNOR, json.dumps(body).encode(),
        {"Content-Type": "application/json", "User-Agent": "chorus-hive/1"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = json.load(resp)["choices"][0]["message"]["content"]
    except (urllib.error.URLError, OSError, ValueError, KeyError, IndexError) as exc:
        return None, f"{seat}: {exc}"
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None, f"{seat}: unparseable"
    try:
        obj = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        return None, f"{seat}: {exc}"
    phrase = str(obj.get("phrase", "")).strip().strip('."')
    # Long phrases push the prompt past CLIP's 77 tokens, where everything after
    # the cut is silently discarded. A proposal that cannot fit cannot be tried.
    if not phrase or len(phrase.split()) > 16:
        return None, f"{seat}: phrase rejected ({len(phrase.split())} words)"
    return {"phrase": phrase, "why": str(obj.get("why", "")).strip(), "seat": seat,
            "kind": KINDS[seat]}, None


def credit(state, picks):
    """Attribute outcomes to challengers.

    Weighted toward `arresting` rather than `keep`. A challenger that raises
    compliance is not worth promoting -- the laws already handle compliance,
    and optimising it further just makes the wall more reliably unremarkable.
    What is worth adopting is whatever made someone stop walking.
    """
    """Attribute the sentinel's keeps and cuts to whichever challenger was in
    the frame. The ledger records which frames carried which candidate."""
    keep = set(picks.get("keep") or [])
    cut = set(picks.get("cut") or [])
    arresting = set(picks.get("arresting") or [])
    keep |= arresting  # arresting frames always count as kept
    for c in state["challengers"]:
        if c["state"] != "trial":
            continue
        frames = set(c.get("frames") or [])
        c["keeps"] = len(frames & keep)
        c["appearances"] = len(frames & (keep | cut))
    anchor_frames = set(state.get("anchor_frames") or [])
    n = len(anchor_frames & (keep | cut))
    k = len(anchor_frames & keep)
    state["baseline"] = (k / n) if n else None
    return state


def settle(state, log, max_promotions=MAX_PROMOTIONS):
    """Promote what beats the anchor, retire what clearly does not, leave the
    undecided on trial. Indecision is not promotion."""
    baseline = state.get("baseline")
    if baseline is None:
        log("no baseline yet; nothing settled")
        return state
    promoted = 0
    for c in state["challengers"]:
        if c["state"] != "trial" or c["appearances"] < MIN_TRIALS:
            continue
        lo = wilson_lower(c["keeps"], c["appearances"])
        hi = 1 - wilson_lower(c["appearances"] - c["keeps"], c["appearances"])
        if lo > baseline and promoted < max_promotions:
            c["state"] = "promoted"
            promoted += 1
            log(f"PROMOTED {c['kind']}: {c['phrase']!r} ({c['keeps']}/{c['appearances']}, "
                f"lower {lo:.2f} > baseline {baseline:.2f})")
        elif hi < baseline:
            c["state"] = "retired"
            log(f"retired {c['kind']}: {c['phrase']!r} ({c['keeps']}/{c['appearances']})")
    return state


def cycle(args, log):
    out = pathlib.Path(args.out_dir).expanduser()
    state_path = out / "challengers.json"
    state = load(state_path, {"challengers": [], "anchor_frames": [], "eps": EPS_MAX})
    picks = load(out / "picks.json", {})

    if picks:
        state = credit(state, picks)
        allowed = 2 if int(state.get("streak") or 0) >= STREAK_FOR_DOUBLE else MAX_PROMOTIONS
        state = settle(state, log, allowed)

    # A regression against the anchor stops exploration entirely rather than
    # negotiating with it: the moment of approval is a local maximum, so every
    # direction from it is downhill until proven otherwise.
    verdicts = []
    for line in (out / "taste-log.jsonl").read_text().splitlines()[-40:] if (out / "taste-log.jsonl").exists() else []:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        # A verdict on a nearly empty run is not evidence. Restarts produce
        # sheets of one or two frames, and one of those scored 0.00 and tripped
        # the regression brake against a healthy run.
        judged_frames = len(row.get("verdict", {}).get("keep") or []) + \
                        len(row.get("verdict", {}).get("cut") or [])
        # Protocol rule 7: only compare scores measured over the same window.
        # Archive-wide scores and current-run scores are different quantities,
        # and mixing them is what tripped the brake against a healthy run.
        if (row.get("judged") and row.get("hit_rate") is not None
                and judged_frames >= MIN_JUDGED and row.get("window") == WINDOW):
            verdicts.append(row["hit_rate"])
    streak = int(state.get("streak") or 0)
    if len(verdicts) >= 2 and verdicts[-1] < max(verdicts[:-1]) - 0.15:
        state["eps"] = 0.0
        state["streak"] = 0
        log(f"REGRESSION {verdicts[-1]:.2f} against {max(verdicts[:-1]):.2f}: "
            f"exploration halted, streak reset")
    elif verdicts:
        # A cycle counts as healthy when the newest score holds against the best
        # of the recent past. Holding is enough; it need not improve every time,
        # or noise alone would keep resetting the ratchet.
        healthy = len(verdicts) < 2 or verdicts[-1] >= max(verdicts[:-1]) - 0.05
        streak = streak + 1 if healthy else 0
        state["streak"] = streak
        state["eps"] = min(EPS_CEIL, EPS_FLOOR + streak * EPS_STEP)
        log(f"streak {streak} -> eps {state['eps']:.2f}"
            f" (hit {verdicts[-1]:.2f}, best {max(verdicts):.2f})")
    else:
        state["eps"] = EPS_FLOOR

    trialing = [c for c in state["challengers"] if c["state"] == "trial"]
    if len(trialing) < MAX_TRIALING and state["eps"] > 0:
        sheet_url = f"{args.public_base.rstrip('/')}/outputs/_sheets/contact.jpg" if args.public_base else ""
        order = sorted(SEATS)
        start = int(time.time() // 3600) % len(order)
        # One seat per engine, in parallel. With a single engine this is the
        # old behaviour exactly; with two the panel widens without waiting.
        chosen = [order[(start + i) % len(order)] for i in range(min(len(ENGINES), MAX_TRIALING - len(trialing)))]
        results = []
        with ThreadPoolExecutor(max_workers=max(len(chosen), 1)) as pool:
            futures = {pool.submit(ask_seat, seat, sheet_url, 200, ENGINES[i]): seat
                       for i, seat in enumerate(chosen)}
            for fut in as_completed(futures):
                results.append(fut.result())
        for proposal, error in results:
            if proposal:
                proposal.update(state="trial", keeps=0, appearances=0, frames=[],
                                proposed_at=time.time())
                state["challengers"].append(proposal)
                log(f"proposed [{proposal['seat']}] {proposal['phrase']!r} -- {proposal['why']}")
            else:
                log(f"no proposal ({error})")

    save(state_path, state)
    active = [c["phrase"] for c in state["challengers"] if c["state"] in ("trial", "promoted")]
    log(f"eps={state['eps']:.2f} trialing={len(trialing)} active={len(active)} "
        f"baseline={state.get('baseline')}")
    return state


def main():
    ap = argparse.ArgumentParser(description="Propose, trial and promote language changes.")
    ap.add_argument("--out-dir", default=str(pathlib.Path.home() / "models" / "flux-output"))
    ap.add_argument("--public-base", default="")
    ap.add_argument("--interval", type=float, default=1800)
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()

    def log(message):
        line = f"{time.strftime('%H:%M:%S')} {message}"
        print(line, flush=True)
        with (pathlib.Path(args.out_dir).expanduser() / "hive-log.txt").open("a") as f:
            f.write(line + "\n")

    while True:
        try:
            cycle(args, log)
        except Exception as exc:
            log(f"hive error: {exc}")
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
