#!/usr/bin/env python3
"""sentinel — the loop's eyes.

Chorus could always be judged; nothing ever made it happen. Six rounds of
changes shipped on the strength of the newest frame, and the collapses that
mattered — everything a solitary structure in a cold landscape, three identical
perspective corridors, four interchangeable pomegranates — were only ever
visible across a sample, never in the frame in front of you.

So judgement stops being something someone remembers to do. The sentinel
samples the run, shows it to a model with eyes, scores it against LAWS.md, and
writes the verdict down whether it flatters the current language or not.

    chorus/sentinel.py --once      one verdict, printed
    chorus/sentinel.py             watch, judging every --interval seconds

The judge is the governor: it already holds this estate's standing intent, it
is multimodal, and its endpoint is OpenAI-shaped. If it is unreachable the
sentinel records that it could not see rather than passing the round -- an
unjudged generation must never look like an approved one.
"""
import argparse
import base64
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
GOVERNOR = "https://governor.influx.vision/v1/chat/completions"
# Every judgement was 524-ing: the governor serves one request at a time with
# speculative decoding, and a vision call over a contact sheet exceeds the
# gateway timeout reliably. A second engine on its own H100 answers the same
# question in time. Try each in turn -- an unjudged round is the failure this
# file exists to prevent, so it must not depend on one busy engine.
ENGINES = [e for e in (os.environ.get("CHORUS_SECOND_ENGINE", ""), GOVERNOR) if e]

# How far back a judgement reaches. Wide enough that a lucky generation cannot
# carry a round, narrow enough that the score tracks the language now running.
# One movement cohort. At four frames per generation and a visual-language hold
# of roughly ten generations, 24 frames are broad enough to resist one lucky
# image without blending several different style epochs into a "random set".
RECENT_WINDOW = 24

# Asking for prose gets prose. The judge is pinned to a shape so the verdict is
# comparable across rounds and can steer the loop without a human reading it.
def _law_count():
    """Derive the range from the file. It was hardcoded to 1-10 while LAWS.md
    had eleven, so the judge could not cite law 11 -- the governor's guardrail
    against exactly the post-approval regression that then happened."""
    try:
        text = (HERE / "LAWS.md").read_text()
    except OSError:
        return 10
    import re
    nums = [int(m) for m in re.findall(r"^(\d+)\.\s", text, re.M)]
    return max(nums) if nums else 10


# Every law is a prohibition, so law-compliance measures the absence of faults
# and nothing else: a frame that breaks no law and stirs nobody scores
# perfectly. That is a machine for manufacturing competence, and competence is
# exactly what the operator kept calling awful. `arresting` is the only
# affirmative question in the system -- asked separately, because a frame can
# be flawless and dead, or flawed and the one you cannot look away from.
SCHEMA = """Reply with ONLY a JSON object, no prose, no markdown fence:
{"keep":[<frame numbers worth wall space>],
 "cut":[<frame numbers that are not>],
 "arresting":[{"frame":<n>,"why":"<what stops you, 10 words, concrete>"}],
 "dead":[<frames that break no rule and are still lifeless>],
 "laws_broken":[{"law":<1-LAWMAX>,"frames":[<n>],"why":"<8 words>"}],
 "movement":{"progressing":<true|false>,"failure_axis":"none|coherence|surface|light|composition|subject","repeated_frames":[<n>],"why":"<10 words>"},
 "dominant_motif":"<the composition or subject recurring across unrelated frames, or none>",
 "verdict":"<one sentence a curator would say>"}

`arresting` is not `keep`. Keep asks whether a frame is good enough to hang.
Arresting asks whether you would stop walking. Most sheets have none, and
saying none is the honest answer -- do not promote a competent frame into it.
`dead` is the opposite and matters as much: name the frames that satisfy every
rule and are still not worth a second of anyone's attention.""".replace("LAWMAX", str(_law_count()))

# One-axis calibration. Gemma was correctly detecting stalled movement, then
# using that run-level criticism to cut individual frames which remained worth
# retaining. Preserve its strict material eye; separate archival value from
# novelty so repetition affects prominence, not existence.
CALIBRATION = """Decision boundary:
- KEEP means retain in the collection. A coherent, beautiful, or useful
  variation belongs in keep even when it does not advance the movement.
- KEEP IS NOT APPROVAL OF A GENERATOR CHANGE. It is deliberately lenient about
  survival and must never be read as permission to steer future work.
- ARRESTING is the higher bar for prominence. Repetition or familiarity may
  keep a frame from arresting without making it a cut.
- CUT only a frame that fails as an individual image: incoherent construction,
  unusable value, broken material logic, severe artifact, or no visual value.
  Bland is not automatically broken. Competent is not automatically disposable.
- Laws 6, 7, 8, and 10 grade the MOVEMENT across the sheet. Report those faults
  in movement, dominant_motif, and laws_broken. They may not, by themselves,
  place an otherwise worthwhile individual frame in cut.
- Set movement.progressing true only when this sheet preserves the approved
  anchor's distinctive beauty AND develops it coherently. Mere variety,
  technical competence, unrelated good pictures, or a new style is false.
- When movement fails, name its single primary failure_axis. `coherence` means
  the pictures do not form a movement; `surface` means their material voice
  drifted; the other axis names are literal. Use `none` only when progressing.
- Put every numbered frame in exactly one of keep or cut. Arresting should be a
  subset of keep. Dead may still be kept as evidence or a useful variation."""


def build_sheet(out_dir, sheet, n, recent=RECENT_WINDOW):
    """Judge the current run, not the archive.

    The wall holds every frame ever made, which is right -- it is the body of
    work. But contact.py samples evenly across whatever it is given, so once
    the archive was restored the gate began grading four hours of superseded
    output and reporting it as today's score. It cited "substitutable satellite
    dishes" from the first broken generator. A gate that measures history
    cannot steer the present.
    """
    subprocess.run(
        [sys.executable, str(HERE / "contact.py"),
         "--dir", str(out_dir), "--out", str(sheet), "--n", str(n),
         "--recent", str(recent)],
        check=False, capture_output=True, timeout=180,
    )
    return sheet.exists()


def sheet_reference(sheet, public_base=""):
    """How the judge gets the picture.

    Prefer a URL: the node already serves the sheet, and Cloudflare's WAF
    refuses the ~500KB POST that inlining the bytes produces -- a 403 that
    reads exactly like an auth failure and cost an hour to tell apart from one.
    Base64 stays as the fallback for a node with no public endpoint, downscaled
    to stay under the limit.
    """
    if public_base:
        return f"{public_base.rstrip('/')}/outputs/_sheets/{sheet.name}"
    data = sheet.read_bytes()
    if len(data) > 180_000:
        try:
            from PIL import Image
            im = Image.open(sheet)
            im.thumbnail((768, 768))
            small = sheet.with_name(f"_{sheet.stem}_small.jpg")
            im.save(small, quality=60)
            data = small.read_bytes()
        except Exception:
            pass
    return "data:image/jpeg;base64," + base64.b64encode(data).decode()


def approved_anchor(out_dir):
    """Return the newest operator-approved visual anchor that still exists."""
    log = pathlib.Path(out_dir) / "taste-log.jsonl"
    lines = log.read_text().splitlines() if log.exists() else []
    for line in reversed(lines):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("kind") != "operator_anchor":
            continue
        path = pathlib.Path(row.get("sheet") or "").expanduser()
        if path.is_file():
            return path
    return None


def ask_governor(sheet, laws, timeout, public_base="", anchor=None):
    """Ask each engine in turn until one answers. Returns the parsed verdict,
    or None if none could be obtained -- never a default-pass."""
    errors = []
    for engine in ENGINES:
        verdict, error = _ask_one(engine, sheet, laws, timeout, public_base, anchor)
        if verdict is not None:
            return verdict, None
        errors.append(error)
    return None, "; ".join(errors)


def _ask_one(engine, sheet, laws, timeout, public_base="", anchor=None):
    reference = sheet_reference(sheet, public_base)
    content = [
        {"type": "text", "text":
            "You are the eye-gate for a live generative art wall. The first image is "
            "the current contact sheet, sampled evenly and numbered left to right, "
            "top to bottom.\n\nJudge it against these laws:\n\n" + laws + "\n\n" +
            CALIBRATION + "\n\n" + SCHEMA},
        {"type": "image_url", "image_url": {"url": reference}},
    ]
    if anchor:
        content.extend([
            {"type": "text", "text":
                "The next image is the operator-approved beauty anchor. Do not grade its "
                "numbered cells. Use it only to decide whether the current movement preserves "
                "its distinctive beauty while genuinely developing it."},
            {"type": "image_url", "image_url": {"url": sheet_reference(anchor)}},
        ])
    body = {
        "model": "governor",
        "max_tokens": 420,
        "messages": [{
            "role": "user",
            "content": content,
        }],
    }
    # Cloudflare fronts the governor and refuses urllib's default agent with a
    # 403, which looks exactly like an auth failure and is not one.
    req = urllib.request.Request(
        engine, json.dumps(body).encode(),
        {"Content-Type": "application/json",
         "Accept": "application/json",
         "User-Agent": "chorus-sentinel/1 (+flux)"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.load(resp)
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as exc:
        return None, f"governor unreachable: {exc}"
    try:
        text = payload["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError):
        return None, "governor returned no content"
    # Models fence JSON even when told not to; take the outermost object.
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None, f"unparseable verdict: {text[:120]}"
    try:
        return json.loads(text[start:end + 1]), None
    except json.JSONDecodeError as exc:
        return None, f"unparseable verdict: {exc}"


def steer(verdict, control_path):
    """Turn a verdict into a change the loop will feel next generation.

    Deliberately narrow: the sentinel may slow the loop down when the wall is
    failing, and it records what it saw. It does not rewrite the language --
    that stays a human decision, because a judge that edits its own criteria
    stops being a judge.
    """
    kept = len(verdict.get("keep") or [])
    cut = len(verdict.get("cut") or [])
    total = kept + cut
    if not total:
        return None
    hit_rate = kept / total
    try:
        control = json.loads(pathlib.Path(control_path).read_text())
    except (OSError, ValueError):
        control = {}
    # Originally: a failing wall should not race to produce more of it. That
    # reasoning was wrong. The throttle cost a third of the duty cycle on an
    # H100 that bills by the minute, and it slowed down the only process that
    # generates the evidence needed to stop failing. Frames are cheap and the
    # wall is paged and ranked now, so a weak frame costs storage, not
    # attention.
    # Taste may redirect the language, but it must not turn a severe opinion
    # into an idle H100. More frames are the evidence needed to decide whether
    # the judgement was a real regression or one critic having a hard round.
    control["sleep"] = 0
    pathlib.Path(control_path).write_text(json.dumps(control, indent=2, sort_keys=True) + "\n")
    return hit_rate


def blanket_rejection(verdict, frame_count):
    keep = verdict.get("keep") or []
    cut = verdict.get("cut") or []
    # Below one work in four, the useful information is the critique and the
    # few positives—not a bulk classification of everything else as waste.
    return (len(keep) / max(frame_count, 1) < 0.25
            and len(cut) >= max(8, int(frame_count * 0.70)))


def publish_picks(out_dir, sheet, verdict, record_cuts=True):
    """Turn frame numbers into a ranking the wall can use.

    The panel was already sitting in the loop; its verdicts just never reached
    the presentation, so the gallery ordered 1,184 frames by modification time
    and buried the judged ones. picks.json accumulates across rounds: a frame
    kept once stays kept, because a later sheet simply may not have sampled it.
    """
    try:
        manifest = json.loads((sheet.parent / "manifest.json").read_text())
    except (OSError, ValueError):
        return None
    frames = manifest.get("frames") or {}
    picks_path = out_dir / "picks.json"
    try:
        picks = json.loads(picks_path.read_text())
    except (OSError, ValueError):
        picks = {"keep": [], "cut": []}
    keep, cut = set(picks.get("keep") or []), set(picks.get("cut") or [])
    arrest = set(picks.get("arresting") or [])
    for a in verdict.get("arresting") or []:
        name = frames.get(str(a.get("frame")))
        if name:
            arrest.add(name)
            keep.add(name)
    for n in verdict.get("keep") or []:
        name = frames.get(str(n))
        if name:
            keep.add(name)
            cut.discard(name)
    if record_cuts:
        for n in verdict.get("cut") or []:
            name = frames.get(str(n))
            if name and name not in keep:
                cut.add(name)
    picks_path.write_text(json.dumps(
        {"keep": sorted(keep), "cut": sorted(cut), "arresting": sorted(arrest),
         "updated": time.time()}, indent=2) + "\n")
    return len(keep)


def publish_panel_decisions(out_dir):
    """Materialise the panel's latest decision per image for human review."""
    decisions = {}
    log_path = pathlib.Path(out_dir) / "taste-log.jsonl"
    lines = log_path.read_text().splitlines()[-40:] if log_path.exists() else []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not row.get("judged") or not row.get("sampled_frames"):
            continue
        verdict = row.get("verdict") or {}
        frames = row["sampled_frames"]
        count = int(row.get("frames") or len(frames) or 16)
        advisory = row.get("blanket_rejection")
        if advisory is None:
            advisory = blanket_rejection(verdict, count)
        keep = {int(n) for n in (verdict.get("keep") or [])}
        cut = {int(n) for n in (verdict.get("cut") or [])}
        arresting = {int(a.get("frame")): a.get("why", "")
                     for a in (verdict.get("arresting") or []) if a.get("frame")}
        laws = {}
        for broken in verdict.get("laws_broken") or []:
            for number in broken.get("frames") or []:
                try:
                    key = int(number)
                except (TypeError, ValueError):
                    continue
                laws.setdefault(key, []).append(
                    f"Law {broken.get('law')} · {broken.get('why', '')}")
        for raw_number, name in frames.items():
            try:
                number = int(raw_number)
            except (TypeError, ValueError):
                continue
            # Human removals live outside the served output directory. Keep
            # their historical verdict in taste-log, but never resurrect a
            # removed work into the active review surface.
            if not (pathlib.Path(out_dir) / name).exists():
                continue
            if number in arresting:
                decision = "arresting"
            elif number in keep:
                decision = "keep"
            elif number in cut:
                decision = "advisory_cut" if advisory else "cut"
            else:
                decision = "unmarked"
            reasons = list(laws.get(number) or [])
            if arresting.get(number):
                reasons.insert(0, arresting[number])
            decisions[name] = {
                "name": name,
                "path": "/outputs/" + name,
                "decision": decision,
                "reasons": reasons,
                "verdict": verdict.get("verdict", ""),
                "dominant_motif": verdict.get("dominant_motif", "none"),
                "advisory": bool(advisory),
                "judged_at": row.get("ts"),
            }
    order = {"arresting": 0, "keep": 1, "advisory_cut": 2, "cut": 3, "unmarked": 4}
    items = sorted(decisions.values(),
                   key=lambda item: (order.get(item["decision"], 9), -(item.get("judged_at") or 0)))
    payload = {"updated": time.time(), "items": items,
               "counts": {key: sum(i["decision"] == key for i in items) for key in order}}
    target = pathlib.Path(out_dir) / "panel-decisions.json"
    temporary = target.with_suffix(".json.part")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    temporary.replace(target)
    return payload


def judge_once(args):
    out_dir = pathlib.Path(args.out_dir).expanduser()
    sheet = out_dir / "_sheets" / "contact.jpg"
    laws = (HERE / "LAWS.md").read_text()

    if not build_sheet(out_dir, sheet, args.n, args.recent):
        print("no frames to judge yet", flush=True)
        return None

    anchor = approved_anchor(out_dir)
    verdict, error = ask_governor(sheet, laws, args.timeout, args.public_base, anchor)
    row = {"ts": time.time(), "frames": args.n, "window": args.recent, "sheet": str(sheet)}
    if anchor:
        row["operator_anchor"] = str(anchor)
    try:
        manifest = json.loads((sheet.parent / "manifest.json").read_text())
        row["sampled_frames"] = manifest.get("frames") or {}
    except (OSError, ValueError):
        row["sampled_frames"] = {}
    if verdict is None:
        # Recorded as a miss, not a pass. An unjudged round must never be
        # mistaken later for an approved one.
        row.update(judged=False, error=error)
        print(f"NOT JUDGED: {error}", flush=True)
    else:
        harsh = blanket_rejection(verdict, args.n)
        hit = steer(verdict, out_dir / "drift-control.json")
        # A blanket rejection is criticism, not curation. Preserve its laws and
        # sentence for learning, but do not let one severe pass classify the
        # whole sheet as disposable.
        publish_picks(out_dir, sheet, verdict, record_cuts=not harsh)
        row.update(judged=True, verdict=verdict, hit_rate=hit,
                   blanket_rejection=harsh)
        keep = verdict.get("keep") or []
        arresting = verdict.get("arresting") or []
        dead = verdict.get("dead") or []
        row["arresting_rate"] = len(arresting) / max(args.n, 1)
        prefix = "ADVISORY blanket rejection · " if harsh else ""
        print(f"{prefix}hit {len(keep)}/{args.n}  arresting {len(arresting)}  dead {len(dead)}"
              f"  motif={verdict.get('dominant_motif')!r}"
              f"  {verdict.get('verdict','')}", flush=True)
        for a in arresting:
            print(f"    ARRESTING frame {a.get('frame')}: {a.get('why')}", flush=True)
        for broken in verdict.get("laws_broken") or []:
            print(f"    law {broken.get('law')} frames {broken.get('frames')}: {broken.get('why')}",
                  flush=True)
    with (out_dir / "taste-log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")
    publish_panel_decisions(out_dir)
    # The gallery needs the panel's sentence, not its entire research ledger.
    # Publish one small atomic snapshot so a reader never catches half JSON.
    taste = {"updated": row["ts"], "judged": row.get("judged", False)}
    if verdict is not None:
        taste.update(
            verdict=verdict.get("verdict", ""),
            dominant_motif=verdict.get("dominant_motif", "none"),
            keep=len(verdict.get("keep") or []),
            arresting=len(verdict.get("arresting") or []),
            dead=len(verdict.get("dead") or []),
            advisory=row.get("blanket_rejection", False),
            movement=verdict.get("movement") or {},
        )
    else:
        taste["error"] = error
    target = out_dir / "taste-status.json"
    temporary = target.with_suffix(".json.part")
    temporary.write_text(json.dumps(taste, indent=2, sort_keys=True) + "\n")
    temporary.replace(target)
    return row


def main():
    ap = argparse.ArgumentParser(description="Judge the wall against chorus/LAWS.md.")
    ap.add_argument("--out-dir", default=str(pathlib.Path.home() / "models" / "flux-output"))
    ap.add_argument("--n", type=int, default=16)
    ap.add_argument("--interval", type=float, default=600)
    ap.add_argument("--timeout", type=float, default=240)
    ap.add_argument("--public-base", default=os.environ.get("CHORUS_PUBLIC_BASE", ""),
                    help="public https base for this node, so the judge fetches the sheet "
                         "instead of receiving it inline")
    ap.add_argument("--recent", type=int, default=RECENT_WINDOW,
                    help="judge only the newest N frames; 0 grades the whole archive")
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()

    while True:
        try:
            judge_once(args)
        except Exception as exc:  # a judge that dies stops the gate entirely
            print(f"sentinel error: {exc}", flush=True)
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
