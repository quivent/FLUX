#!/home/dev/venv/bin/python
"""Structural vacuuming: strip the subject until only atmosphere is left.

The governor's stage 2, and the fix for the failure we hit today. Asked for a
robot alone at 3am in an empty laundromat, the model painted a child into the
frame -- it fills any space it is not explicitly told to leave. His diagnosis was
sharper than mine: do not describe the void, describe THE BOUNDARY OF THE VOID.
"empty white space" is an instruction the model can ignore; "the edge where the
snowfield stops and the white air begins" is a thing it must draw.

So each subject here exists at five levels of removal. Level 0 is a scene. By
level 4 the subject is gone entirely and only the boundary condition remains --
the light, the edge, the weather. The keep rule is his: keep the frames where the
model FAILS to hallucinate a subject and gives us atmosphere instead.

Also his: ban the quality adjectives. "stunning", "detailed", "hyper-realistic",
"masterpiece" are tokens that pull toward the dead centre of the distribution --
the statistical average of everything ever labelled good. They are the opposite
of a search for the rare.
"""

BANNED = ("stunning", "detailed", "hyper-realistic", "hyperrealistic",
          "masterpiece", "beautiful", "gorgeous", "award-winning", "8k", "4k",
          "best quality", "ultra")

# Each entry descends: scene -> figure removed -> object removed -> trace only ->
# boundary condition alone. The last two are where the interesting failures live.
LADDERS = [
    [
        "A monk crossing a snowfield at dawn, small in the frame.",
        "A single line of footprints crossing a snowfield at dawn. No figure anywhere.",
        "The last footprint before the snow becomes unmarked. Nothing else.",
        "The edge where the snowfield stops and the white air begins, a faint blue "
        "shadow marking the boundary.",
        "The boundary between two whites: colder white below, warmer white above, "
        "one thin line of blue shadow where they meet. Nothing else in the frame.",
    ],
    [
        "A woman standing at the end of a long wooden pier at night, lantern in hand.",
        "A lantern left burning at the end of a long wooden pier at night. No person.",
        "The circle of lantern light on wet planking, the lantern out of frame.",
        "The exact edge where lamplight stops and the black water begins.",
        "The boundary between lit wood and unlit water, nothing else, the frame "
        "given entirely to that edge.",
    ],
    [
        "A child sitting inside a vast empty concert hall.",
        "A single lit seat in a vast dark concert hall. No people.",
        "The shaft of light that would fall on a seat, and the dust in it.",
        "The edge of a shaft of light meeting the dark of an enormous interior.",
        "The boundary where a volume of lit air ends and unlit air begins, "
        "architecture only implied.",
    ],
    [
        "A fisherman hauling a net in heavy fog at sea.",
        "An empty boat in heavy fog at sea. No figure.",
        "A rope disappearing into fog over the side of an unseen boat.",
        "The distance at which fog becomes total, water below still legible.",
        "The boundary where water stops being water and becomes fog, one gull's "
        "worth of scale and nothing else.",
    ],
    [
        "A pilot walking away from a landed aircraft in a desert at noon.",
        "A landed aircraft alone in a desert at noon. No people.",
        "The shadow of an aircraft on desert floor, the aircraft out of frame.",
        "The edge of a hard shadow on pale sand at noon.",
        "The boundary between two temperatures of light on sand, hard-edged, "
        "nothing else in the frame at all.",
    ],
    [
        "Two figures under one umbrella on a city street in rain.",
        "One open umbrella lying abandoned on a wet city street. No people.",
        "Rain rings spreading on a flooded street, the street empty.",
        "The edge where rain-pocked water meets dry pavement.",
        "The boundary between wet and dry stone, rendered as pure tonal difference, "
        "nothing else.",
    ],
]

# Craft: light and material only. No adjective may assert quality.
CRAFT = ("Painted anime background art. Exact control of light direction, "
         "atmospheric perspective, restrained palette, one dominant light source. "
         "Generous empty space, composition built on a single boundary. "
         "No text, no watermark, no signature, no border, no figures unless stated.")

NEG = ("people, person, figure, crowd, character, face, animal, text, watermark, "
       "signature, logo, border, frame, clutter, busy composition")


def prompt_for(line, treatment_body=None):
    p = f"{line} {CRAFT}"
    if treatment_body:
        p = f"{treatment_body} {p}"
    low = p.lower()
    for b in BANNED:                       # a guard, not a suggestion
        assert b not in low, f"banned quality adjective in prompt: {b}"
    return p


def every():
    """(ladder_index, depth, line) for every rung."""
    out = []
    for li, ladder in enumerate(LADDERS):
        for depth, line in enumerate(ladder):
            out.append((li, depth, line))
    return out
