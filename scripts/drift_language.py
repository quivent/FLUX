#!/usr/bin/env python3
"""drift_language — the part that decides what to make.

Split out of creative_drift because the first generator's failure was entirely
here: one template, one aesthetic, mutation that restyled but never reinvented.
Twenty images in, every frame was a solitary structure centred in a cold empty
landscape. The model was never the problem.

Three things changed.

FRAMING IS A VARIABLE. There is no single template. A grammar decides whether
you are two inches from a face or a mile above a city, and it reorders the
whole sentence. Composition cannot vary while one slot order is compulsory.

MEDIUM IS A VARIABLE. Cel animation, ink wash, gouache, tintype, claymation --
each carries its own light, edge quality and palette logic, written into the
style itself rather than bolted on. This is the axis that moves an image
furthest for the fewest words.

A SEQUENCE IS A UNIT. Each sequence commits to one concept -- medium, world,
grammar, palette -- and renders variations inside it. Consecutive images are
siblings; consecutive sequences are strangers. The old loop inverted this:
it mutated forever and so never went anywhere.

There is no "cinematic, highly detailed" here. It is the slop suffix that makes
every model's output converge, and dropping it is most of the battle.
"""
import random

# ---------------------------------------------------------------- media
# Each entry is a whole rendering language, not an adjective. The trailing
# clause does the work a generic quality tag pretends to.
MEDIA = [
    ("cel animation", "flat cel shading, confident ink outlines, limited palette, painted background"),
    ("90s anime film still", "hand-painted backgrounds, soft film grain, warm analogue colour"),
    ("gouache illustration", "opaque matte pigment, visible brush edges, paper tooth"),
    ("sumi-e ink wash", "wet black ink on rice paper, enormous empty space, one decisive stroke"),
    ("oil painting, impasto", "thick loaded brushwork, palette-knife ridges, glazed shadows"),
    ("risograph print", "two-colour misregistration, halftone dots, coarse newsprint"),
    ("stained glass panel", "leaded black cames, saturated transmitted light, flat jewel colour"),
    ("woodblock print", "carved flat planes, visible grain, hand-registered colour"),
    ("stop-motion puppet, claymation", "fingerprints in the clay, shallow macro focus, tiny practical lights"),
    ("graphite drawing", "soft pencil shading, smudged tone, white paper showing through"),
    ("watercolour", "blooming wet edges, granulating pigment, untouched white paper"),
    ("technical blueprint", "cyanotype blue, fine white linework, annotated dimensions"),
    ("tintype photograph", "wet plate collodion, silver halation, shallow field, chemical edge flaws"),
    ("16mm documentary still", "grainy reversal stock, halated highlights, handheld framing"),
    ("collage of torn paper", "layered cut edges, mixed printed textures, visible glue"),
    ("airbrushed 70s paperback cover", "soft gradients, chrome highlights, high-contrast drama"),
]

# ---------------------------------------------------------------- worlds
# Deliberately not "a lonely structure in a landscape". Faces, hands, crowds,
# food, cloth, weather -- the categories the first pass had no words for.
WORLDS = {
    "faces": ["an old fisherman", "a girl mid-laugh", "a tired surgeon", "twin sisters",
              "a street musician", "a boy with a chipped tooth", "a woman with grey braids"],
    "hands": ["hands kneading dough", "hands tying a fishing fly", "hands folding paper",
              "a hand catching rain", "hands passing a cup", "a potter's hands on the wheel"],
    "creatures": ["a heron mid-strike", "a sleeping fox", "a horse shaking off water",
                  "a moth on a windowpane", "an octopus changing colour", "a pack of dogs running"],
    "crowds": ["a night market", "a crowded tram at rush hour", "a wedding procession",
               "a fish auction", "swimmers at a public pool", "a protest in the rain"],
    "interiors": ["a barber's shop at closing", "a cluttered watchmaker's bench",
                  "a laundromat at 2am", "a grandmother's kitchen", "a hotel corridor",
                  "a library reading room"],
    "food": ["a split pomegranate", "noodles lifted from broth", "a burnt loaf",
             "oysters on ice", "a melting ice cream", "peppers drying on a string"],
    "textiles": ["a wind-filled sheet on a line", "a frayed silk kimono",
                 "woven baskets stacked", "a knitted sweater unravelling", "embroidered cuffs"],
    "weather": ["a dust storm arriving", "the first snow on a city street",
                "heat shimmer over asphalt", "fog rolling through a valley",
                "a squall over harbour water"],
    "machines": ["a printing press mid-run", "a bicycle drivetrain", "a loom in motion",
                 "an engine block on a bench", "a pinball machine's underside"],
    "botanical": ["a fig cut open", "seed heads gone to fluff", "roots in a glass jar",
                  "moss on a north wall", "a flowering cactus"],
    "water": ["a diver breaking the surface", "koi under lily pads", "a canal lock filling",
              "rain on a car windscreen", "waves against a breakwater"],
    "ritual": ["a tea ceremony", "candles floated on a river", "bread broken at a table",
               "a barber's straight razor", "a boat being blessed"],
}

# ---------------------------------------------------------------- grammars
# The sentence itself changes. `{s}` is the subject, `{m}` the medium and its
# clause, `{l}` light, `{p}` palette. Distance, angle and tense all move.
GRAMMARS = [
    "extreme close-up, {s}. {m}. {l}, {p}",
    "{s}, shot from directly overhead, flat lay. {m}. {l}, {p}",
    "wide shot, {s} small in the frame, vast negative space. {m}. {l}, {p}",
    "portrait of {s}, shoulders up, looking just past the camera. {m}. {l}, {p}",
    "{s}, caught mid-movement, motion blur. {m}. {l}, {p}",
    "{s} seen through a rain-streaked window. {m}. {l}, {p}",
    "{s}, reflected in a cracked mirror. {m}. {l}, {p}",
    "a crowded frame, {s} among many overlapping shapes. {m}. {l}, {p}",
    "{s} in silhouette against a bright ground. {m}. {l}, {p}",
    "low angle, {s} towering over the viewer. {m}. {l}, {p}",
    "{s}, cropped tight and off-centre, edges cut by the frame. {m}. {l}, {p}",
    "a quiet detail at the edge of {s}. {m}. {l}, {p}",
]

# Light and palette are drawn independently of any diurnal arc, because the
# first pass's arc quietly locked every image into the same cold blue.
LIGHT = [
    "hard side light, deep shadow", "flat overcast light", "warm lamplight from below",
    "backlit, rim glow", "dappled light through leaves", "harsh direct flash",
    "candlelight", "green fluorescent tube light", "golden late afternoon sun",
    "grey pre-dawn light", "light through coloured glass", "single bare bulb",
]
PALETTE = [
    "ochre, oxblood and cream", "acid green against black", "warm greys and dusty pink",
    "cobalt and marigold", "sepia and bone", "hot magenta and cyan",
    "earth reds and umber", "pale mint and coral", "deep purple and gold",
    "high-contrast black and white", "faded pastel primaries", "rust, teal and sand",
]


def new_sequence(rng):
    """One committed concept. Everything inside a sequence shares it."""
    medium, clause = rng.choice(MEDIA)
    world = rng.choice(list(WORLDS))
    return {
        "medium": medium,
        "medium_clause": clause,
        "world": world,
        "grammar": rng.choice(GRAMMARS),
        "light": rng.choice(LIGHT),
        "palette": rng.choice(PALETTE),
    }


def variation(rng, seq, index, previous=None):
    """A sibling within the sequence.

    Medium, world and palette are the sequence's identity and never move here:
    letting palette drift per frame was the first draft's mistake, and it made
    a "sequence" just four unrelated images again. What moves is the subject,
    the light, and -- once the concept is established -- the framing.

    The subject is drawn excluding the previous one, because rng.choice over a
    six-item pool repeats often enough to look like a stuck loop.
    """
    v = dict(seq)
    pool = [s for s in WORLDS[seq["world"]] if s != (previous or {}).get("subject")]
    v["subject"] = rng.choice(pool or WORLDS[seq["world"]])
    if rng.random() < 0.5:
        v["light"] = rng.choice(LIGHT)
    if index >= 2 and rng.random() < 0.5:
        v["grammar"] = rng.choice(GRAMMARS)
    return v


def compose(v):
    return v["grammar"].format(
        s=v["subject"],
        m=f"{v['medium']}, {v['medium_clause']}",
        l=v["light"],
        p=v["palette"],
    )


def describe(seq):
    return f"{seq['medium']} / {seq['world']} / {seq['palette']}"


if __name__ == "__main__":
    # Eyeball the spread: three sequences, three variations each.
    rng = random.Random(11)
    for _ in range(3):
        seq = new_sequence(rng)
        print(f"\n=== {describe(seq)}")
        for i in range(3):
            print("  -", compose(variation(rng, seq, i)))
