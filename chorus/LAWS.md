# Chorus design laws

Not style preferences. Each law exists because breaking it produced a specific
failure that reached the wall, and each is stated so a judge with eyes can
return a verdict on a frame without knowing anything else.

Version 1. Amend by adding the failure that forced the change.

## The laws

1. **The medium must be legible as a surface.** A viewer should be able to name
   how the image was made without reading the prompt. *Failed by:* a wall of
   images that were all recognisably the same digital illustration wearing
   different medium labels.

2. **No bullseye.** The subject does not sit centred, whole, with clean margin
   on all sides. *Failed by:* every grammar specifying distance or angle and
   none specifying position, which resolves to centre every time.

3. **Highlights are reserved, not added.** In any print or paint medium the
   brightest value is bare substrate. *Failed by:* specular dots on fruit that
   watercolour physically cannot make.

4. **One accent, not a wash.** Saturation is quarantined to a small part of
   the frame against a restrained ground. *Failed by:* palettes applied as
   global colour, which reads as a filter.

5. **The range is populated.** Neither an all-mid wash nor a black frame with a
   lit keyhole. *Failed by:* four of six sampled frames with more than half
   their pixels in the bottom 15% of the value scale.

6. **Siblings differ.** Within a sequence, no two frames may be substitutable
   for each other. *Failed by:* four near-identical pomegranates when only the
   framing moved.

7. **Neighbours contrast.** Consecutive sequences do not share a look. *Failed
   by:* twenty consecutive images of a solitary structure in a cold landscape.

8. **No motif may dominate.** If one composition recurs across unrelated
   sequences it is a collapse, not a style. *Failed by:* three receding
   perspective corridors appearing in unrelated sequences.

9. **Every instruction must reach the model.** Prompts stay inside CLIP's 77
   tokens. *Failed by:* 122-token prompts whose entire tail — light, palette,
   substrate — was silently discarded.

10. **The mood pool must span its whole range.** *Failed by:* eight lighting
    entries that were all chiaroscuro and six palettes that were all grey,
    making gloom the only reachable output.

11. **Fidelity is not beauty.** A proposal to clean up, sharpen, or raise the
    fidelity of the output is treated as a suspected regression until proven
    otherwise. The struggle of the medium outranks the smoothness of the
    render. *Given by the governor on anchoring commit 68f2a0a*, after the
    operator's first approval, as the guardrail against drifting back into the
    perfect void the first generator lived in.

12. **Approval freezes the distribution, not the work.** After an operator
    approval, nothing is added to a sampled pool. New material enters only as a
    challenger drawn for a small fixed share of frames, and earns a place by
    out-scoring the anchor over repeated appearances. *Failed by:* eight media,
    four worlds and six moods added within minutes of "This is beautiful",
    which moved 36% of all draws onto unproven material and halved realised
    variety while nominal variety rose.

13. **Rare vocabulary renders as the model's default.** A term the model knows
    thinly contributes almost nothing to the guidance delta, so the image falls
    back to the mean. Adding obscure media does not add attractors; it adds
    doors into the same one. Prefer fewer terms the model renders decisively.

14. **Awe is a ratio.** A frame with one scale, one distance and one moment can
    be beautiful but cannot be overwhelming. Reachable in a short prompt: scale
    disparity, multiplicity past counting, duration made visible, and one hard
    shaft of light. Not reachable: the uncanny, narrative across frames, and
    any adjective standing in for the thing itself.

## The anchor

`taste-log.jsonl` holds one kind of entry that outranks every other: an
`operator_anchor`. The sentinel's scores are calibrated against anchors, never
the other way round. A round that scores well against the laws but has drifted
from the last approved state has drifted, whatever its score says.

First anchor: commit `68f2a0a`, "This is beautiful."

## The gate

A change to the language is not shipped on the strength of the newest frame.
It is judged against an even sample across the whole run — `contact.py` builds
that sheet, `sentinel.py` judges it, and the verdict is appended to
`taste-log.jsonl` whether it is good or bad.

The failure this replaces: six consecutive rounds of "this is better", each
argued from one or two frames chosen by recency, against a wall that was not
better. Looking was optional, so under time pressure it was the first thing
dropped. It is no longer optional.
