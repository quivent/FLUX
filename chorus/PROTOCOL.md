# Chorus protocol

The hive is only as good as the contracts between its parts. Almost every
failure in this system has been a protocol failure, not a model failure:

- The judge's schema said `"law": <1-10>` while `LAWS.md` held eleven, so the
  governor's own guardrail against post-approval regression **could not be
  cited by the eye enforcing it**. A hardcoded range in a string.
- Seat prompts were written long. The governor serves one request at a time
  with speculative decoding, so four parallel seats starved each other into
  gateway timeouts and three of four councils returned nothing.
- The contact sheet's contract was "sample evenly across what you are given".
  Correct in isolation; once the archive was restored it meant the gate graded
  four hours of superseded work and reported it as today.
- The composer was never given the laws at all. It could not avoid a failure
  it had never heard of, so it re-derived the same mistakes forever.
- Nothing mapped a verdict's frame numbers back to files, so per-image taste
  data was computed and discarded every round for hours.

None of those were fixed by a better model. All were fixed by a better
contract. This file is the contract.

## Rules every message in this system obeys

**1. Carry the why, not only the rule.** A law is transmitted with the failure
that forced it. A rule alone is a constraint to route around; a rule with its
history is understanding, and a reader can extend it to a case nobody listed.

**2. Derive from the source, never restate it.** No count, range or enum is
written twice. `SCHEMA` reads its law range from `LAWS.md`. A restated
constant is a lie waiting for the source to change.

**3. Bound every free field.** Prompts are 30–50 words because CLIP discards
past 77 tokens. Seat phrases are ≤16 words. A field without a bound will
eventually arrive at a length that silently breaks something downstream.

**4. Refuse rather than coerce.** An over-long prompt is rejected, not
truncated. Truncation is how every fix the councils gave was deleted before
reaching the model, invisibly, for hours.

**5. Absence is not consent.** An unreachable judge records `judged: false`,
never a pass. A round nobody scored must never be mistakable later for a round
that scored well.

**6. Ask small of a busy engine.** One seat, one question, a few hundred
tokens. Parallel work goes to separate engines; the same engine gets a queue,
not a fan-out.

**7. Name what you are measuring.** A verdict states the window it judged. A
score over the whole archive and a score over the current run are different
quantities and must never be compared as one series.

A movement verdict uses one 24-frame visual-language cohort. Mixing several
style epochs into one sheet makes a coherent generator look random and makes
causal attribution impossible.

**8. Every judgement resolves to an artifact.** A verdict in frame numbers is
useless without the manifest mapping numbers to files. If a signal cannot be
attributed, it cannot promote anything, and it will be silently discarded.

**9. Separate the seats.** The judge does not write the laws. The proposer does
not judge. The operator alone sets an anchor. A component that scores its own
proposals will always find them good.

**10. New material shares a budget; it never enlarges the distribution.**
Adding to a weighted pool takes probability mass from everything already
proven. Growth is rationed and earned, never granted.

**11. Survival is not succession.** `keep` means an individual work remains in
the collection. A change earns succession only when it produces an arresting
frame on a sheet whose movement progressed, while preserving the anchor's
retention floor. A lenient archive must not become a lenient steering system.

**12. Change one declared axis at a time.** The hive may adjust the creative
system, but every adjustment names its axis, carries evidence, and remains
reversible. A stalled movement lengthens commitment to one visual language;
it does not simultaneously replace subject, medium, light, and composition.

The eye reports one primary `failure_axis`. `coherence` lengthens the style
hold. `surface` or `light` issues a versioned one-axis directive to Drift. The
other axes remain evidence until a dedicated reversible actuator exists.
The active visual language, its age, and the last applied directive are written
to `drift-status.json` and resumed after a daemon restart; operational recovery
must never masquerade as creative evolution.

**13. Operator feedback outranks model evidence.** Durable feedback is supplied
to proposal seats as context. An explicit operator promotion or retirement is
applied before statistical settlement and recorded on the candidate. Neither
Gemma nor Hive may overwrite an operator anchor.

## The message shapes

`sentinel → taste-log.jsonl` — one row per round, always written:

```json
{"ts": 0, "judged": true, "frames": 16, "window": 80, "hit_rate": 0.31,
 "verdict": {"keep": [1], "cut": [2], "laws_broken": [{"law": 8, "frames": [3], "why": ""}],
             "dominant_motif": "", "verdict": ""}}
```

`contact → manifest.json` — the join that makes a verdict attributable:

```json
{"sheet": "contact.jpg", "frames": {"1": "drift-0001-00-seed-123.png"}}
```

`sentinel → picks.json` — accumulating, because a later sheet may not resample
a frame it already kept:

```json
{"keep": ["drift-...png"], "cut": ["drift-...png"], "updated": 0}
```

`hive → challengers.json` — every candidate with its evidence:

```json
{"eps": 0.16, "streak": 2, "baseline": 0.31, "change_baseline": 0.04,
 "challengers": [{"phrase": "", "kind": "detail", "seat": "materials",
                  "state": "trial|promoted|retired", "keeps": 0,
                  "change_wins": 0, "appearances": 0}]}
```

`operator → operator-feedback.jsonl` — durable prose or a direct decision:

```json
{"id":"human-001", "ts":0, "instruction":"Preserve beauty; be harsher on change."}
{"id":"human-002", "ts":0, "action":"retire", "challenger_id":"abc123", "instruction":"Lost the material voice."}
```

`operator → taste-log.jsonl` — outranks everything above:

```json
{"kind": "operator_anchor", "verdict": {"verdict": "This is beautiful."},
 "language_commit": "68f2a0a"}
```

## Reading a verdict

**Partial failure is a tuning problem. Universal failure is a missing
mechanism.**

When a fault appears on some instances, change the weights. When it appears on
every one, stop tuning: the machine has no way to produce the thing whose
absence you are measuring, and no rate applied to a device that does not exist
will move it.

The case that taught it: a judge broke law 14 -- awe is a ratio -- on all
sixteen frames of a sheet. Not eleven, not fourteen. Sixteen. The grammar
composes one subject, at one distance, in one moment; nothing in it could ever
place two scales in a frame, so the ratio was unreachable at any setting. A
device answering it was sitting at 13% and could never have mattered. The rate
of a thing is only worth arguing about once the thing can happen at all.

The corollary for reading any score: a number that moves is a tuning signal, a
number pinned at zero across every instance is a design signal, and treating
the second as the first wastes every cycle spent on it.

## What raising the hive's ability actually means

Not a bigger model. In order of leverage:

1. **Give a component the history it needs to reason.** The composer with the
   laws and the panel's keeps and cuts is a different machine from the same
   model without them.
2. **Make the contract machine-checked.** A bound that is only in prose is a
   bound that will be violated.
3. **Widen the panel across engines, not within one.** Seats are parallel only
   when they sit on separate hardware.
4. **Attribute every outcome.** A signal that cannot be traced to a cause
   cannot improve anything, however good the judgement behind it was.
