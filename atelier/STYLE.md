# The Atelier design system

This documents the design system **as it is implemented** in `control.html` — the
tokens, the type, the layout and the rules that are actually in the stylesheet, not a
system anyone would design from scratch. Where the code and the intent disagree, the
code is what is written down here.

The stylesheet states its own thesis at the top, and it is the most useful single
sentence about the whole surface:

> Koyomi — Atelier. A white room with pink walls of light.
> White is the field. Pink is the voice. Red is only ever urgent.
> Black is ink: text and weight, never ground. Everything is curved.

Four rules, and the palette follows from them exactly.

## Palette

### The card's own materials

Two tokens are lifted straight out of `collection.py` — the same RGB the PIL
typography pass prints with — so the interface and the artefact share a substrate.

| token | value | what it is for |
|---|---|---|
| `--cream` | `#f7f0e2` | the card stock, `collection.CREAM` |
| `--cream-dim` | `#ece2d0` | **the mat behind every image while it decodes** |
| `--ink` | `#191320` | the card's ink, `collection.INK`. Headings, stat values, card titles |

`--cream-dim` is the single most load-bearing colour on the page. See *The rules*.

### The room

| token | value | what it is for |
|---|---|---|
| `--room` | `#fffafc` | the page ground: near-white, pink-lit |
| `--panel`, `--panel2` | `#ffffff` | card surfaces. Pure white, always |
| `--wall` | `#fffafc` | the wall the work hangs on |
| `--wall-lit` | `#fff6fa` | the bottom of the output plate's gradient |
| `--sink` | `#fdf3f7` | **recessed** surfaces: inputs, wells, the log, segmented-control tracks |
| `--line` | `#f7e7ef` | soft rules — table borders, card edges |
| `--line2` | `#f0d5e3` | the slightly firmer rule: control borders, image edges |

The distinction that matters is `--panel` (white, raised) against `--sink` (pink-tinted,
recessed). Anything the operator *types into* is sunk; anything that *presents* is
raised. There are no hard hairlines anywhere — both rules are pink-tinted, never grey.

The body ground is not flat. Three radial gradients are pinned to the viewport with
`background-attachment: fixed`, so the room's light stays put while the work scrolls
past it:

    radial-gradient(720px 520px at   6% -8%, rgba(255,92,158,.20), transparent 60%)
    radial-gradient(560px 420px at  98%  2%, rgba(246, 48, 74,.10), transparent 58%)
    radial-gradient(900px 640px at  82% 92%, rgba(255,92,158,.13), transparent 62%)

### Text

| token | value | what it is for |
|---|---|---|
| `--text` | `#1c1622` | body copy |
| `--muted` | `#6d6577` | secondary copy, resting button labels, table cells |
| `--faint` | `#aaa0b1` | field labels, units, timestamps, the "was" value of a changed knob |
| `--dim` | `#00000000` | **fully transparent.** A legacy token, kept so nothing that still paints with it can put a colour on the page |

### Pink is the voice

Pink is not an accent applied to a neutral UI. It carries the room, and it is what
"on", "synced", "selected", and "acknowledged" all look like.

| token | value | what it is for |
|---|---|---|
| `--pink` | `#ff5c9e` | the live colour: active chips, `on` toggles, the `.go` button, slider fill and thumb, focus rings, `sync.ok` |
| `--pink-ink` | `#d81b7a` | pink as *text* — the readable one. Hover labels, `<code>`, log emphasis |
| `--pink-soft` | `#ffe9f2` | pink as *ground* — hover fills, the advisory note, row hover, focus glow |
| `--pink-line` | `#ffcbe0` | pink as *edge* — and as the cel drop-shadow under everything raised |

### Red is rationed

Red never means "accent". It means a state the operator has to resolve.

| token | value | what it is for |
|---|---|---|
| `--red` | `#f6304a` | the halt button; the slider track of a knob with unsaved changes |
| `--red-ink` | `#d9102a` | red as text: a stale applied-revision, a dirty knob's value |
| `--red-soft` | `#ffe4e7` | red as ground: `.knob.dirty`, `.revbox.a.stale`, the halt button at rest |

The three places red is allowed to appear are exactly: **halt**, **a knob edited but
not yet published**, and **a revision the worker has not acknowledged**. That last one
is the ack contract rendered in colour — `.sync.pend` is red with a pulsing dot, and it
stays red until the worker itself says otherwise.

### Legacy tokens

    --accent:#d81b7a;  --accent-soft:#ff5c9e;
    --good:#d81b7a;    --warn:#f6304a;    --bad:#d9102a;

These are **remapped, never renamed.** The JavaScript paints with them by name;
repointing the values re-themed the whole surface without touching a line of script.
Note that `--good` is pink, not green: there is no green on this page.

## Typography

The UI is set in **rounded Japanese-family sans faces**, loaded from Google Fonts:

| token | stack | used for |
|---|---|---|
| `--display` | `Zen Maru Gothic`, `ui-rounded`, `Hiragino Maru Gothic ProN`, `M PLUS Rounded 1c` | headings, stat values, card titles, revision numbers |
| `--sans` | `M PLUS Rounded 1c`, `ui-rounded`, `Hiragino Maru Gothic ProN`, system | everything else. Body is `15px/1.65` |
| `--mono` | `JetBrains Mono`, `ui-monospace`, SFMono, Menlo | the worker log, `<code>` |
| `--serif` | **aliased to `--display`** | a legacy name; there is no serif on the page |

The dominant type gesture is **uppercase micro-labels**: `9.5px`, weight `700`,
`letter-spacing: .14em`, in `--faint` or `--muted`. Every field name, stat key, table
header, chip and small button uses it. The contrast between those and the big rounded
`--display` numerals is most of the page's visual rhythm.

`h1` is `30px/900`, uppercase, `letter-spacing: .22em`. `h2` is a `12.5px` uppercase
label with a `::after` rule underneath it — 3px tall, fully rounded, and a hard-stop
gradient that paints **pink for the first 44px and `--line` for the rest**. It reads as
a tab under the section name.

### A note on EB Garamond

`control.py` still serves `/font/EBGaramond.ttf` and `/font/EBGaramond-Italic.ttf` from
the app itself, and its docstring still says *"the interface is set in the same face the
cards are printed in."*

**It is not, any more.** The current `control.html` declares no `@font-face` and makes
no request to `/font/`. The endpoint is live and unused.

The fonts are still shipped and still load-bearing: `collection.py` prints every card
in them (`FONT = /home/dev/fonts/EBGaramond.ttf`, `FONT_I` for the italic quote and
role lines). The cards are Garamond; the interface that steers them is Zen Maru Gothic.
The one thread still connecting them is `--cream` / `--ink`, taken from the card.

## Layout

**The studio grid.** Work in the main column, controls in a sticky rail.

    .studio { grid-template-columns: minmax(0,1fr) 348px; gap: 26px; align-items: start }

- `main` holds The Wall (the hero card and the run's grid of plates), Instruments
  (desired/applied revisions, the diff table, the adoption trail), the fitness chart,
  the mutation-vector table, and the worker log.
- `aside` is the rail: Direction, Press, The Press lifecycle, Cadence, Services. It is
  `position: sticky; top: 22px` with `max-height: calc(100vh - 44px)` and its own
  scroll, styled with a `--pink-line` thumb. The controls stay in reach no matter how
  far down the work you are.
- `.wrap` caps the page at `1240px`, padded `30px 26px 84px`.
- A secondary `.cols` grid (`360px` + rest) is used for the narrower paired panels.

Breakpoints, in order:

| width | change |
|---|---|
| ≤1080px | the rail unsticks and drops below the work — `.studio` becomes one column |
| ≤980px | `.cols` collapses to one column |
| ≤820px | the hero stacks; `h1` drops to 24px; the toast goes full-bleed |
| ≤520px | plates retile at `minmax(118px,1fr)`; the masthead tightens |

**The masthead** (`.bar`) is a rounded white plate, `border-radius: 28px`, holding the
run's vital signs as `.stat` pairs — loop, cycle, champion fitness, anchors, GPU, rev
sync, transport — with the Koyomi wordmark right-aligned at the end.

## The rules

These are the non-obvious commitments in the stylesheet. They are what make it a system
rather than a palette.

**1. Plates are matted in the card's own cream, never black.**

    .hero .big img, .tile img { background: var(--cream-dim); opacity: 0 }

Every image sits on `#ece2d0` — the card stock's own dimmed cream — before its bytes
arrive. The conventional choice is a dark placeholder, and it is wrong here: a black
rectangle in a white room is a hole, and it flashes against the paper the card is
printed on.

**2. Images fade in after decode and never show an empty ground.**

    .hero .big img, .tile img       { opacity: 0; transition: opacity .5s ease }
    .hero .big img.in, .tile img.in { opacity: 1 }

Images render at `opacity: 0` and the script adds `.in` once the image has decoded. The
hero additionally has a `.swap` layer at `inset: 14px` that cross-fades on the same
500 ms curve, so changing the champion is a dissolve, not a blink. Combined with rule 1,
there is no frame in which the page shows an empty plate.

**3. Everything is curved, and the radii are a scale.**

    --r: 24px   --r-md: 18px   --r-sm: 13px   --r-xs: 9px

Plus `999px` for every pill: buttons, chips, toggles, segmented controls, sync badges,
meter fills, scrollbar thumbs. Larger containers step *above* the scale — cards 24px,
the masthead 28px, the output plate 30px — so the outer shape is always rounder than
what it holds. There is not a square corner on the page.

**4. Shadows are flat cel drops, not realistic blur.**

    --cel: 0 9px 0 -4px var(--pink-line);

A zero-blur pink offset, sometimes paired with a wide soft `rgba(216,27,122,.10)` glow
for depth. Raised things sit on a coloured ledge rather than a grey smudge — this is
what makes the surface read as illustration rather than as material.

**5. Motion is a spring, and buttons have travel.**

    --spring: cubic-bezier(.34, 1.46, .64, 1);

The overshoot is the point. `.go` buttons carry a `0 5px 0 -1px var(--pink-ink)` ledge
that grows to `7px` and lifts `1.5px` on hover, then **compresses to `2px` and drops
`2px` on `:active`** — the button is physically pressed. Tiles lift `4px` on hover.

**6. Verdicts are shown by treatment, not by badge.**

    .tile.keep   { box-shadow: 0 0 0 3px var(--pink), … }
    .tile.retire { opacity: .32; filter: grayscale(.85) }

A kept card gets a solid pink ring and a heavier drop. A retired card is desaturated to
32% opacity and 85% greyscale — still legible, clearly discarded, never removed. The
operator can always see what they rejected.

**7. Pending state pulses; settled state does not.**

    @keyframes pulse { 0%,100% { opacity:1; transform:scale(1) }
                       50%     { opacity:.3; transform:scale(.72) } }
    .sync.pend .dot { background: var(--red); animation: pulse 1.1s infinite }

The only animation on the page that runs unprompted is the one that means *the worker
has not acknowledged this yet.* When the ack lands, `.sync.ok` goes solid pink and
still. Motion on this surface means an outstanding claim, and nothing else.

**8. No dark mode.**

There is no `prefers-color-scheme` block and no `[data-theme]` selector. The palette is
a white room, committed to in one direction. Inverting it would put pink light on a
dark ground, and the cream mat behind every plate — rule 1 — would stop making sense.
