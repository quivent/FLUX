# Chorus

A resident FLUX loop that never finishes, streaming to a live wall.

The name is the mechanism: every generation renders **two voices on one
subject** — the same charge and the same story beats, in media from different
families — interleaved, so the gallery reads as a dialogue rather than a feed.

```
chorus/loop.py --> piper --> flux serve --> /api/assets/ws --> /gallery/
```

## What it is for

`run_creative_flux_manifest.py` is a finite sweep: N sources × M passes, then
done. Chorus is the other shape — a pipeline held in memory, emitting until
stopped, where what it makes next depends on what it just made.

## Why it looks the way it does

The first version collapsed. Twenty images in, every frame was a solitary
structure centred in a cold empty landscape: a satellite dish under an aurora,
a monolith in a snowy forest. The model was never the problem. One slot order
made composition compulsory, the pools held exactly one aesthetic, and carrying
elites forward pulled every generation back toward the same attractor.

Five things fixed it, in order of how much they moved the needle:

1. **Kill the mud pairings.** Motion blur over a cluttered macro subject in a
   tactile medium is brown soup whatever the model does. Excluding those
   combinations was worth more than any number of new adjectives.
2. **Weight toward opinionated media.** Sumi-e, woodblock, risograph, cel,
   tintype. An even spread across the space spends half the night in its weak
   half. This is curation, not fairness.
3. **Imperfection, matched to the surface.** Brush bristles, ink bleeding past
   its edge, misregistered plates, chemical streaks. It is the difference
   between a clean render *of* a woodcut and a woodcut. The flaw has to belong
   to the medium — "brush bristles" on a risograph is a wrong word, not a
   texture.
4. **Lead with intent, not inventory.** Every prompt opens with what the frame
   is *about* — "capturing the dignity of something worn out by use" — before
   what is in it. Materials come last. This is what separates a competent
   picture of a thing from a picture with a point of view.
5. **A sequence is one story.** Fixed subject, four ordered beats, and a
   posture progression whose direction matches the arc — a beat that disperses
   cannot land on a body that tightens, or the frame argues with its caption.
   The art is in the delta between frames, not in optimising any one of them.

Points 3–5 are the governor's direction, taken verbatim and implemented.

## Running it

```sh
make chorus          # bring the whole suite up on the node
make chorus-status   # what is running, and how fast
make chorus-stop     # stop the loop, leave the gallery served
```

`up.sh` is deliberately a file rather than an inline command: `pkill -f
piper_local` matches the very shell about to launch `piper_local`, because the
launch line puts that name on the same command line. PID files make the kills
exact.

## Steering it live

`loop.py` re-reads `<out-dir>/drift-control.json` every generation, so nothing
below needs a restart — the resident pipeline is the expensive part and must
survive a knob turn. Values are clamped rather than rejected: a typo'd
4000-step render would wedge the loop for an hour.

| key | range | effect |
|---|---|---|
| `steps` | 1–60 | sampling steps |
| `width` / `height` | 256–1536 | frame size |
| `guidance` | 0–12 | prompt adherence |
| `batch` | 1–16 | frames per generation (two voices interleave inside it) |
| `phase` | `auto`/`dawn`/`day`/`dusk`/`night` | force the light arc |
| `pinned` | `{"medium": "sumi-e ink wash"}` | hold any slot fixed |
| `paused` | `true`/`false` | stop generating, keep the model resident |

`drift-status.json` reports the live settings and the current concept.

## What it does not have

Taste. Nothing here judges whether an image is good. The old "novelty" metric
counted differing slots, which happily reported *novel* while the image looked
identical — it is gone. The only real signal is a human picking frames, which
steers the sequence picker; everything else is structure.
