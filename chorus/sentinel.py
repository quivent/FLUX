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


SCHEMA = """Reply with ONLY a JSON object, no prose, no markdown fence:
{"keep":[<frame numbers worth wall space>],
 "cut":[<frame numbers that are not>],
 "laws_broken":[{"law":<1-LAWMAX>,"frames":[<n>],"why":"<8 words>"}],
 "dominant_motif":"<the composition or subject recurring across unrelated frames, or none>",
 "verdict":"<one sentence a curator would say>"}""".replace("LAWMAX", str(_law_count()))


def build_sheet(out_dir, sheet, n):
    subprocess.run(
        [sys.executable, str(HERE / "contact.py"),
         "--dir", str(out_dir), "--out", str(sheet), "--n", str(n)],
        check=False, capture_output=True, timeout=180,
    )
    return sheet.exists()


def sheet_reference(sheet, public_base):
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
            small = sheet.with_name("_contact_small.jpg")
            im.save(small, quality=60)
            data = small.read_bytes()
        except Exception:
            pass
    return "data:image/jpeg;base64," + base64.b64encode(data).decode()


def ask_governor(sheet, laws, timeout, public_base=""):
    """One multimodal call. Returns the parsed verdict, or None if it could not
    be obtained -- never a default-pass."""
    reference = sheet_reference(sheet, public_base)
    body = {
        "model": "governor",
        "max_tokens": 700,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text":
                    "You are the eye-gate for a live generative art wall. This is a contact "
                    "sheet of one run, sampled evenly, numbered left to right and top to "
                    "bottom.\n\nJudge it against these laws:\n\n" + laws + "\n\n" + SCHEMA},
                {"type": "image_url", "image_url": {"url": reference}},
            ],
        }],
    }
    # Cloudflare fronts the governor and refuses urllib's default agent with a
    # 403, which looks exactly like an auth failure and is not one.
    req = urllib.request.Request(
        GOVERNOR, json.dumps(body).encode(),
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
    # A wall that is mostly failing should not race to produce more of it.
    control["sleep"] = 20 if hit_rate < 0.34 else 0
    pathlib.Path(control_path).write_text(json.dumps(control, indent=2, sort_keys=True) + "\n")
    return hit_rate


def judge_once(args):
    out_dir = pathlib.Path(args.out_dir).expanduser()
    sheet = out_dir / "_sheets" / "contact.jpg"
    laws = (HERE / "LAWS.md").read_text()

    if not build_sheet(out_dir, sheet, args.n):
        print("no frames to judge yet", flush=True)
        return None

    verdict, error = ask_governor(sheet, laws, args.timeout, args.public_base)
    row = {"ts": time.time(), "frames": args.n, "sheet": str(sheet)}
    if verdict is None:
        # Recorded as a miss, not a pass. An unjudged round must never be
        # mistaken later for an approved one.
        row.update(judged=False, error=error)
        print(f"NOT JUDGED: {error}", flush=True)
    else:
        hit = steer(verdict, out_dir / "drift-control.json")
        row.update(judged=True, verdict=verdict, hit_rate=hit)
        keep = verdict.get("keep") or []
        print(f"hit {len(keep)}/{args.n}"
              f"  motif={verdict.get('dominant_motif')!r}"
              f"  {verdict.get('verdict','')}", flush=True)
        for broken in verdict.get("laws_broken") or []:
            print(f"    law {broken.get('law')} frames {broken.get('frames')}: {broken.get('why')}",
                  flush=True)
    with (out_dir / "taste-log.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")
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
