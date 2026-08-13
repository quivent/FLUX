#!/home/dev/venv/bin/python
"""Visionary beauty: awe rather than anecdote.

The scene set was narrative -- a joke, a loss, a room. This set is after the
sublime: scale, light, silence, the moment before or after an event rather than
the event. Nothing here has a punchline.

Two craft rules, both learned from today's failures rather than assumed:

EMPTINESS MUST BE COMMANDED, NOT IMPLIED. Asked for a robot alone at 3am, the
model painted a child into the frame and the sadness evaporated. It fills space
it is not told to leave. So every prompt that depends on emptiness states it as a
positive instruction -- "no other figures, the rest of the frame empty" -- rather
than trusting "alone" to do the work.

LIGHT IS THE SUBJECT. Beauty in this model tracks light description far more than
adjective stacking. "Beautiful" does nothing; "a single shaft of late sun through
dust, everything else in shadow" does the work.
"""

VISIONS = [
    "A vast cathedral of ice lit from beneath by something blue and slow, "
    "columns fading into darkness above, one small figure standing at the edge of "
    "the light with their back to us. No other figures. The upper two thirds are "
    "empty dark air.",

    "The moment a thousand paper lanterns are released over a black lake, seen "
    "from water level, each lantern doubled in the still surface, the far shore "
    "invisible. No people in frame.",

    "An enormous whale made of constellations drifting above a sleeping village, "
    "its body transparent, stars visible through it, snow falling upward into it.",

    "A single cherry tree in full blossom growing from the deck of a derelict "
    "battleship half-sunk in a calm sea at dawn, mist on the water, absolutely "
    "still, no figures anywhere.",

    "The inside of a wave the instant before it breaks, seen from within the "
    "barrel, sunlight fracturing through green water into a corridor of light.",

    "A staircase of clouds ascending into a sunset, each step a distinct layer of "
    "stratus lit a different colour, one tiny bird ascending, nothing else in the "
    "frame at all.",

    "A field of enormous glass flowers at night, each refracting the aurora "
    "overhead into a different colour, the horizon perfectly flat and empty.",

    "A city grown entirely from coral, drowned and lit by shafts of sunlight from "
    "the surface far above, fish moving through empty windows, no people, silence.",

    "The last tree on a mountaintop above a sea of cloud at dawn, its branches "
    "holding a hundred wind chimes, wind visible in the grass. Nothing else.",

    "An observatory dome opened to a meteor storm, the astronomer a small "
    "silhouette at the eyepiece, the entire upper frame given to the sky.",

    "A river of molten gold flowing through a canyon of black obsidian under a "
    "green aurora, the reflected light climbing the canyon walls.",

    "A colossal statue of a seated figure so old it has become a hill, forest "
    "growing from its shoulders, mist in its lap, a single flock of birds giving "
    "the scale.",

    "The moment of total eclipse over a still desert lake, the corona blazing, the "
    "landscape rendered in silver and shadow, one line of standing stones.",

    "A library whose windows open onto different weathers -- one snow, one storm, "
    "one summer noon -- light from each falling in a different colour across the "
    "same empty reading room.",

    "An enormous moon rising directly behind a mountain range so close that its "
    "craters are legible, a single lit farmhouse window in the foreground valley.",

    "A forest where every leaf is a small pane of stained glass, sunlight through "
    "the canopy throwing coloured light across the empty forest floor.",

    "The bow of a ship pushing through a sea of mirror-still mercury under a "
    "double sunset, the wake spreading in perfect concentric arcs, no crew visible.",

    "A shrine gate standing alone in shallow water at high tide, a typhoon sky "
    "behind it torn open by one shaft of gold light striking the gate exactly.",

    "Migration of enormous sky-whales past the window of a mountain teahouse at "
    "dusk, seen from inside the dark room, the whales lit from below by the town.",

    "A canyon whose walls are covered in bioluminescent handprints going back "
    "thousands of years, glowing faintly blue, the river below reflecting them, "
    "no figures present.",
]

# Craft applied to all of them. Light and composition, never adjectives about quality.
BANNED = ("stunning", "detailed", "hyper-realistic", "hyperrealistic",
          "masterpiece", "beautiful", "gorgeous", "award-winning", "8k", "4k",
          "best quality", "ultra", "masterful", "immaculate", "breathtaking",
          "ghibli", "shinkai", "artstation", "trending")

# Physical facts only. No artist, no quality claim, nothing that names an
# impression the render is supposed to make on a viewer. Every clause here is
# something a renderer can be wrong about.
CRAFT = ("Single dominant light source with a stated direction. Air with weight: "
         "haze thickening with distance so far things go pale and lose contrast. "
         "Three depth planes, one of them empty. Colour held to a narrow band with "
         "one departure from it. Horizon placed off centre. "
         "No text, no watermark, no signature, no border.")

NEG = ("cluttered, busy, extra figures, crowds, text, watermark, signature, "
       "logo, frame, border, low detail, flat lighting")


def prompt_for(v, treatment_body=None):
    p = f"{treatment_body} {v} {CRAFT}" if treatment_body else f"{v} {CRAFT}"
    low = p.lower()
    for b in BANNED:          # fail loudly rather than degrade a thousand images
        assert b not in low, f"banned quality adjective in prompt: {b}"
    return p


def every():
    return list(enumerate(VISIONS))
