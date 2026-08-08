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
# Framing only. The medium, light and palette are no longer interleaved here:
# the sentence has to be able to open with what the image is ABOUT, and a
# template that starts with an inventory of materials can never do that.
GRAMMARS = [
    "in extreme close-up, {s}",
    "{s}, shot from directly overhead",
    "{s} small in a vast empty frame",
    "{s}, shoulders up, looking just past the lens",
    "{s}, caught mid-movement",
    "{s} seen through a rain-streaked window",
    "{s}, reflected in a cracked mirror",
    "{s} among many overlapping bodies",
    "{s} in silhouette against a bright ground",
    "{s} from below, towering",
    "{s} cropped tight, cut by the frame edge",
    "a quiet detail at the edge of {s}",
]

# The governor: describe the intent of the lens, not the subject. A charge is
# what the frame is about; the model renders it as distortion and light rather
# than as a neutral depiction. This is the layer that separates a competent
# picture of a thing from a picture that means something.
CHARGES = [
    "the desperate tension of a secret exchange",
    "the exhaustion at the end of an eighteen-hour shift",
    "the tenderness of a gesture nobody was meant to see",
    "the moment authority stops being obeyed",
    "grief that has not yet been admitted",
    "the swagger of someone who knows they are being watched",
    "a joke landing badly",
    "the relief of putting something heavy down",
    "the arrogance of a thing built to outlast its makers",
    "the embarrassment of being caught needing help",
    "reverence performed for an audience",
    "the panic under a calm surface",
]

# A sequence is a story, not four attempts at one image. The art is in the
# delta between frames. Each arc is four beats that must happen in order.
ARCS = [
    ("approach", ["long before it happens, everything still ordinary",
                  "the first sign that something is wrong",
                  "the instant it breaks",
                  "the room afterwards, emptied"]),
    ("closeness", ["watched from across the street",
                   "close enough to hear, still unnoticed",
                   "close enough to touch",
                   "too close; the spell broken"]),
    ("labour", ["the work begun, hands clean",
                "deep in it, no longer careful",
                "the mistake",
                "the thing finished, imperfect, set down"]),
    ("weather", ["the light going strange",
                 "the first of it arriving",
                 "the full force, nothing else visible",
                 "the stillness after, everything changed"]),
    ("ceremony", ["preparation, nobody present yet",
                  "the gathering, self-conscious",
                  "the act itself",
                  "dispersal, the ordinary returning"]),
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


# The governor's note, and the single biggest lift: signal the physical
# struggle of the medium over the perfection of the latent space. Without this
# the output is a clean render OF a woodcut; with it, it is a woodcut.
IMPERFECTION = [
    "visible brush bristles dragged through the pigment",
    "ink bleeding past its intended edge",
    "fingerprints pressed into the material",
    "chemical streaks and uneven development at the edges",
    "a torn edge and a crease across the surface",
    "misregistered colour, one plate printed a millimetre off",
    "dust and hair caught in the emulsion",
    "the ground showing through where the pigment ran thin",
    "an overworked passage where the surface has gone dull",
]

# Not every medium survives every framing. Motion blur over a cluttered macro
# subject is brown soup whatever the model does, and a fine ink wash cannot
# hold a crowded frame. These are the pairings observed to fail; excluding
# them is worth more than adding ten more adjectives.
GRAMMAR_BANS = {
    "sumi-e ink wash": {"a crowded frame", "reflected in a cracked mirror", "through a rain-streaked window"},
    "stop-motion puppet, claymation": {"caught mid-movement", "a crowded frame"},
    "stained glass panel": {"caught mid-movement", "motion blur", "a quiet detail"},
    "graphite drawing": {"a crowded frame"},
    "tintype photograph": {"caught mid-movement"},
    "technical blueprint": {"caught mid-movement", "a crowded frame"},
}

# Media that reliably produce a strong graphic image get drawn more often. This
# is curation, not fairness: an even spread over the space spends most of the
# night in its weak half.
MEDIA_WEIGHTS = {
    "sumi-e ink wash": 5, "woodblock print": 4, "stained glass panel": 4,
    "risograph print": 4, "cel animation": 4, "gouache illustration": 3,
    "90s anime film still": 3, "collage of torn paper": 3, "watercolour": 3,
    "tintype photograph": 3, "graphite drawing": 2, "oil painting, impasto": 2,
    "stop-motion puppet, claymation": 2, "16mm documentary still": 2,
    "technical blueprint": 1, "airbrushed 70s paperback cover": 1,
}


# A flaw has to belong to its medium: "brush bristles" on a risograph is not
# imperfection, it is a wrong word. Grouped by how the surface is actually made.
FLAWS_BY_FAMILY = {
    "paint": ["visible brush bristles dragged through the pigment",
              "the ground showing through where the pigment ran thin",
              "an overworked passage where the surface has gone dull"],
    "ink": ["ink bleeding past its intended edge",
            "a stray splatter across the empty space",
            "the brush running dry at the end of the stroke"],
    "print": ["misregistered colour, one plate printed a millimetre off",
              "ink starved in patches, the paper showing through",
              "a torn edge and a crease across the surface"],
    "photo": ["chemical streaks and uneven development at the edges",
              "dust and hair caught in the emulsion",
              "light leak bleeding in from one corner"],
    "hand": ["fingerprints pressed into the material",
             "a seam left visible where two pieces meet",
             "a thumbprint smudged into a soft edge"],
}
MEDIUM_FAMILY = {
    "cel animation": "paint", "90s anime film still": "paint",
    "gouache illustration": "paint", "oil painting, impasto": "paint",
    "watercolour": "ink", "sumi-e ink wash": "ink", "graphite drawing": "ink",
    "risograph print": "print", "woodblock print": "print",
    "collage of torn paper": "print", "technical blueprint": "print",
    "stained glass panel": "hand", "stop-motion puppet, claymation": "hand",
    "tintype photograph": "photo", "16mm documentary still": "photo",
    "airbrushed 70s paperback cover": "paint",
}


def pick_flaw(rng, medium):
    return rng.choice(FLAWS_BY_FAMILY[MEDIUM_FAMILY.get(medium, "paint")])


def allowed_grammars(medium, world=None):
    banned = set(GRAMMAR_BANS.get(medium, set()))
    # "Among many overlapping bodies" needs bodies; over a cactus it is just a
    # word the model has to reconcile, and it reconciles it badly.
    if world is not None and world not in HUMAN_WORLDS:
        banned.add("overlapping bodies")
    keep = [g for g in GRAMMARS if not any(b in g for b in banned)]
    return keep or GRAMMARS


# A charge has to be possible for its world. "The desperate tension of a secret
# exchange" over a sleeping fox is not evocative, it is a category error, and
# the model renders the confusion faithfully.
HUMAN_WORLDS = {"faces", "hands", "crowds", "ritual", "interiors"}
OBJECT_CHARGES = [
    "the arrogance of a thing built to outlast its makers",
    "the dignity of something worn out by use",
    "the violence held in a stopped machine",
    "abundance about to spoil",
    "the indifference of weather to anyone watching",
    "a small thing insisting on being noticed",
]
WORLD_ARCS = {
    "weather": "weather", "machines": "labour", "hands": "labour",
    "ritual": "ceremony", "crowds": "ceremony", "food": "labour",
    "faces": "closeness", "interiors": "approach", "creatures": "closeness",
    "botanical": "weather", "water": "weather", "textiles": "labour",
}


# The governor: frame 1 and frame 4 must differ in the subject's state, or the
# delta is incidental. A posture progression makes the arc visible in the body
# rather than only in the caption.
POSTURE_ARCS = [
    ("tighten", ["loose, unaware", "attention caught, stilling",
                 "braced, committed", "held rigid at the limit"]),
    ("release", ["braced, holding everything in", "the first give",
                 "letting go, off balance", "loosened, the weight set down"]),
]

# Two media are in dialogue when they disagree about how a surface is made.
# Pairing within a family (two paints, two prints) reads as indecision.
def contrasting_medium(rng, medium):
    family = MEDIUM_FAMILY.get(medium, "paint")
    others = [(m, c) for m, c in MEDIA if MEDIUM_FAMILY.get(m, "paint") != family]
    weights = [MEDIA_WEIGHTS.get(m, 2) for m, _ in others]
    return rng.choices(others, weights=weights, k=1)[0]


def pick_charge(rng, world):
    return rng.choice(CHARGES if world in HUMAN_WORLDS else OBJECT_CHARGES)


def pick_arc(rng, world):
    want = WORLD_ARCS.get(world)
    for name, beats in ARCS:
        if name == want:
            return (name, beats)
    return rng.choice(ARCS)


def new_sequence(rng):
    """One committed concept. Everything inside a sequence shares it."""
    medium, clause = rng.choices(MEDIA, weights=[MEDIA_WEIGHTS.get(m, 2) for m, _ in MEDIA], k=1)[0]
    world = rng.choice(list(WORLDS))
    arc = pick_arc(rng, world)
    arc_name = arc[0]
    return {
        "medium": medium,
        "medium_clause": clause,
        "world": world,
        "grammar": rng.choice(allowed_grammars(medium, world)),
        "charge": pick_charge(rng, world),
        "arc": arc,
        # One story means one subject: the arc is carried by the beats and the
        # framing, not by swapping what we are looking at every frame.
        "subject": rng.choice(WORLDS[world]),
        # The body must agree with the story: a beat that disperses cannot land
        # on a posture that tightens, or the frame argues with its own caption.
        "posture_arc": next(pa for pa in POSTURE_ARCS
                            if pa[0] == ("tighten" if arc_name == "closeness" else "release")),
        "light": rng.choice(LIGHT),
        "palette": rng.choice(PALETTE),
        "flaw": pick_flaw(rng, medium),
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
    if rng.random() < 0.5:
        v["light"] = rng.choice(LIGHT)
    if index >= 2 and rng.random() < 0.5:
        v["grammar"] = rng.choice(allowed_grammars(seq["medium"], seq["world"]))
    if rng.random() < 0.4:
        v["flaw"] = pick_flaw(rng, seq["medium"])
    beats = seq["arc"][1]
    v["beat"] = beats[index % len(beats)]
    postures = seq["posture_arc"][1]
    v["posture"] = postures[index % len(postures)]
    return v


def compose(v):
    # Intent first, subject second, materials last. The order is the point:
    # what the picture is about has to reach the model before the inventory.
    return (
        f"{v['medium']} capturing {v['charge']}. "
        f"{v['beat'].capitalize()}: {v['grammar'].format(s=v['subject'])}, {v['posture']}. "
        f"{v['medium_clause']}. {v['light']}, {v['palette']}. {v['flaw']}"
    )


def paired_sequence(rng, seq):
    """The counter-voice: same subject and charge, a medium from another family.

    Not an independent second sequence -- that is just more images. The dialogue
    only exists if both halves are about the same thing.
    """
    medium, clause = contrasting_medium(rng, seq["medium"])
    other = dict(seq)
    other["medium"] = medium
    other["medium_clause"] = clause
    other["grammar"] = rng.choice(allowed_grammars(medium, seq["world"]))
    other["flaw"] = pick_flaw(rng, medium)
    other["palette"] = rng.choice(PALETTE)
    return other


def describe(seq):
    return f"{seq['medium']} / {seq['world']} / {seq['arc'][0]} / {seq['charge']}"


if __name__ == "__main__":
    # Eyeball the spread: three sequences, three variations each.
    rng = random.Random(11)
    for _ in range(3):
        seq = new_sequence(rng)
        print(f"\n=== {describe(seq)}")
        for i in range(3):
            print("  -", compose(variation(rng, seq, i)))
