#!/usr/bin/env python3
"""drift_language, third rewrite — the short-prompt version.

The second generator failed by addition. Every prompt carried a medium, an
abstract emotional charge, a narrative beat, a framing, a posture, a light
recipe, a palette recipe and a surface flaw — sixty to eighty words of
competing instructions. FLUX satisfied none of them strongly and averaged
toward murk: the wall filled with underlit brown-grey pictures. The two best
frames of the night came from the two shortest prompts.

Three rules now.

ONE SENTENCE OF SUBJECT. The subject phrase carries the story and the feeling
as concrete visible fact — "a street musician playing trumpet to nobody in
the rain" needs no charge, no beat, no posture caption. If the emotion is not
in what the camera can see, it is not in the prompt.

ONE MOOD, NOT THREE RECIPES. Light + palette + flaw collapsed into a single
fused clause, and the pool spans bright, airy and colourful as well as dark.
The old LIGHT list was eight flavours of chiaroscuro and the old PALETTE six
flavours of grey; together they guaranteed a gallery of gloom.

THE STORY IS A CAMERA MOVE. Four beats of narrative caption ("the mistake",
"the room afterwards, emptied") are unrenderable; four steps of camera are
not. A sequence walks one arc — approach, retreat, orbit, descend — and the
delta between siblings is visible without being explained.

Total budget: 30–40 words. Every word is something a camera could verify.
"""
import random

# ---------------------------------------------------------------- media
# (name, surface clause, weight, family, tonal)
# The clause is the medium's physicality — flaw and surface folded in, kept
# under a dozen words. Weights follow what actually looked good on the wall:
# the anime still and the ink wash carried the night; oil went to mud.
MEDIA = [
    ("90s anime film still", "hand-painted background, soft film grain, warm analogue colour", 5, "screen", False),
    ("cel animation still", "flat colour, confident ink outlines, painted background", 4, "screen", False),
    ("sumi-e ink wash", "wet black ink blooming into absorbent paper, one decisive stroke, vast empty space", 5, "ink", True),
    ("woodblock print", "carved flat colour planes, bold outlines, brightest areas left as bare paper", 4, "print", False),
    ("risograph print", "two inks slightly misregistered, coarse halftone, highlights are unprinted paper", 4, "print", False),
    ("linocut print", "bold white lines gouged through solid black ink, no grey and no shine", 3, "print", True),
    ("gouache painting", "opaque matte pigment, ragged brush edges, simplified shapes", 3, "paint", False),
    ("watercolour", "wet blooming edges, granulating pigment, highlights left as bare paper", 3, "paint", False),
    ("oil painting", "thick impasto strokes, palette-knife ridges, wet-into-wet colour", 2, "paint", False),
    ("charcoal drawing", "rough black strokes, smudged tone, paper showing through", 2, "ink", True),
    ("tintype photograph", "silver halation, shallow focus, hand-poured chemical edges", 3, "photo", True),
    ("16mm film still", "grainy colour reversal stock, halated highlights, handheld framing", 2, "photo", False),
    ("stained glass panel", "black leading, saturated transmitted light, flat jewel colour", 3, "craft", False),
    ("paper collage", "torn coloured paper layers, hard cut edges, visible overlaps", 2, "craft", False),
    ("etching", "fine bitten lines, plate tone, wiped margins", 4, "print", True),
    ("screenprint", "flat opaque inks, hard stencil edges, one colour off-register", 4, "print", False),
    ("scratchboard", "white lines scratched out of solid black", 3, "ink", True),
    ("egg tempera panel", "thin translucent layers, luminous flat colour", 3, "paint", False),
    ("soft pastel", "chalky broken colour, paper tooth showing", 3, "paint", False),
    ("fresco fragment", "pigment sunk into plaster, chalky matte, cracked and lost patches", 3, "craft", False),
    ("glass mosaic", "irregular tesserae, grout lines, colour shifting tile to tile", 2, "craft", False),
    ("cyanotype", "deep Prussian blue, white silhouettes, brush-coated edges", 3, "photo", True),
]

# ---------------------------------------------------------------- subjects
# Each subject is a complete miniature scene: an action plus one telling
# detail. This line does the work CHARGES, ARCS and POSTURE_ARCS used to
# fail at, because it is made of things that show up in pixels.
WORLDS = {
    "figures": [
        "an old fisherman coiling rope on a dock, cigarette clamped in his teeth",
        "a girl laughing so hard she has to grip the table",
        "a barber sweeping hair across the floor of his empty shop",
        "a night-shift nurse asleep upright on the last bus",
        "a street musician playing trumpet to nobody in the rain",
        "a woman with grey braids shelling peas into a steel bowl",
    ],
    "hands": [
        "flour-dusted hands folding dough over itself",
        "a potter's hands closing around wet clay on the wheel",
        "hands striking a match, cupped against the wind",
        "hands threading a needle by lamplight",
        "a child's hands releasing a paper boat onto dark water",
    ],
    "creatures": [
        "a heron frozen mid-strike over dark water",
        "a fox asleep in a patch of sun",
        "a horse shaking off water in a bright spray",
        "a moth resting on a lit windowpane at night",
        "a black cat crossing an empty road",
    ],
    "streets": [
        "a night market under strings of paper lanterns",
        "an empty tram stop in the rain",
        "laundry strung between buildings, filled by wind",
        "a pyramid of oranges on a fruit stall under a striped awning",
        "a narrow staircase alley climbing between shuttered houses",
    ],
    "interiors": [
        "a laundromat at 2am, one dryer still turning",
        "a watchmaker's bench crowded with tiny tools",
        "a grandmother's kitchen mid-morning, steam rising from a pot",
        "an empty theatre with one work light on the stage",
        "a swimming pool at night, lit only from underwater",
    ],
    "food": [
        "a split pomegranate spilling seeds",
        "noodles lifted steaming from broth",
        "peppers drying on a string against a whitewashed wall",
        "a whole fish on ice, eye still bright",
        "a burnt loaf cooling on a windowsill",
    ],
    "weather": [
        "a dust storm swallowing the end of a street",
        "the first snow falling past a streetlamp",
        "fog erasing all but the nearest trees",
        "a squall crossing harbour water",
        "heat shimmer over an empty road",
    ],
    "water": [
        "a diver breaking the surface in a ring of spray",
        "koi crowding under lily pads",
        "rain running down a window, the street beyond dissolved",
        "waves shattering against a black breakwater",
    ],
    "machines": [
        "a bicycle drivetrain in motion, chain a silver blur",
        "a printing press mid-run, fresh sheets flying",
        "a loom mid-weave, warp threads under tension",
        "a pinball machine glowing in a dark arcade",
    ],
    "music": [
        "a cellist's bow lifted at the end of a phrase",
        "a brass band marching through a narrow street",
        "a piano with its lid open and no one playing",
        "a singer with her eyes shut, mid-note",
        "a drum kit abandoned under stage lights",
    ],
    "fire": [
        "a bonfire collapsing into embers",
        "a welder's arc lighting a dark shop",
        "candles guttering at the end of a long table",
        "a struck match at the moment it catches",
        "a forge with iron glowing orange",
    ],
    "thresholds": [
        "a door left open onto a bright yard",
        "a curtain lifting in a doorway",
        "a gate rusted half open",
        "a train window with the platform sliding past",
        "a stairwell turning out of sight",
    ],
    "sleep": [
        "a child asleep across two chairs",
        "a dog dreaming, paws twitching",
        "an old man asleep in a barber's chair",
        "someone asleep on a train, head against glass",
    ],
    "botanical": [
        "a fig cut open",
        "seed heads gone to silver fluff",
        "moss swallowing a stone wall",
        "a flowering cactus on a windowsill",
    ],
}

# ---------------------------------------------------------------- details
# One concrete, camera-visible thing that changes per frame. With the subject
# fixed and only the camera moving, siblings converged -- four pomegranates a
# judge could not tell apart. Narrative beats were the wrong fix for that
# (unrenderable); a different physical fact in the frame is the right one.
UNIVERSAL_DETAILS = [
    "half hidden, most of it out of sight",
    "reduced to a silhouette against the light",
    "only a fragment of it inside the frame",
    "seen through something in the way",
    "reflected, the thing itself out of frame",
    "repeated across the frame, many of them",
]

DETAILS = {
    "music": ["mid-gesture, arm at full extension", "hands blurred with movement",
              "the instrument alone, the player gone", "seen from behind the performer"],
    "fire": ["at its brightest, everything else lost", "down to embers",
             "only the light it throws on a wall", "reflected in someone's face"],
    "thresholds": ["nothing visible beyond it", "a figure just leaving frame",
                   "light spilling through from the far side", "closed, from the outside"],
    "sleep": ["face turned away", "only a hand visible", "seen from across the room",
              "close enough to see breathing"],
    "figures": ["a dog watching from the edge of the frame", "rain starting to fall",
                "a cigarette burned down to the filter", "someone else's hand entering frame",
                "a radio playing on a crate", "a broken chair pushed aside"],
    "hands": ["flour on the sleeve", "a wedding ring set down beside them",
              "a cut on one knuckle", "steam rising past the wrist",
              "a cat's tail crossing the frame"],
    "creatures": ["absolutely still", "caught at full stretch",
                  "head turned away", "half hidden behind something",
                  "only its reflection visible, itself out of frame"],
    "streets": ["a bicycle propped against the wall", "a dog asleep in the doorway",
                "a torn poster peeling off brick", "steam venting from a grate",
                "one shutter half open"],
    "interiors": ["a coat left over the back of a chair", "one bulb flickering",
                  "a cup of tea gone cold", "a cat asleep on the counter",
                  "a window open onto the dark"],
    "food": ["a knife left beside it", "flies circling", "a spill running off the edge",
             "a chipped enamel plate", "salt scattered across the surface"],
    "weather": ["a bicycle blown flat", "an umbrella turned inside out",
                "birds scattering", "a plastic bag lifted high", "a shutter banging"],
    "water": ["seen from below, through the surface", "only ripples where it was",
              "frozen mid-motion, spray suspended", "half above and half below the waterline",
              "seen through rain on glass"],
    "machines": ["oil pooled beneath it", "a spanner left on the housing",
                 "a warning label peeling", "sparks jumping", "a gloved hand steadying it"],
    "botanical": ["crushed flat", "in silhouette against the light",
                  "half eaten away", "reduced to a single specimen on white",
                  "overgrown until the container is invisible"],
}

# ---------------------------------------------------------------- moods
# One fused clause replacing LIGHT + PALETTE + FLAW. Tonal media draw from
# the mono pool; everything else gets colour — and the colour pool commits
# to bright and saturated as often as dark, on purpose.
MONO_MOODS = [
    "dense black against untouched white, the subject holding one side of the frame",
    "pale grey washes, the darkest mark saved for one small place",
    "deep shadow swallowing half the frame, one bright edge",
    "morning mist, forms dissolving at their edges",
    "hard low light raking across the surface, long shadows",
]

COLOR_MOODS = [
    "late golden sun and long shadows, warm ochre and dust",
    "flat overcast light, quiet greys, one red accent",
    "hard noon glare, bleached pale ground, a wedge of deep shadow",
    "blue evening, one window glowing amber",
    "just after rain, wet surfaces doubling every light",
    "early morning haze, pale mint and cream",
    "night under a single sodium lamp, amber against black",
    "high clear daylight, saturated colour, crisp shadows",
    "deep green shade, one shaft of hot white sun",
    "dusty pink dusk, everything softening",
    "cold blue snow light, one warm window",
    "firelight only, orange on the near side, black behind",
    "sun through leaves, dappled gold on shadow",
    "grey sea light, muted colour, one flash of red",
]

# ---------------------------------------------------------------- camera
# The narrative arc, made of framings instead of captions. Frame 1 to frame 4
# is a move the eye can follow on the wall without reading anything.
# Flat, frontal alternatives to the wide shot. The sentinel named "receding
# perspective corridor" as the dominant motif twice: a wide framing that does
# not say how the space is organised gets one-point perspective by default.
FLAT_WIDE = [
    "far away and flat-on, the space reading as stacked bands not depth",
    "far away, seen square against a wall with no vanishing point",
    "small against a large flat field of one colour",
]

CAMERA_ARCS = [
    ("approach", ["far away, low and far left, rest empty",
                  "at middle distance, pushed to one side",
                  "close, running off one edge",
                  "in extreme close-up, cropped by the frame edge"]),
    ("retreat", ["in extreme close-up, cropped by the frame edge",
                 "close, running off one edge",
                 "at middle distance, pushed to one side",
                 "far away, high and far right, rest empty"]),
    ("orbit", ["seen from the front, set low in the frame",
               "seen from the side, in profile",
               "seen from behind",
               "seen from directly above"]),
    ("descend", ["from high above, ground a flat pattern",
                 "at eye level, well off to one side",
                 "from low, looking up",
                 "at ground level, towering overhead"]),
]

# Orbit only makes sense around something with a front and a back.
_ORBIT_WORLDS = {"figures", "creatures", "machines", "interiors"}


def _pick_arc(rng, world):
    pool = [a for a in CAMERA_ARCS if a[0] != "orbit" or world in _ORBIT_WORLDS]
    return rng.choice(pool)


def _mood_for(rng, tonal):
    # Even a tonal medium gets colour sometimes: a tintype can be toned, a
    # linocut can be printed in two inks. Without this the mono media -- which
    # carry the heaviest weights because they make the best surfaces -- turn
    # the whole wall grey.
    if tonal and rng.random() < 0.7:
        return rng.choice(MONO_MOODS)
    return rng.choice(COLOR_MOODS)


def new_sequence(rng):
    """One committed concept: medium, subject, mood, camera arc."""
    name, clause, _w, family, tonal = rng.choices(
        MEDIA, weights=[m[2] for m in MEDIA], k=1)[0]
    world = rng.choice(list(WORLDS))
    arc = _pick_arc(rng, world)
    return {
        "medium": name,
        "medium_clause": clause,
        "family": family,
        "tonal": tonal,
        "world": world,
        "subject": rng.choice(WORLDS[world]),
        "mood": _mood_for(rng, tonal),
        "arc": arc,
        "framing": arc[1][0],
    }


def paired_sequence(rng, seq):
    """The counter-voice: same subject and camera arc, a medium from another
    family, its own mood. The dialogue is two surfaces disagreeing about one
    thing, not two unrelated pictures."""
    others = [m for m in MEDIA if m[3] != seq["family"]]
    # If the first voice is monochrome, the answering voice brings colour.
    # Two tonal media on the same subject is not a dialogue, it is an echo.
    if seq.get("tonal"):
        coloured = [m for m in others if not m[4]]
        others = coloured or others
    name, clause, _w, family, tonal = rng.choices(
        others, weights=[m[2] for m in others], k=1)[0]
    other = dict(seq)
    other.update(medium=name, medium_clause=clause, family=family, tonal=tonal,
                 mood=_mood_for(rng, tonal))
    return other


_DEPTH_PRONE = {"streets", "interiors"}


def variation(rng, seq, index, previous=None, last=3):
    """A sibling: everything held, only the camera moves along the arc.
    The per-frame seed supplies the rest of the difference. `previous` is
    accepted for API compatibility and unused — with the subject fixed for
    the whole sequence there is nothing to avoid repeating."""
    v = dict(seq)
    beats = seq["arc"][1]
    v["framing"] = beats[index % len(beats)]
    # Swap the depth-inviting wide shot for a flat one in the worlds that
    # collapse into corridors. Half the time, so the arc still has a far end.
    if (seq["world"] in _DEPTH_PRONE and "far away" in v["framing"]
            and rng.random() < 0.7):
        v["framing"] = rng.choice(FLAT_WIDE)
    # A world is not a subject: "water" holds a diver, a koi and rain on glass,
    # so a detail assuming anatomy is discarded for most of them -- which is how
    # siblings collapsed back into pairs. Alternate with a universal pool.
    pool = UNIVERSAL_DETAILS if index % 2 else (DETAILS.get(seq["world"]) or UNIVERSAL_DETAILS)
    v["detail"] = pool[index % len(pool)]
    return v


def _cap(s):
    return s[0].upper() + s[1:]


def _an(noun):
    return "An" if noun[0].lower() in "aeiou" else "A"


def compose(v):
    # Three sentences, 30-40 words: what it is and how it is framed; how the
    # surface is made; what the light and colour are doing. Nothing else.
    detail = f", {v['detail']}" if v.get("detail") else ""
    return (f"{_an(v['medium'])} {v['medium']} of {v['subject']}{detail}, "
            f"{v['framing']}. {_cap(v['medium_clause'])}. {_cap(v['mood'])}.")


def describe(seq):
    return f"{seq['medium']} / {seq['world']} / {seq['arc'][0]} / {seq['mood']}"


if __name__ == "__main__":
    rng = random.Random(7)
    for _ in range(4):
        seq = new_sequence(rng)
        pair = paired_sequence(rng, seq)
        print(f"\n=== {describe(seq)}")
        for i in range(4):
            src = seq if i % 2 == 0 else pair
            p = compose(variation(rng, src, i))
            print(f"  [{len(p.split())}w] {p}")
