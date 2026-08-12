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

PROMOTION REQUIRES EVIDENCE, NOT ENTHUSIASM. Keep is the retention floor, not a
vote for succession. A challenger must preserve the anchor's keep-rate and beat
its rate of arresting frames on sheets where the movement actually progressed.
A raw rate over four frames is noise, and noise is how "this is better" shipped
six times against a wall that was not better.

THE JUDGE DOES NOT WRITE THE LAWS AND THE PROPOSER DOES NOT JUDGE. The governor
scores sheets in sentinel.py and proposes candidates here, but a proposal only
survives by out-scoring the anchor on a later sheet it did not choose.

THE HUMAN HOLDS THE ANCHOR. Nothing here writes an operator_anchor, and a
regression against the last one forces exploration to zero rather than
negotiating with it.
"""
import argparse
import hashlib
import json
import math
import os
import pathlib
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

import governor

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
WINDOW = 24           # one visual-language cohort; never blend style epochs
EVIDENCE_ROUNDS = 40  # bounded, contemporary evidence rather than the archive
MIN_CHANGE_WINS = 2   # one lucky arresting frame is not a new direction
KEEP_MARGIN = 0.10    # exploration may bend, but not discard, the beauty floor

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


def candidate_id(candidate):
    if candidate.get("id"):
        return str(candidate["id"])
    material = "|".join(str(candidate.get(k, "")) for k in ("seat", "kind", "phrase"))
    return hashlib.sha256(material.encode()).hexdigest()[:12]


def verdict_numbers(values):
    out = set()
    for value in values or []:
        try:
            out.add(int(value))
        except (TypeError, ValueError):
            continue
    return out


def ask_seat(seat, sheet_url, timeout=200, engine=None, feedback=""):
    """One short question to one seat. Short because the gateway times out on
    long prompts against a 31B with speculative decoding, and a 524 here would
    silently stall the whole cycle."""
    prompt = (
        f"{SEATS[seat]}\n\nThis is the current wall. Reply with ONLY a JSON object:\n"
        '{"phrase":"<8-14 words, concrete, something a camera could verify>",'
        '"why":"<8 words>"}\n'
        "No abstractions, no adjectives standing in for the thing itself."
    )
    if feedback:
        prompt += "\n\nThe operator's recent feedback outranks your instinct:\n" + feedback
    content = [{"type": "text", "text": prompt}]
    if sheet_url:
        content.append({"type": "image_url", "image_url": {"url": sheet_url}})
    body = {"model": "governor", "max_tokens": 200,
            "messages": [{"role": "user", "content": content}]}
    req = urllib.request.Request(
        engine or GOVERNOR, json.dumps(body).encode(),
        governor.headers("chorus-hive/1"), method="POST")
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


def operator_feedback(out, limit=12):
    """Read durable human direction; prose guides seats, actions govern state."""
    path = pathlib.Path(out) / "operator-feedback.jsonl"
    rows = []
    for line in path.read_text().splitlines()[-limit:] if path.exists() else []:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def apply_operator_feedback(state, feedback, log):
    """Apply explicit human promote/retire actions before model evidence.

    Most feedback is prose and shapes the next proposals. Direct actions are
    intentionally narrow and attributable by challenger id or exact phrase.
    """
    applied = set(state.get("applied_feedback") or [])
    candidates = state.get("challengers") or []
    for row in feedback:
        key = str(row.get("id") or row.get("ts") or "")
        action = row.get("action")
        if not key or key in applied or action not in ("promote", "retire"):
            continue
        target_id, phrase = row.get("challenger_id"), row.get("phrase")
        match = next((c for c in candidates
                      if (target_id and candidate_id(c) == str(target_id))
                      or (phrase and c.get("phrase") == phrase)), None)
        if match:
            match["state"] = "promoted" if action == "promote" else "retired"
            match["operator_decision"] = {"id": key, "why": row.get("instruction", "")}
            applied.add(key)
            log(f"OPERATOR {action.upper()} {match.get('kind')}: {match.get('phrase')!r}")
    state["applied_feedback"] = sorted(applied)
    return state


def feedback_prompt(feedback):
    lines = []
    for row in feedback:
        instruction = str(row.get("instruction") or row.get("feedback") or "").strip()
        if instruction:
            lines.append(f"- {instruction}")
    return "\n".join(lines[-8:])


def movement_failure_axis(verdict):
    movement = verdict.get("movement") or {}
    axis = movement.get("failure_axis")
    if axis in ("coherence", "surface", "light", "composition", "subject"):
        return axis
    # Compatibility with verdicts written before failure_axis joined the
    # schema. Infer only where the language is unambiguous; otherwise do not
    # turn prose into an unrelated control action.
    text = " ".join((str(movement.get("why") or ""),
                     str(verdict.get("verdict") or ""))).lower()
    if any(word in text for word in ("random", "incoher", "unrelated", "cohes")):
        return "coherence"
    if any(word in text for word in ("texture", "surface", "smooth", "sheen", "medium", "graphic")):
        return "surface"
    if any(word in text for word in ("light", "value", "contrast", "tonal")):
        return "light"
    return None


def adjust_style_hold(out, state, rows, log):
    """Let measured movement tune one declared rate, once per verdict.

    A stalled movement needs longer commitment to a visual language, not more
    simultaneous novelty. Two progressing verdicts earn a slightly faster
    evolution. This never changes subject pools, curation, or multiple axes.
    """
    judged = [row for row in rows
              if row.get("judged") and row.get("verdict") and row.get("window") == WINDOW]
    if not judged:
        return state
    newest = judged[-1]
    stamp = newest.get("ts")
    if stamp is None or stamp == state.get("style_adjusted_for"):
        return state
    control_path = pathlib.Path(out) / "drift-control.json"
    control = load(control_path, {})
    current = int(control.get("style_hold_generations") or 4)
    verdict = newest["verdict"]
    progressing = bool((verdict.get("movement") or {}).get("progressing"))
    failure_axis = movement_failure_axis(verdict)
    prior_progressing = (len(judged) > 1 and
                         bool((judged[-2]["verdict"].get("movement") or {}).get("progressing")))
    changed = current
    if not progressing and failure_axis == "coherence":
        changed = min(12, current + 2)
    elif prior_progressing:
        changed = max(4, current - 1)
    if changed != current:
        control["style_hold_generations"] = changed
        temporary = control_path.with_suffix(".json.part")
        temporary.write_text(json.dumps(control, indent=2, sort_keys=True) + "\n")
        temporary.replace(control_path)
        log(f"movement {'progressing' if progressing else 'stalled'}: "
            f"style hold {current} -> {changed} generations")
    if not progressing and failure_axis in ("surface", "light"):
        control["style_directive"] = {"id": f"verdict-{stamp}", "axis": failure_axis}
        temporary = control_path.with_suffix(".json.part")
        temporary.write_text(json.dumps(control, indent=2, sort_keys=True) + "\n")
        temporary.replace(control_path)
        log(f"movement failed on {failure_axis}: issued one-axis style directive")
    state["style_adjusted_for"] = stamp
    return state


def credit(state, out):
    """Join Sentinel's numbered verdicts to Drift's experimental ledger.

    Each filename is counted once at its newest verdict. That prevents a frame
    sampled on three consecutive sheets from masquerading as three trials and
    keeps the anchor contemporary with the candidates it is compared against.
    """
    attribution = {}
    ledger_path = out / "trial-ledger.jsonl"
    for line in ledger_path.read_text().splitlines() if ledger_path.exists() else []:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("file"):
            attribution[row["file"]] = row

    outcomes = {}
    taste_path = out / "taste-log.jsonl"
    lines = taste_path.read_text().splitlines()[-EVIDENCE_ROUNDS:] if taste_path.exists() else []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if (not row.get("judged") or not row.get("sampled_frames")
                or row.get("window") != WINDOW):
            continue
        frames = row["sampled_frames"]
        verdict = row.get("verdict") or {}
        arresting = verdict_numbers(a.get("frame") for a in (verdict.get("arresting") or []))
        keep = verdict_numbers(verdict.get("keep")) | arresting
        cut = verdict_numbers(verdict.get("cut"))
        progressing = bool((verdict.get("movement") or {}).get("progressing"))
        for number in keep | cut:
            name = frames.get(str(number))
            if name and name in attribution:
                outcomes[name] = {
                    "kept": number in keep,
                    "arresting": number in arresting,
                    "progressing": progressing,
                    # This is deliberately conjunctive. Novelty alone is not
                    # beauty, and a good frame in a stalled sheet is not proof
                    # that the proposed change moved the body of work.
                    "change_win": progressing and number in arresting,
                }

    anchor = {name: result for name, result in outcomes.items()
              if attribution[name].get("arm") == "anchor"}
    state["anchor_frames"] = sorted(anchor)
    state["baseline"] = (sum(r["kept"] for r in anchor.values()) / len(anchor)
                         if anchor else None)
    state["change_baseline"] = (sum(r["change_win"] for r in anchor.values()) / len(anchor)
                                if anchor else None)
    state["arresting_baseline"] = (sum(r["arresting"] for r in anchor.values()) / len(anchor)
                                   if anchor else None)
    for candidate in state.get("challengers") or []:
        candidate["id"] = candidate_id(candidate)
        if candidate.get("state") != "trial":
            continue
        frames = {name: result for name, result in outcomes.items()
                  if attribution[name].get("arm") == "trial"
                  and attribution[name].get("challenger_id") == candidate["id"]}
        candidate["frames"] = sorted(frames)
        candidate["keeps"] = sum(r["kept"] for r in frames.values())
        candidate["arresting"] = sum(r["arresting"] for r in frames.values())
        candidate["progressing"] = sum(r["progressing"] for r in frames.values())
        candidate["change_wins"] = sum(r["change_win"] for r in frames.values())
        candidate["appearances"] = len(frames)
    return state


def settle(state, log, max_promotions=MAX_PROMOTIONS):
    """Promote what beats the anchor, retire what clearly does not, leave the
    undecided on trial. Indecision is not promotion."""
    keep_baseline = state.get("baseline")
    change_baseline = state.get("change_baseline")
    if keep_baseline is None or change_baseline is None:
        log("no keep/change baseline yet; nothing settled")
        return state
    promoted = 0
    for c in state["challengers"]:
        if c["state"] != "trial" or c["appearances"] < MIN_TRIALS:
            continue
        n = c["appearances"]
        keep_lo = wilson_lower(c["keeps"], n)
        keep_hi = 1 - wilson_lower(n - c["keeps"], n)
        wins = int(c.get("change_wins") or 0)
        change_lo = wilson_lower(wins, n)
        change_hi = 1 - wilson_lower(n - wins, n)
        preserves_beauty = keep_lo >= max(0.0, keep_baseline - KEEP_MARGIN)
        earns_change = wins >= MIN_CHANGE_WINS and change_lo > change_baseline
        if preserves_beauty and earns_change and promoted < max_promotions:
            c["state"] = "promoted"
            promoted += 1
            log(f"PROMOTED {c['kind']}: {c['phrase']!r} "
                f"(change {wins}/{n}, lower {change_lo:.2f} > {change_baseline:.2f}; "
                f"keep lower {keep_lo:.2f} >= floor {max(0.0, keep_baseline - KEEP_MARGIN):.2f})")
        elif keep_hi < max(0.0, keep_baseline - KEEP_MARGIN) or change_hi < change_baseline:
            c["state"] = "retired"
            log(f"retired {c['kind']}: {c['phrase']!r} "
                f"(keep {c['keeps']}/{n}, change {wins}/{n})")
    return state


def cycle(args, log):
    out = pathlib.Path(args.out_dir).expanduser()
    state_path = out / "challengers.json"
    state = load(state_path, {"challengers": [], "anchor_frames": [], "eps": EPS_MAX})
    feedback = operator_feedback(out)
    state = apply_operator_feedback(state, feedback, log)
    state = credit(state, out)
    if state.get("baseline") is not None:
        allowed = 2 if int(state.get("streak") or 0) >= STREAK_FOR_DOUBLE else MAX_PROMOTIONS
        state = settle(state, log, allowed)

    # A regression against the anchor stops exploration entirely rather than
    # negotiating with it: the moment of approval is a local maximum, so every
    # direction from it is downhill until proven otherwise.
    verdicts = []
    taste_rows = []
    for line in (out / "taste-log.jsonl").read_text().splitlines()[-40:] if (out / "taste-log.jsonl").exists() else []:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        taste_rows.append(row)
        # A verdict on a nearly empty run is not evidence. Restarts produce
        # sheets of one or two frames, and one of those scored 0.00 and tripped
        # the regression brake against a healthy run.
        judged_frames = len(row.get("verdict", {}).get("keep") or []) + \
                        len(row.get("verdict", {}).get("cut") or [])
        # Protocol rule 7: only compare scores measured over the same window.
        # Archive-wide scores and current-run scores are different quantities,
        # and mixing them is what tripped the brake against a healthy run.
        if (row.get("judged") and row.get("verdict")
                and judged_frames >= MIN_JUDGED and row.get("window") == WINDOW):
            verdict = row["verdict"]
            progressing = bool((verdict.get("movement") or {}).get("progressing"))
            arresting = len(verdict.get("arresting") or [])
            verdicts.append((arresting / judged_frames) if progressing else 0.0)
    state = adjust_style_hold(out, state, taste_rows, log)
    streak = int(state.get("streak") or 0)
    # A single vision pass is an opinion, not a referendum. Require two
    # consecutive regressions against the earlier best before braking, and
    # retain a narrow exploration floor even then so the system can discover
    # its way out rather than fossilise the failing anchor.
    earlier_best = max(verdicts[:-2]) if len(verdicts) >= 3 else None
    confirmed_regression = (earlier_best is not None
                            and verdicts[-1] < earlier_best - 0.15
                            and verdicts[-2] < earlier_best - 0.15)
    if confirmed_regression:
        state["eps"] = EPS_FLOOR / 2
        state["streak"] = 0
        log(f"CONFIRMED REGRESSION {verdicts[-2]:.2f}, {verdicts[-1]:.2f} "
            f"against {earlier_best:.2f}: exploration narrowed, streak reset")
    elif verdicts:
        # A cycle counts as healthy when the newest score holds against the best
        # of the recent past. Holding is enough; it need not improve every time,
        # or noise alone would keep resetting the ratchet.
        healthy = verdicts[-1] > 0 and (
            len(verdicts) < 2 or verdicts[-1] >= max(verdicts[:-1]) - 0.05)
        streak = streak + 1 if healthy else 0
        state["streak"] = streak
        state["eps"] = min(EPS_CEIL, EPS_FLOOR + streak * EPS_STEP)
        log(f"streak {streak} -> eps {state['eps']:.2f}"
            f" (change {verdicts[-1]:.2f}, best {max(verdicts):.2f})")
    else:
        state["eps"] = EPS_FLOOR

    recent = verdicts[-5:]
    prior = verdicts[-10:-5]
    state["measurement"] = {
        "judged_rounds": len(verdicts),
        "metric": "arresting_when_movement_progresses",
        "latest": verdicts[-1] if verdicts else None,
        "recent_mean": sum(recent) / len(recent) if recent else None,
        "prior_mean": sum(prior) / len(prior) if prior else None,
        "delta": ((sum(recent) / len(recent)) - (sum(prior) / len(prior)))
                 if recent and prior else None,
        "updated": time.time(),
    }

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
            guidance = feedback_prompt(feedback)
            futures = {pool.submit(ask_seat, seat, sheet_url, 200, ENGINES[i], guidance): seat
                       for i, seat in enumerate(chosen)}
            for fut in as_completed(futures):
                results.append(fut.result())
        for proposal, error in results:
            if proposal:
                proposal.update(state="trial", keeps=0, appearances=0, frames=[],
                                proposed_at=time.time())
                proposal["id"] = candidate_id(proposal)
                state["challengers"].append(proposal)
                log(f"proposed [{proposal['seat']}] {proposal['phrase']!r} -- {proposal['why']}")
            else:
                log(f"no proposal ({error})")

    save(state_path, state)
    active = [c["phrase"] for c in state["challengers"] if c["state"] in ("trial", "promoted")]
    log(f"eps={state['eps']:.2f} trialing={len(trialing)} active={len(active)} "
        f"baseline={state.get('baseline')}")
    return state


def pid_alive(path):
    try:
        pid = int(pathlib.Path(path).read_text().strip())
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def judge_health():
    endpoint = os.environ.get("CHORUS_SECOND_ENGINE", "")
    if not endpoint:
        return True
    parsed = urllib.parse.urlparse(endpoint)
    if parsed.hostname not in ("127.0.0.1", "localhost"):
        return True
    url = f"{parsed.scheme}://{parsed.hostname}:{parsed.port or 80}/health"
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError):
        return False


def stale(path, seconds):
    try:
        return time.time() - pathlib.Path(path).stat().st_mtime > seconds
    except OSError:
        return True


def audit(args):
    run = pathlib.Path(args.run_dir).expanduser()
    required = ["piper", "nexus", "serve", "gemma", "drift", "sentinel"]
    if (run / "r2sync.pid").exists():
        required.append("r2sync")
    dead = [name for name in required if not pid_alive(run / f"{name}.pid")]
    if dead:
        return "dead service: " + ", ".join(dead)
    out = pathlib.Path(args.out_dir).expanduser()
    if stale(out / "drift-status.json", args.drift_stale):
        return "drift stopped producing"
    if stale(out / "taste-log.jsonl", args.sentinel_stale):
        return "sentinel stopped judging"
    if (run / "r2sync.pid").exists():
        status_path = out / "r2-status.json"
        if stale(status_path, args.r2_stale):
            return "R2 durability stream stopped proving life"
        status = load(status_path, {})
        last_success = float(status.get("last_success_at") or 0)
        if last_success and time.time() - last_success > args.r2_stale:
            return "R2 durability stream has not completed a healthy sweep"
    if not judge_health():
        return "visionary endpoint failed health"
    return None


def restart_stack(args, log, reason):
    log("SUPERVISION FAILURE: " + reason + "; restarting chorus")
    destination = pathlib.Path.home() / "watchdog.log"
    stream = destination.open("a")
    subprocess.Popen(
        ["bash", str(pathlib.Path(args.restart_script).expanduser())],
        stdout=stream, stderr=subprocess.STDOUT, env=os.environ.copy(),
        start_new_session=True,
    )


def supervise(args, log):
    # Give a fresh stack time to load both large models and make its first
    # frame before absence becomes failure.
    time.sleep(args.supervision_grace)
    while True:
        problem = audit(args)
        if problem:
            restart_stack(args, log, problem)
            # The detached restart will replace this Hive. Exit immediately so
            # the outer fuse sees one unambiguous owner rather than two.
            os._exit(2)
        time.sleep(args.audit_interval)


def main():
    ap = argparse.ArgumentParser(description="Propose, trial and promote language changes.")
    ap.add_argument("--out-dir", default=str(pathlib.Path.home() / "models" / "flux-output"))
    ap.add_argument("--public-base", default="")
    ap.add_argument("--interval", type=float, default=1800)
    ap.add_argument("--run-dir", default=str(pathlib.Path.home() / ".flux-run"))
    ap.add_argument("--restart-script", default=str(HERE / "up.sh"))
    ap.add_argument("--audit-interval", type=float, default=15)
    ap.add_argument("--supervision-grace", type=float, default=120)
    ap.add_argument("--drift-stale", type=float, default=180)
    ap.add_argument("--sentinel-stale", type=float, default=960)
    ap.add_argument("--r2-stale", type=float, default=180)
    ap.add_argument("--no-supervise", action="store_true")
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()

    def log(message):
        line = f"{time.strftime('%H:%M:%S')} {message}"
        print(line, flush=True)
        with (pathlib.Path(args.out_dir).expanduser() / "hive-log.txt").open("a") as f:
            f.write(line + "\n")

    if not args.once and not args.no_supervise:
        threading.Thread(target=supervise, args=(args, log), daemon=True).start()

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
