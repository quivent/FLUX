#!/home/dev/venv/bin/python
"""Actual variation: whole treatments, not adjectives inside one template.

collection.STYLE hard-specifies the entire picture -- "vertical premium product
label illustration, {framing} of one character, centred, large in frame, head and
shoulders fully visible ... cel-shaded anime art ... flat cream paper background
... vintage apothecary label composition". The four axes only swap fragments
inside that sentence, and three of the four (line, ornament, and most framing
levels) are invisible at thumbnail size. Sweeping them produces one picture
wearing 144 hats.

A treatment replaces the whole style block: medium, movement, composition,
palette logic and light. Two cards from different treatments do not look like
settings of each other; they look like different artists were hired.

The one thing every treatment must preserve is the blank lower third -- compose()
stamps the product name, quote and house marks there, and art behind that type is
unreadable. That is a real constraint of the format, not a stylistic timidity.
"""

LOWER = ("The entire lower third of the image is an empty flat panel of plain "
         "background, completely blank, no detail. Absolutely no text anywhere: "
         "no letters, no words, no kanji, no calligraphy, no signage, no "
         "signature, no watermark.")

# name -> (medium/movement + composition + light, palette logic)
TREATMENTS = {
    "ukiyoe": (
        "Ukiyo-e woodblock print in the manner of Utamaro. Flat unmodulated colour "
        "fields, confident black keyblock outline, visible woodgrain and slight "
        "ink registration offset, mineral pigments on absorbent washi. {subject}. "
        "Bust composition against a flat bokashi gradient sky, no cast shadows.",
        "restrained mineral palette: indigo, vermilion, ochre, bone white"),
    "nouveau": (
        "Art Nouveau lithographic poster in the manner of Mucha. Ornate whiplash "
        "linework, decorative halo arch behind the head, flattened perspective, "
        "gold leaf accents, stylised hair as flowing ornament. {subject}. "
        "Three-quarter figure filling a tall panel, framed by a stylised botanical arch.",
        "muted sage, dusty rose, cream and old gold"),
    "riso": (
        "Two-colour risograph print. Visible halftone dot grain, deliberate "
        "misregistration between the two ink layers, coarse paper texture, no "
        "gradients. {subject}. Bold graphic silhouette, high figure-ground contrast.",
        "exactly two inks: fluorescent pink and deep blue, overprinting to violet"),
    "sumie": (
        "Sumi-e ink wash painting on rice paper. Wet brush, bleeding edges, "
        "enormous negative space, a single confident gesture describing the whole "
        "form, sparse and unhurried. {subject}. Small figure placed off-centre "
        "low in the frame, the rest empty paper.",
        "black ink in five tones, one small accent of colour and nothing else"),
    "shojo70": (
        "1970s shojo manga illustration. Screentone dots and hatching, oversized "
        "luminous eyes with multiple catchlights, soft airbrushed cheeks, floating "
        "roses and starbursts, dreamy soft focus. {subject}. Portrait with the "
        "figure dissolving into decorative flowers at the edges.",
        "pale rose, lilac and cream with sepia line"),
    "cel80s": (
        "1980s anime production cel, hand-painted acrylic on acetate over a "
        "painted background. Hard-edged colour separation, thick outline, airbrushed "
        "gradient sky, film grain and slight cel dust. {subject}. Low-angle heroic "
        "framing, dramatic rim light.",
        "saturated sunset: magenta, orange, cyan shadows"),
    "engraving": (
        "19th century copperplate botanical engraving. Fine parallel hatching and "
        "cross-hatching only, no solid fills, printed on toned laid paper with a "
        "plate mark. {subject}. Specimen-plate arrangement: the figure rendered as "
        "a scientific illustration with the botanical subject.",
        "monochrome sepia ink on warm paper, one hand-tinted accent"),
    "gouache": (
        "Mid-century gouache storybook illustration. Matte opaque paint, visible "
        "brush texture, simplified naive shapes, flat stylised perspective. "
        "{subject}. Full scene with the figure small in an interior, warm domestic "
        "clutter around them.",
        "chalky teal, mustard, brick and off-white"),
    "cyanotype": (
        "Cyanotype photogram on cotton rag. Deep Prussian blue ground, white "
        "silhouetted forms, soft edge bleed, uneven brushed emulsion border. "
        "{subject}. Botanical silhouettes overlapping the figure like a pressed "
        "specimen sheet.",
        "Prussian blue and paper white only"),
    "makie": (
        "Japanese lacquer maki-e panel. Deep black urushi ground, gold and silver "
        "powder inlay, mother-of-pearl fragments, everything rendered as precious "
        "metal on lacquer. {subject}. Elegant flattened decorative arrangement, "
        "asymmetric, generous black.",
        "black lacquer with gold, silver and pale shell iridescence"),
    "kirie": (
        "Layered paper-cut kirie. Cleanly scissor-cut silhouettes in four stacked "
        "paper planes with real drop shadows between layers, no rendering or "
        "shading within a layer. {subject}. Strong flat silhouette reading, "
        "foreground foliage cut in the nearest plane.",
        "four papers: charcoal, coral, sand and white"),
    "oil": (
        "Oil on canvas, alla prima, visible impasto and palette-knife edges, "
        "chiaroscuro lighting from a single low warm source, dark transparent "
        "background. {subject}. Half-length old-master portrait, hands lit, "
        "everything else falling into shadow.",
        "earth palette: raw umber, lead white, madder, black"),
    "screenprint": (
        "Three-colour screenprint poster. Flat knocked-out ink shapes, hard edges, "
        "visible screen texture and slight ink build at the edges, no gradients at "
        "all. {subject}. Bold poster composition, huge simplified shapes, figure "
        "cropped confidently by the frame.",
        "three flat inks: black, warm red, and a single pale ground"),
    "stainedglass": (
        "Stained glass window. Heavy black lead cames enclosing every shape, "
        "luminous saturated glass with visible texture and bubbles, backlit so "
        "colours glow. {subject}. Symmetrical devotional arrangement inside a "
        "gothic arch.",
        "jewel glass: cobalt, emerald, ruby, amber"),
}


def style_for(name, spec):
    """The full prompt for one treatment. Replaces STYLE entirely."""
    body, palette = TREATMENTS[name]
    return (body.format(subject=spec["subject"])
            + f" Colour: {palette}, with {spec['accent']} as the accent. "
            + f"Motif: {spec['botanical']}. " + LOWER)


NAMES = list(TREATMENTS)
