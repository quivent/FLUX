#!/home/dev/venv/bin/python
"""Scenes, not labels.

Everything rendered so far obeyed one sentence: "vertical premium product label
illustration, one character, centred, head and shoulders, blank lower third".
That format cannot be funny, cannot be sad, and cannot hold a room full of
people -- it can only hold a portrait. So this abandons it. No card furniture,
no blank panel, no product. Full-bleed pictures with somewhere to look.

Each scene is written to be a specific picture rather than a mood word. "Sad" is
not a prompt; a robot in a laundromat folding clothes for an owner who is not
coming back is a prompt. The comedy scenes work the same way: the joke is in the
staging, and the render only has to be honest about it.
"""

# --- the joke is in the staging, not in the adjectives ----------------------
FUNNY = [
    "A colossal ancient dragon, scales like cracked obsidian, wedged awkwardly "
    "inside a tiny suburban laundromat, trying with enormous delicate claws to "
    "extract a single sock from a front-loading machine. Fluorescent lighting. "
    "One bored teenager in headphones does not look up from their phone.",

    "A samurai in full armour at a child's birthday party, seated cross-legged "
    "on a tiny plastic chair, wearing a paper party hat over his helmet, holding "
    "a slice of cake with immense ceremonial gravity while children scream around him.",

    "Two rival ninja assassins frozen mid-fight in a convenience store aisle, "
    "blades crossed, both staring in horror at the last remaining rice ball "
    "between them on the shelf.",

    "An enormous grandmotherly kaiju sitting in the sea, gently knitting a scarf "
    "long enough to wrap the harbour, while tiny military helicopters hover "
    "uncertainly, unsure whether to intervene.",

    "A stern demon lord on his throne of skulls, utterly defeated, holding a "
    "sleeping kitten on his lap and not moving a muscle in case he wakes it. "
    "His generals wait in the doorway, afraid to speak.",

    "A cat that has clearly just knocked a priceless vase off a shelf, caught in "
    "mid-air alongside the falling shards, wearing an expression of complete "
    "innocence, in an opulent collector's study.",
]

# --- restraint does the work; nothing here announces itself as sad -----------
SAD = [
    "An old repair robot alone in a closed laundromat at 3am, carefully folding "
    "a child's small yellow raincoat that it has clearly folded many times "
    "before. Rain on the windows. One flickering tube light. No one else.",

    "A young woman standing at a train window at dusk, watching a station recede, "
    "her hand pressed flat to the glass, an empty seat beside her with a second "
    "unopened bento on it.",

    "An enormous gentle forest spirit sitting in a clear-cut hillside of stumps, "
    "holding the last living sapling in cupped hands, mist rising, evening light.",

    "A very old swordsman polishing a blade in an empty dojo hung with faded "
    "photographs of students, dust motes in a shaft of late afternoon sun, "
    "every practice mat rolled and stacked.",

    "A girl in a hospital courtyard in winter releasing a paper crane into the "
    "air, wearing a coat far too big for her, footprints leading to her and none "
    "leading away.",

    "A lighthouse keeper's ghost setting the lamp at dusk out of habit, "
    "translucent, while below the automated beacon turns without him.",
]

# --- fictional, period, ornate: an atmosphere piece, not an endorsement ------
DEN = [
    "Interior of an opulent fictional opium den in an imagined port city: low "
    "lacquered couches, layered smoke lit in shafts by hanging jewelled lanterns, "
    "silk hangings, a dozen reclining figures rendered as sculptural shapes in "
    "the haze, an attendant moving between them with a long-stemmed pipe. "
    "Deep reds and gold, heavy atmosphere, cinematic.",

    "A fox spirit in an embroidered robe reclining in a smoky den, exhaling a "
    "long ribbon of smoke that curls into the shape of a distant city she is "
    "remembering. Lantern light, ornate wooden screens, patterned rugs.",

    "A detective in a rumpled coat standing in the doorway of a fictional opium "
    "den, backlit, silhouetted against the street, the smoke of the room curling "
    "around his ankles as every reclining face turns slowly toward him.",

    "An elderly proprietress of a fictional den counting coins by lamplight in a "
    "back room, ledgers and brass scales, the main room's smoke bleeding through "
    "a beaded curtain behind her.",
]

# --- maximalist: density is the subject --------------------------------------
INTRICATE = [
    "An impossible vertical city built inside a single enormous tree, hundreds "
    "of lantern-lit windows, staircases, laundry lines, market stalls and tiny "
    "figures at every level, rendered with obsessive detail, dusk.",

    "The interior of a clockmaker's workshop where the clocks are alive: hundreds "
    "of brass mechanisms with tiny faces, gears, springs and pendulums, the "
    "clockmaker asleep at the bench among them.",

    "A grand library where the books are birds, thousands of them roosting on "
    "shelves and wheeling through shafts of light in a vast domed hall, a single "
    "librarian below with a net.",

    "A many-armed kitchen goddess preparing a hundred dishes at once in a "
    "cavernous temple kitchen, every arm mid-task, steam, copper pots, chaos held "
    "in perfect balance.",

    "A festival night market seen from above: hundreds of stalls, paper lanterns, "
    "fireworks, crowds in yukata, food smoke, a river of people through narrow "
    "streets, every stall individually detailed.",

    "A giant sleeping beast whose body has become an inhabited landscape: villages "
    "on its back, terraced fields down its flank, bridges between its horns, "
    "smoke from a hundred chimneys.",
]

# --- deliberately hard to predict --------------------------------------------
UNEXPECTED = [
    "A funeral at sea for a robot, held by other robots, each holding an umbrella "
    "in the rain although none of them need one.",

    "A tea ceremony conducted in absolute silence between a human and something "
    "enormous and unknowable that has folded itself politely into the small room, "
    "only part of it visible.",

    "An astronaut discovering a fully furnished Japanese living room, tatami and "
    "kotatsu and television, intact on the surface of an airless moon.",

    "A whale swimming slowly through the flooded main hall of a sunken train "
    "station, commuters' umbrellas drifting past the ticket gates.",

    "A duel between two masters fought entirely with calligraphy brushes, the "
    "strokes hanging in the air between them as living black shapes.",

    "A child leading an enormous armoured war machine by one finger through a "
    "field of flowers, the machine stepping with exaggerated care.",
]

MOODS = {"funny": FUNNY, "sad": SAD, "den": DEN,
         "intricate": INTRICATE, "unexpected": UNEXPECTED}

# Craft clauses applied to every scene. These are about execution, not content.
CRAFT = ("Highly detailed anime illustration, masterful composition with a clear "
         "focal point and deep space, expressive character acting, confident "
         "linework, painterly cel shading with considered light direction, rich "
         "atmospheric perspective, film-still framing. No text, no watermark, "
         "no signature.")


def prompt_for(scene, treatment_body=None):
    """A scene, optionally rendered through one of the treatments."""
    if treatment_body:
        return f"{treatment_body} {scene} {CRAFT}"
    return f"{scene} {CRAFT}"


def every():
    out = []
    for mood, items in MOODS.items():
        for i, s in enumerate(items):
            out.append((mood, i, s))
    return out
