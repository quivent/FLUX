#!/home/dev/venv/bin/python
"""The spice & tea collection: FLUX renders the art, PIL sets the type.

FLUX garbles anything longer than a couple of words, so the artwork is prompted
with a deliberately empty lower panel and the typography is composited after --
crisp quotes, consistent metrics card to card, and the layout is the same object
for every product, which is what makes eight images read as one collection.

    ./collection.py            # render + typeset everything into ~/cards
    ./collection.py --only sencha star-anise
    ./collection.py --compose-only     # re-typeset from existing art, no GPU

Talks to the resident daemon (~/fluxd.py) over HTTP, so no second copy of the
model is loaded.
"""
import argparse
import json
import os
import time
import pathlib
import urllib.request

from PIL import Image, ImageDraw, ImageFont

BASE_W, BASE_H = 896, 1344  # the reference geometry the layout was drawn for
W, H = BASE_W, BASE_H       # actual render size; --width/--height override
ART_DIR = pathlib.Path("/home/dev/art")
CARD_DIR = pathlib.Path("/home/dev/cards")
AXES_ACTIVE = {}
RUNS_DIR = pathlib.Path("/home/dev/runs")
FONT = "/home/dev/fonts/EBGaramond.ttf"
FONT_I = "/home/dev/fonts/EBGaramond-Italic.ttf"
DAEMON = "http://127.0.0.1:8080"

CREAM = (244, 237, 224)
INK = (38, 32, 28)
HOUSE = "KOYOMI"

# The style spine every card shares. Identical wording across all eight is what
# holds the set together -- only the character, the botanical and the accent move.
# The lettering clauses are load-bearing: left alone, FLUX fills empty label space
# with invented kanji, which is charming and completely unusable on real packaging.
STYLE = (
    "vertical premium product label illustration. "
    "Framing, identical on every card in the series: {framing} of one character, "
    "centred, large in frame, head and shoulders fully visible, "
    "no vignette, no circular frame, no extreme close-up, no full-body wide shot. "
    "Cel-shaded anime art with {line} and soft gradient shading, "
    "{palette} with {accent} accents, "
    "{ornament} of {botanical} framing the edges, flat cream paper "
    "background with subtle paper grain, vintage apothecary label composition, "
    "the entire lower third is an empty flat cream panel, completely blank. "
    "Absolutely no text anywhere in the image: no letters, no words, no kanji, "
    "no japanese characters, no calligraphy, no scrolls, no banners, no signage, "
    "no signature, no watermark. Highly detailed, print quality"
)

# Each axis is a named dimension with discrete levels. The first level of each is
# the baseline the collection was built at, so an unset axis reproduces exactly
# what came before -- which is what makes a one-axis change measurable.
AXES = {
    "framing": {
        "waist": "a waist-up three-quarter portrait",
        "bust": "a tight bust portrait, head and shoulders only",
        "half": "a half-body portrait from the thighs up",
        "profile": "a waist-up profile portrait turned to one side",
    },
    "line": {
        "clean": "clean confident linework",
        "fine": "fine delicate ink linework with sparse hatching",
        "bold": "bold heavy brush linework with strong black shapes",
    },
    "palette": {
        "muted": "muted natural palette",
        "cool": "cool desaturated palette with blue-grey shadows",
        "warm": "warm sunlit palette with amber shadows",
        "ink": "near-monochrome ink wash palette with a single colour accent",
    },
    "ornament": {
        "delicate": "a delicate decorative border",
        "sparse": "a minimal thin rule border with very little ornament",
        "dense": "a dense, elaborately ornamented border",
    },
}

BASELINE = {k: next(iter(v)) for k, v in AXES.items()}


def axis_fragments(axes=None):
    """Resolve axis levels to prompt fragments, defaulting to the baseline."""
    chosen = dict(BASELINE)
    chosen.update(axes or {})
    out = {}
    for name, level in chosen.items():
        if name not in AXES:
            raise SystemExit(f"unknown axis '{name}'; known: {', '.join(AXES)}")
        if level not in AXES[name]:
            raise SystemExit(
                f"unknown level '{level}' for axis '{name}'; "
                f"known: {', '.join(AXES[name])}"
            )
        out[name] = AXES[name][level]
    return out


SPEC = [
    {
        "key": "sencha",
        "product": "Sencha",
        "subtitle": "First Flush · Steamed Green",
        "family": "tea",
        "accent": "deep jade green",
        "rgb": (47, 107, 79),
        "botanical": "young tea leaves and morning mist",
        "character": "Ren Aoyagi",
        "role": "The Wandering Blade",
        "subject": (
            "a composed young ronin swordsman with a long dark ponytail and a faded "
            "indigo haori, eyes half-closed in concentration, holding a small "
            "celadon teacup with both hands, a sheathed katana resting against his shoulder"
        ),
        "quote": "I drink it before the duel. Nothing afterward ever tastes as clear.",
    },
    {
        "key": "hojicha",
        "product": "Hojicha",
        "subtitle": "Charcoal Roasted · Low Caffeine",
        "family": "tea",
        "accent": "roasted umber and ember orange",
        "rgb": (122, 74, 43),
        "botanical": "roasted twigs and drifting smoke",
        "character": "Master Kuroda",
        "role": "The Retired Mentor",
        "subject": (
            "a weathered old master with a grey topknot, deep laugh lines and a long "
            "kiseru pipe, seated cross-legged beside a glowing charcoal brazier, "
            "warm firelight on his face, an expression of amused patience"
        ),
        "quote": "Roast the leaf and it forgives the evening. I have needed that more than once.",
    },
    {
        "key": "matcha",
        "product": "Matcha",
        "subtitle": "Ceremonial Grade · Stone Ground",
        "family": "tea",
        "accent": "vivid moss green",
        "rgb": (76, 122, 58),
        "botanical": "bamboo whisks and shade-grown leaves",
        "character": "Suzume",
        "role": "The Shrine Maiden",
        "subject": (
            "a serene shrine maiden in white and vermilion robes, long black hair tied "
            "with paper streamers, whisking bright green matcha in a dark raku bowl, "
            "sleeves caught mid-motion, cherry petals in the air"
        ),
        "quote": "Whisk until it sings. The gods notice effort.",
    },
    {
        "key": "genmaicha",
        "product": "Genmaicha",
        "subtitle": "Toasted Rice · Everyday Green",
        "family": "tea",
        "accent": "toasted gold",
        "rgb": (140, 106, 58),
        "botanical": "puffed rice grains and rice stalks",
        "character": "Momo Hoshikawa",
        "role": "The Relentless Optimist",
        "subject": (
            "an exuberant schoolgirl with short peach-coloured hair and a stray cowlick, "
            "beaming, both hands wrapped around an enormous steaming mug, "
            "leaning into frame with unstoppable enthusiasm"
        ),
        "quote": "Toasted rice! In the tea! Whoever thought of that is my hero and I will find them.",
    },
    {
        "key": "star-anise",
        "product": "Star Anise",
        "subtitle": "Whole Pods · Warm Spice",
        "family": "spice",
        "accent": "deep plum and antique silver",
        "rgb": (91, 58, 107),
        "botanical": "eight-pointed star anise pods",
        "character": "Kaito Shirogane",
        "role": "The Rival",
        "subject": (
            "an aloof young duelist with silver hair falling across one violet eye, "
            "high-collared dark coat, holding a single star anise pod up to the light "
            "between two fingers, faint superior smile"
        ),
        "quote": "Eight points. Perfect symmetry. Unlike your knife work.",
    },
    {
        "key": "sichuan-pepper",
        "product": "Sichuan Pepper",
        "subtitle": "Red Husk · Numbing Heat",
        "family": "spice",
        "accent": "chili red",
        "rgb": (160, 58, 46),
        "botanical": "red peppercorn husks and flame motifs",
        "character": "Rika Enjo",
        "role": "The Fighter",
        "subject": (
            "a fierce young martial artist mid-stance with a red training jacket tied at "
            "the waist, scarlet hair whipping upward, hands wrapped in tape, "
            "grinning through the burn, sparks of heat around her"
        ),
        "quote": "It numbs the tongue so the rest of you pays attention. Try to keep up.",
    },
    {
        "key": "cinnamon",
        "product": "Cinnamon",
        "subtitle": "True Quills · Ceylon",
        "family": "spice",
        "accent": "warm cinnamon brown",
        "rgb": (138, 75, 34),
        "botanical": "curled cinnamon quills and bark scrolls",
        "character": "Nyoko",
        "role": "The Cat Spirit",
        "subject": (
            "a mischievous cat-spirit girl with tufted ears, slit golden eyes and a "
            "striped tail curling behind her, crouched on a shop beam clutching a stolen "
            "bundle of cinnamon quills, entirely unrepentant"
        ),
        "quote": "I stole an entire quill and regret nothing. Put it in everything.",
    },
    {
        "key": "saffron",
        "product": "Saffron",
        "subtitle": "Hand-Picked Threads · Grade I",
        "family": "spice",
        "accent": "saffron gold",
        "rgb": (194, 133, 27),
        "botanical": "purple crocus blooms and crimson threads",
        "character": "Lady Amarante",
        "role": "The Court Sorceress",
        "subject": (
            "an elegant sorceress in deep violet court robes with gold embroidery, "
            "silver-blonde hair coiled high, letting three crimson saffron threads fall "
            "from her fingertips into a glowing vessel, utterly unhurried"
        ),
        "quote": "Three threads colour the entire pot. Power should always be this economical.",
    },
]



def population():
    """The concepts currently competing, or the founding eight if none exist yet.

    Imported lazily: concepts.py reads SPEC to seed generation zero, so a
    module-level import here would be circular.
    """
    try:
        import concepts

        pool = concepts.load()
        living = concepts.alive(pool)
        if living:
            return concepts.as_spec(living), pool["generation"]
    except Exception as e:  # a broken pool must never stop production
        print(f"  [pool unavailable: {e}; using the founding eight]", flush=True)
    return SPEC, 0


# Per-card variation policy, driven live from direction.py.
#   mode: "off" | "sweep" | "random";  axes: which axis names may move
VARY = {"mode": "off", "axes": [], "seq": 0}
VARY_LOG = pathlib.Path("/home/dev/variations.jsonl")
LAST_AXES = {}


def _vary_pick():
    """Next axis combination under the current policy, or None when off.

    sweep is a mixed-radix odometer over the selected axes: successive calls
    advance the fastest axis first, so consecutive cards differ minimally and any
    visible difference is attributable to the axis that actually moved.
    """
    names = [a for a in VARY.get("axes", []) if a in AXES]
    if VARY.get("mode") not in ("sweep", "random") or not names:
        return None
    chosen = dict(BASELINE)
    chosen.update(AXES_ACTIVE or {})
    if VARY["mode"] == "random":
        import random
        for n in names:
            chosen[n] = random.choice(list(AXES[n]))
        return chosen
    n_ = VARY.get("seq", 0)
    VARY["seq"] = n_ + 1
    for name in names:                      # fastest axis first
        levels = list(AXES[name])
        chosen[name] = levels[n_ % len(levels)]
        n_ //= len(levels)
    return chosen


def prompt_for(card, axes=None):
    # AXES_ACTIVE was declared and never assigned, and no caller ever passed axes,
    # so every card ever rendered used the baseline levels. Honour the active set.
    if axes is None:
        axes = _vary_pick() or AXES_ACTIVE or None
    if axes:
        global LAST_AXES
        LAST_AXES = dict(axes)
        try:                                # the record of what each card actually got
            with VARY_LOG.open("a") as f:
                f.write(json.dumps({"key": card.get("key"), "at": time.time(),
                                    "axes": dict(axes), "mode": VARY.get("mode")}) + "\n")
        except OSError:
            pass
    return f"{card['subject']}, " + STYLE.format(
        accent=card["accent"], botanical=card["botanical"], **axis_fragments(axes)
    )


def render(card, steps, seed):
    """Ask the resident daemon for this card's artwork."""
    body = json.dumps(
        {
            "prompt": prompt_for(card),
            "steps": steps,
            "width": W,
            "height": H,
            "seed": seed,
            "num": 1,
            "stem": f"art-{card['key']}",
        }
    ).encode()
    req = urllib.request.Request(
        f"{DAEMON}/generate", data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=1800) as r:
        out = json.load(r)
    src = pathlib.Path(out["images"][0]["path"])
    dst = ART_DIR / f"{card['key']}.png"
    ART_DIR.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(src.read_bytes())
    return dst, out["images"][0]["seconds"]


def render_chunk(chunk, steps, base_seed, offset):
    """One forward pass for one chunk of cards.

    Every image in a chunk shares steps and dimensions -- the requirement for
    batching -- while each keeps its own deterministic seed, so a card's art is
    reproducible no matter which chunk it landed in.
    """
    ART_DIR.mkdir(parents=True, exist_ok=True)
    body = json.dumps(
        {
            "items": [
                {
                    "prompt": prompt_for(c, AXES_ACTIVE),
                    "seed": base_seed + (offset + i) * 17,
                    "stem": f"art-{c['key']}",
                }
                for i, c in enumerate(chunk)
            ],
            "steps": steps,
            "width": W,
            "height": H,
        }
    ).encode()
    req = urllib.request.Request(
        f"{DAEMON}/batch", data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=3600) as r:
        out = json.load(r)
    for card, img in zip(chunk, out["images"]):
        (ART_DIR / f"{card['key']}.png").write_bytes(pathlib.Path(img["path"]).read_bytes())
    return out["images"]


def new_run(label, steps, batch_size):
    """Allocate the next generation directory. Numbering is monotonic, so the
    feed can sort newest-first without parsing timestamps."""
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    existing = [int(d.name.split("-")[1]) for d in RUNS_DIR.glob("gen-*") if d.name.split("-")[1].isdigit()]
    n = (max(existing) + 1) if existing else 1
    run = RUNS_DIR / f"gen-{n:04d}"
    (run / "cards").mkdir(parents=True, exist_ok=True)
    (run / "run.json").write_text(
        json.dumps(
            {
                "generation": n,
                "label": label,
                "steps": steps,
                "width": W,
                "height": H,
                "batch_size": batch_size,
                "started": time.time(),
                "cards": [],
            },
            indent=2,
        )
    )
    return run


def publish(run, card, card_path, art_meta):
    """Copy a finished card into the generation and append it to the manifest.

    Written card-first, manifest-second: the feed only ever advertises a file
    that is already fully on disk.
    """
    dst = run / "cards" / f"{card['key']}.png"
    dst.write_bytes(pathlib.Path(card_path).read_bytes())
    art_src = ART_DIR / f"{card['key']}.png"
    if art_src.is_file():
        (run / "art").mkdir(parents=True, exist_ok=True)
        (run / "art" / f"{card['key']}.png").write_bytes(art_src.read_bytes())
    manifest = json.loads((run / "run.json").read_text())
    manifest["cards"].append(
        {
            "key": card["key"],
            "product": card["product"],
            "subtitle": card["subtitle"],
            "family": card["family"],
            "character": card["character"],
            "role": card["role"],
            "quote": card["quote"],
            "concept": card.get("concept"),
            "seed": art_meta.get("seed"),
            "seconds": art_meta.get("seconds"),
            "finished": time.time(),
        }
    )
    (run / "run.json").write_text(json.dumps(manifest, indent=2))
    return dst


# ---------------------------------------------------------------- typography


def tracked(draw, xy, text, font, fill, tracking):
    """PIL has no letter-spacing; step the pen manually. Returns total width."""
    x, y = xy
    for ch in text:
        if draw:
            draw.text((x, y), ch, font=font, fill=fill)
        x += font.getlength(ch) + tracking
    return x - tracking - xy[0]


def tracked_centred(draw, cx, y, text, font, fill, tracking):
    width = tracked(None, (0, 0), text, font, fill, tracking)
    tracked(draw, (cx - width / 2, y), text, font, fill, tracking)


def wrap_to(text, font, max_width):
    """Greedy wrap measured in real glyph widths, not characters."""
    words, lines, line = text.split(), [], ""
    for w in words:
        trial = f"{line} {w}".strip()
        if font.getlength(trial) <= max_width or not line:
            line = trial
        else:
            lines.append(line)
            line = w
    if line:
        lines.append(line)
    return lines


def variable(path, size, weight):
    f = ImageFont.truetype(path, size)
    try:
        f.set_variation_by_axes([weight])
    except Exception:
        pass
    return f


def compose(card):
    """Lay the plate, the rules and the type over the rendered art.

    The block is measured before it is drawn and then centred in the panel, so a
    one-line quote and a three-line quote both sit correctly without hand-tuning
    per card -- and nothing can run off the bottom margin.
    """
    art = Image.open(ART_DIR / f"{card['key']}.png").convert("RGB").resize((W, H))
    accent = tuple(card["rgb"])
    S = W / BASE_W  # every metric below was drawn for BASE_W; scale, don't re-tune

    def px(v):
        return int(round(v * S))

    # The plate: opaque where the type sits so any stray FLUX lettering is buried,
    # fading in above so it doesn't read as a pasted rectangle.
    plate_top, fade = int(H * 0.60), px(90)
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    for i in range(fade):
        od.line([(0, plate_top + i), (W, plate_top + i)], fill=CREAM + (int(255 * i / fade),))
    od.rectangle([0, plate_top + fade, W, H], fill=CREAM + (255,))
    img = Image.alpha_composite(art.convert("RGBA"), overlay).convert("RGB")
    d = ImageDraw.Draw(img)

    m = px(46)  # outer margin
    d.rectangle([m, m, W - m, H - m], outline=accent, width=max(1, px(2)))
    d.rectangle([m + px(9), m + px(9), W - m - px(9), H - m - px(9)], outline=accent, width=1)

    name_font = variable(FONT, px(74), 700)
    sub_font = variable(FONT, px(23), 500)
    q_font = ImageFont.truetype(FONT_I, px(31))
    at_font = variable(FONT, px(21), 600)
    role_font = ImageFont.truetype(FONT_I, px(20))
    foot_font = variable(FONT, px(17), 600)

    q_lines = wrap_to(f"“{card['quote']}”", q_font, W - 2 * m - px(120))

    # Measure the whole block, then centre it between the plate and the footer.
    RULE_GAP, NAME_H, SUB_H = px(30), px(88), px(52)
    SEP_H, LINE_H, AT_H, ROLE_H = px(46), px(42), px(32), px(26)
    block = RULE_GAP + NAME_H + SUB_H + SEP_H + LINE_H * len(q_lines) + px(16) + AT_H + ROLE_H
    top = plate_top + fade + px(14)
    bottom = H - m - px(58)  # the footer row lives below this
    y = top + max(0, (bottom - top - block) / 2)
    cx = W / 2

    d.line([(m + px(60), y), (W - m - px(60), y)], fill=accent, width=1)
    y += RULE_GAP

    tracked_centred(d, cx, y, card["product"].upper(), name_font, INK, 4 * S)
    y += NAME_H

    tracked_centred(d, cx, y, card["subtitle"].upper(), sub_font, accent, 3.4 * S)
    y += SUB_H

    r7, r14, r18 = px(7), px(14), px(18)
    d.polygon([(cx, y), (cx + r7, y + r7), (cx, y + r14), (cx - r7, y + r7)], fill=accent)
    d.line([(m + px(90), y + r7), (cx - r18, y + r7)], fill=accent, width=1)
    d.line([(cx + r18, y + r7), (W - m - px(90), y + r7)], fill=accent, width=1)
    y += SEP_H

    for line in q_lines:
        d.text((cx, y), line, font=q_font, fill=INK, anchor="ma")
        y += LINE_H
    y += px(16)

    tracked_centred(d, cx, y, f"— {card['character'].upper()}", at_font, INK, 3 * S)
    y += AT_H
    d.text((cx, y), card["role"], font=role_font, fill=accent, anchor="ma")

    # House mark, bottom corners: ties the two families into one shelf.
    fy = H - m - px(34)
    tracked(d, (m + px(26), fy), HOUSE, foot_font, accent, 3 * S)
    fam = card["family"].upper()
    fw = tracked(None, (0, 0), fam, foot_font, accent, 3 * S)
    tracked(d, (W - m - px(26) - fw, fy), fam, foot_font, accent, 3 * S)

    CARD_DIR.mkdir(parents=True, exist_ok=True)
    out = CARD_DIR / f"{card['key']}.png"
    img.save(out)
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--only", nargs="*", help="card keys to build (default: all)")
    p.add_argument("--steps", type=int, default=32)
    p.add_argument("--seed", type=int, default=8801,
                   help="base seed; each card gets seed+i*17. LOCKED by default: "
                        "it is the control, so a difference between generations is "
                        "attributable to the axis that moved, not to noise")
    p.add_argument("--compose-only", action="store_true", help="re-typeset existing art")
    p.add_argument("--width", type=int, default=448, help="render width (default: half of 896)")
    p.add_argument("--height", type=int, default=672, help="render height (default: half of 1344)")
    p.add_argument("--batch-size", type=int, default=4, help="images per forward pass")
    p.add_argument("--label", default="", help="what changed in this generation")
    p.add_argument("--axis", action="append", default=[], metavar="NAME=LEVEL",
                   help="move one axis off baseline; repeatable")
    a = p.parse_args()

    global W, H
    W, H = a.width, a.height

    global AXES_ACTIVE
    for spec in a.axis:
        if "=" not in spec:
            raise SystemExit(f"--axis wants NAME=LEVEL, got: {spec}")
        name, level = spec.split("=", 1)
        AXES_ACTIVE[name.strip()] = level.strip()
    axis_fragments(AXES_ACTIVE)  # validate before spending any GPU time

    pool_spec, pool_gen = population()
    cards = [c for c in pool_spec if not a.only or c["key"] in a.only]
    run = new_run(a.label, a.steps, a.batch_size)
    manifest = json.loads((run / "run.json").read_text())
    manifest["base_seed"] = a.seed
    manifest["axes"] = {**BASELINE, **AXES_ACTIVE}
    manifest["axes_moved"] = dict(AXES_ACTIVE)
    manifest["concept_generation"] = pool_gen
    manifest["concepts"] = {
        c["key"]: c.get("concept", {}) for c in cards if c.get("concept")
    }
    (run / "run.json").write_text(json.dumps(manifest, indent=2))
    print(f"generation {run.name} ({W}x{H}, {a.steps} steps, batch {a.batch_size}) "
          f"— concept pool gen {pool_gen}, {len(cards)} concepts", flush=True)

    for start in range(0, len(cards), a.batch_size):
        chunk = cards[start : start + a.batch_size]
        metas = {}
        if not a.compose_only:
            imgs = render_chunk(chunk, a.steps, a.seed, start)
            metas = {c["key"]: m for c, m in zip(chunk, imgs)}
            first = imgs[0]
            print(
                f"  batch {start // a.batch_size + 1}: {first['batch_size']} images in "
                f"{first['batch_seconds']}s ({first['seconds']}s/image) at {W}x{H}",
                flush=True,
            )
        # Compose and publish this chunk NOW, so the feed fills in while the
        # remaining chunks are still on the GPU.
        for card in chunk:
            out = compose(card)
            publish(run, card, out, metas.get(card["key"], {}))
            print(f"  card {card['key']:<16} -> {out}", flush=True)

    manifest = json.loads((run / "run.json").read_text())
    manifest["finished"] = time.time()
    (run / "run.json").write_text(json.dumps(manifest, indent=2))
    print(f"done: {len(cards)} cards in {run}")


if __name__ == "__main__":
    main()
