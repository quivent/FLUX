#!/usr/bin/env python3
"""author — a composer that reasons instead of filling slots.

language.py is a grammar. It draws a medium, a subject, a framing and a mood
from pools and assembles them. Its ceiling is therefore the pools: the best
image it can ever make is the best combination of phrases someone typed into
it, which is why growth kept stalling and why widening the pools made things
worse rather than better.

More to the point, the grammar has no idea why any of its rules exist. LAWS.md
is read by the judge and never by the thing that writes, so the composer cannot
avoid a failure it has never heard of. It re-derives the same mistakes because
nothing in it remembers.

This module gives the composer both halves. It hands an engine:

  * the laws, INCLUDING the failure each one was written from -- the "why",
    not just the rule;
  * frames the panel recently kept and recently cut, with the prompts that
    produced them, so it can see what actually worked here rather than in
    general;
  * the hard constraint that made half of tonight's fixes invisible: CLIP
    truncates at 77 tokens, so anything past ~50 words is discarded unread.

and asks for one prompt. What comes back is judged exactly like any other
frame. It earns its place or it does not; this is a challenger, not a
replacement, and the grammar keeps running beside it.

    chorus/author.py --once      author one prompt and print it
"""
import argparse
import json
import os
import pathlib
import random
import time
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
GOVERNOR = "https://governor.influx.vision/v1/chat/completions"
ENGINES = [e for e in (os.environ.get("CHORUS_SECOND_ENGINE", ""), GOVERNOR) if e]

# Tension, not objects. The governor's own diagnosis of why he collapsed:
# "Stop naming the nouns. Start naming the conflict. Force me to synthesize the
# imagery from an abstract emotion rather than a grocery list." A seed is a
# situation with a contradiction in it, never a subject.
TENSIONS = [
    "the feeling of a sudden departure",
    "the geometry of a betrayal",
    "something ending while everyone keeps working",
    "a kindness that arrives far too late",
    "the moment before anyone admits what happened",
    "order imposed on something that resists it",
    "a celebration nobody is enjoying",
    "the weight of a thing nobody will name",
    "proof of a presence that has gone",
    "intimacy observed from too far away",
    "a repair that made it worse",
    "the last warm thing in a cold place",
]

BRIEF = """You compose prompts for FLUX.1-dev, which renders one still image per prompt.

Write ONE prompt. Hard rules, each learned the expensive way:

- 30 to 50 words, never more. CLIP truncates at 77 tokens and silently discards
  everything after it, so a longer prompt loses its own ending unread.
- Name the medium and how its surface is physically made. "Oil painting" is a
  label; "thick impasto ridges catching the light" is a surface.
- Say where in the rectangle the subject sits. Distance without position
  resolves to dead centre every time.
- One accent of colour against a restrained ground, not a wash over everything.
- Everything must be something a camera could verify. No adjectives standing in
  for the thing itself: not "haunting", not "profound", not "awe-inspiring".
- Compliance is the enemy of the arresting frame. A frame may be asymmetrical,
  uncomfortable or wrong on purpose. Discord is permitted; safety is not
  interesting and scores nothing.

Reply with ONLY a JSON object:
{"prompt":"<the prompt>","intent":"<what you are going for, 10 words>"}"""


def read_laws():
    try:
        return (HERE / "LAWS.md").read_text()
    except OSError:
        return ""


def recent_examples(out_dir, n=6):
    """What worked and what did not, HERE -- with the prompt that caused it.

    A model asked to write well in the abstract writes competently in the
    abstract. Shown the frames this panel kept and the frames it cut, it can
    aim at this wall instead of at art in general.
    """
    out = pathlib.Path(out_dir)
    try:
        picks = json.loads((out / "picks.json").read_text())
    except (OSError, ValueError):
        return [], []
    keep, cut = set(picks.get("keep") or []), set(picks.get("cut") or [])
    prompts = {}
    ledger = out / "creative-drift.jsonl"
    if ledger.exists():
        for line in ledger.read_text().splitlines()[-4000:]:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("file") and row.get("prompt"):
                prompts[row["file"]] = row["prompt"]
    # Arresting frames first. A prompt that produced a merely compliant frame
    # teaches the composer to be compliant, which is the trap: the laws already
    # enforce compliance, and imitating it just makes the wall more reliably
    # unremarkable.
    arrest = [f for f in (picks.get("arresting") or []) if f in prompts]
    kept = [prompts[f] for f in arrest][-n:]
    if len(kept) < n:
        kept += [prompts[f] for f in list(keep) if f in prompts and f not in arrest][-(n - len(kept)):]
    cutt = [prompts[f] for f in list(cut) if f in prompts][-n:]
    return kept, cutt


def recent_motifs(out_dir, n=12):
    """What the composer has lately repeated, so it can be forbidden.

    Negative constraints, per the governor: "Ban the bottom left composition.
    Ban the pewter plate. Ban the cracked motif." A collapse is only visible in
    aggregate, and the composer cannot see its own aggregate.
    """
    out = pathlib.Path(out_dir)
    mine = []
    ledger = out / "creative-drift.jsonl"
    if ledger.exists():
        for line in ledger.read_text().splitlines()[-2000:]:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("authored") and row.get("prompt"):
                mine.append(row["prompt"])
    words = {}
    for p in mine[-n:]:
        for w in set(p.lower().replace(",", " ").split()):
            if len(w) > 4:
                words[w] = words.get(w, 0) + 1
    # A word in a third or more of recent output is a rut, not a preference.
    threshold = max(2, len(mine[-n:]) // 3)
    return sorted(w for w, c in words.items() if c >= threshold)[:14]


def author(out_dir, timeout=200):
    laws = read_laws()
    kept, cut = recent_examples(out_dir)
    banned = recent_motifs(out_dir)
    tension = random.choice(TENSIONS)
    parts = [BRIEF]
    if laws:
        # The laws carry their own failure history in the file: each says what
        # it was broken by. That history is the understanding; the rule alone
        # is just a constraint to route around.
        parts.append("These laws govern this wall. Each records the failure that "
                     "forced it -- read the failures, not only the rules:\n\n" + laws)
    # Winners are shown as EXHAUSTED, never as models. Shown as models they
    # turned the composer into a mirror of the past -- twenty prompts that
    # converged on a cracked still life, bottom left, every time. The fix is
    # its own: "stop giving me the answer key."
    if kept:
        parts.append("These already exist. They are used up. Do not write anything "
                     "adjacent to them -- not the same subject, not the same "
                     "composition, not the same palette:\n"
                     + "\n".join("- " + p for p in kept))
    if cut:
        parts.append("These failed:\n" + "\n".join("- " + p for p in cut))
    if banned:
        parts.append("BANNED, because your own recent prompts collapsed onto them:\n"
                     + "\n".join("- " + b for b in banned))
    parts.append(f"Your seed is a tension, not a subject: {tension}.\n\n"
                 "Find the image that carries it. Do not name the emotion in the "
                 "prompt -- render it as a physical fact a camera could see. Then "
                 "write one prompt for that image.")

    body = {"model": "governor", "max_tokens": 320,
            "messages": [{"role": "user", "content": "\n\n".join(parts)}]}
    errors = []
    for engine in ENGINES:
        req = urllib.request.Request(
            engine, json.dumps(body).encode(),
            {"Content-Type": "application/json", "User-Agent": "chorus-author/1"},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                text = json.load(resp)["choices"][0]["message"]["content"]
        except (urllib.error.URLError, OSError, ValueError, KeyError, IndexError) as exc:
            errors.append(str(exc))
            continue
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            errors.append("unparseable")
            continue
        try:
            obj = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            errors.append("bad json")
            continue
        prompt = str(obj.get("prompt", "")).strip()
        words = len(prompt.split())
        # Enforce the budget here rather than trusting it. A prompt over the
        # limit does not fail loudly -- it silently loses its own ending, which
        # is the exact bug that made every earlier fix invisible.
        if not prompt or words > 55:
            errors.append(f"rejected at {words} words")
            continue
        return {"prompt": prompt, "intent": str(obj.get("intent", "")).strip(),
                "words": words, "authored_at": time.time()}, None
    return None, "; ".join(errors) or "no engine answered"


def main():
    ap = argparse.ArgumentParser(description="Author one FLUX prompt from the laws and the taste log.")
    ap.add_argument("--out-dir", default=str(pathlib.Path.home() / "models" / "flux-output"))
    ap.add_argument("--n", type=int, default=1)
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()
    for _ in range(args.n):
        result, error = author(args.out_dir)
        if result:
            print(f"[{result['words']}w] {result['prompt']}")
            print(f"       intent: {result['intent']}")
        else:
            print("no prompt:", error)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
