#!/usr/bin/env python3
"""creative_drift — a FLUX loop that never finishes and never repeats itself.

The manifest runner (run_creative_flux_manifest.py) is a finite sweep: N sources
x M passes, then done. This is the other shape -- a resident generator that
holds the pipeline in memory and keeps emitting, where each generation is built
from the last rather than drawn fresh.

    creative_drift --> piper (asset.publish) --> flux serve --> /api/assets/ws --> page

Two things make the output move rather than jitter:

  * Mutation walks a genome one or two slots at a time, so consecutive frames
    are relatives, not strangers.
  * A slow phase advances with wall-clock time and biases which pools those
    slots draw from, so an hour of output has an arc instead of a uniform
    scatter of random prompts.

What this does NOT have is taste. Nothing here judges whether an image is good;
"fitness" is novelty against recent history, which keeps the walk from
collapsing onto one attractor but is not an aesthetic claim. When picks exist
(the gallery writes them), they steer parent selection -- that is the only real
signal in the loop, and it comes from a human.
"""
import argparse
import hashlib
import json
import os
import pathlib
import queue
import random
import signal
import socket
import subprocess
import sys
import threading
import time

# The suite imports its own language module and the repo's flux_paths,
# so both this directory and the repo root go on the path.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import flux_paths  # noqa: E402
import author  # noqa: E402
import era  # noqa: E402
import language as lang  # noqa: E402

# Share of generations that carry one authored frame. Low: the grammar is the
# proven arm and the author is the challenger, not the replacement.
AUTHOR_RATE = float(os.environ.get("CHORUS_AUTHOR_RATE", "0.25"))
PROMOTED_RATE = float(os.environ.get("CHORUS_PROMOTED_RATE", "0.25"))

# Titles are part of the work, not storage plumbing. Keep the lineage suffix
# machine-readable, but let the visible stem carry the same restrained,
# material vocabulary as the images. This is deterministic from the prompt and
# seed so a restart never renames an existing idea.
TITLE_MOTIFS = {
    "figures": ("last gesture", "quiet witness", "passing face", "work of hands"),
    "hands": ("held breath", "small ritual", "trace of touch", "patient hand"),
    "creatures": ("wild stillness", "watchful distance", "soft animal", "sudden wing"),
    "streets": ("empty crossing", "lantern street", "weathered passage", "late tram"),
    "interiors": ("room remembering", "work light", "open window", "chair left warm"),
    "food": ("broken feast", "salt and rind", "red seed", "kitchen offering"),
    "weather": ("turning weather", "first snow", "wind at the door", "vanishing road"),
    "water": ("dark water", "surface breaking", "silver current", "after the wave"),
    "machines": ("tender machine", "wheel in motion", "bright mechanism", "useful noise"),
    "music": ("last phrase", "room after music", "lifted bow", "brass in rain"),
    "fire": ("ember hour", "match held close", "orange edge", "fire going quiet"),
    "thresholds": ("door left open", "far side", "light through", "turning stair"),
    "sleep": ("borrowed sleep", "dreaming animal", "night carriage", "breathing room"),
    "botanical": ("green persistence", "silver seed", "moss returning", "flower at noon"),
}
TITLE_WEATHER = (
    "first light", "rain", "blue hour", "green leaves", "morning",
    "firelight", "white ground", "dusk", "one red mark", "deep shade",
)
TITLE_FORMS = (
    "{motif} / {weather}",
    "study for {motif}",
    "{motif} after {weather}",
    "{weather}, {motif}",
    "the hour of {motif}",
    "{motif}, nearly gone",
)


def artwork_filename(genome, generation, index, seed, era_spec=None):
    material = f"{lang.compose(genome)}|{seed}".encode()
    digest = hashlib.sha256(material).digest()
    motifs = (era.title_motifs(era_spec) if era_spec else ()) or TITLE_MOTIFS.get(
        genome.get("world"), ("quiet object", "held light", "near distance"))
    motif = motifs[digest[0] % len(motifs)]
    weather = TITLE_WEATHER[digest[1] % len(TITLE_WEATHER)]
    title = TITLE_FORMS[digest[2] % len(TITLE_FORMS)].format(motif=motif, weather=weather)
    slug = "-".join(title.replace("/", " ").replace(",", " ").split())
    return f"{slug}--g{generation:04d}-{index:02d}-s{seed}.png"


def challenger_id(candidate):
    """Stable across Hive restarts and old state written before IDs existed."""
    if candidate.get("id"):
        return str(candidate["id"])
    material = "|".join(str(candidate.get(k, "")) for k in ("seat", "kind", "phrase"))
    return hashlib.sha256(material.encode()).hexdigest()[:12]


def load_hive_state(out_dir):
    try:
        state = json.loads((pathlib.Path(out_dir) / "challengers.json").read_text())
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        return {}


def apply_hive_candidate(genome, candidate):
    """Place one proposal in the exact prompt slot it claims to improve."""
    kind = candidate.get("kind")
    slot = {"detail": "detail", "mood": "mood", "framing": "framing"}.get(kind)
    phrase = str(candidate.get("phrase") or "").strip()
    if not slot or not phrase:
        return False
    genome[slot] = phrase
    return True


def choose_hive_arm(rng, genome, state):
    """Draw from a fixed trial budget; promoted language joins the anchor.

    The return value is written beside the frame. Hive can therefore compare
    a candidate with contemporaneous anchor frames rather than guessing from
    an archive-wide score.
    """
    candidates = state.get("challengers") or []
    trials = [c for c in candidates if c.get("state") == "trial"]
    promoted = [c for c in candidates if c.get("state") == "promoted"]
    eps = max(0.0, min(float(state.get("eps") or 0.0), 0.5))
    roll = rng.random()
    if trials and roll < eps:
        candidate = rng.choice(trials)
        if apply_hive_candidate(genome, candidate):
            return {"arm": "trial", "challenger_id": challenger_id(candidate),
                    "phrase": candidate.get("phrase"), "kind": candidate.get("kind")}
    if promoted and roll < eps + (1.0 - eps) * PROMOTED_RATE:
        candidate = rng.choice(promoted)
        if apply_hive_candidate(genome, candidate):
            return {"arm": "anchor", "adopted_id": challenger_id(candidate)}
    return {"arm": "anchor"}


class AuthorAhead:
    """Keep authored prompts ready so the GPU never waits on a language model.

    The author was called inline: one generation in four stopped rendering and
    blocked on a 31B with a 200 second timeout before sampling a single pixel.
    Over three hours the H100 spent twenty-six minutes actually sampling, and
    this was part of why.

    One background thread keeps a couple of prompts in hand. If the queue is
    empty the loop renders from the grammar and moves on -- a slow author costs
    a challenger frame, never a stalled GPU.
    """

    def __init__(self, out_dir, depth=2):
        self.out_dir = str(out_dir)
        self.queue = queue.Queue(maxsize=depth)
        self.errors = 0
        threading.Thread(target=self._fill, daemon=True).start()

    def _fill(self):
        while True:
            try:
                result, error = author.author(self.out_dir)
                if result:
                    self.queue.put(result)          # blocks until a slot frees
                else:
                    self.errors += 1
                    time.sleep(30)
            except Exception:
                self.errors += 1
                time.sleep(30)

    def take(self):
        try:
            return self.queue.get_nowait()
        except queue.Empty:
            return None
PIPER_SOCKET = os.environ.get("PIPER_SOCKET", "/tmp/piper.sock")
NEXUS_ADDR = os.environ.get("NEXUS_ADDR", "127.0.0.1:9999")

# Slots are ordered loosely from "what it is" to "how it is shot": mutating a
# late slot restyles, mutating an early one reinvents. The mutation weighting
# below leans late, so drift reads as continuous.
POOLS = {
    "subject": [
        "a glass cabin", "an abandoned observatory", "a floating monolith",
        "a lighthouse of black stone", "a greenhouse full of ferns",
        "a derelict funicular", "a shrine cut into a cliff", "a paper lantern boat",
        "a radio telescope", "a cathedral of ice", "a rusted orrery",
    ],
    "setting": [
        "in a snowbound pine forest", "on a salt flat", "above a sea of cloud",
        "in a flooded quarry", "on a basalt shore", "inside a fog bank",
        "at the edge of a caldera", "in a field of tall grass",
        "beneath an aurora", "in a canyon of red rock",
    ],
    "light": {
        "dawn": ["cold blue dawn", "first light, long shadows", "pale rose horizon"],
        "day": ["high overcast light", "hard noon sun", "silver diffused daylight"],
        "dusk": ["low amber sun", "last light raking the surface", "burnt orange dusk"],
        "night": ["moonlight on snow", "starlight and deep shadow", "bioluminescent glow"],
    },
    "palette": {
        "dawn": ["muted cyan and bone", "pale indigo, frost white"],
        "day": ["desaturated slate and moss", "washed linen and stone"],
        "dusk": ["amber, rust, deep violet", "copper and ash"],
        "night": ["ink blue and silver", "black, teal, faint magenta"],
    },
    "lens": [
        "35mm, shallow depth of field", "wide anamorphic frame",
        "long lens compression", "tilt-shift miniature",
        "large format stillness", "handheld, slight motion blur",
    ],
    "texture": [
        "fine film grain", "wet surfaces", "drifting particulate",
        "condensation on glass", "weathered patina", "volumetric haze",
    ],
    "mood": [
        "quiet and unpeopled", "on the edge of a storm", "ceremonial",
        "abandoned mid-task", "patient", "the moment before snow",
    ],
}

# The arc a run walks through. One full cycle per --phase-hours.
PHASES = ["dawn", "day", "dusk", "night"]


def phase_for(started_at, phase_hours):
    elapsed = (time.time() - started_at) / 3600.0
    return PHASES[int((elapsed / max(phase_hours, 0.01)) % len(PHASES))]


def draw(rng, slot, phase):
    pool = POOLS[slot]
    if isinstance(pool, dict):
        pool = pool[phase]
    return rng.choice(pool)


def new_genome(rng, phase):
    return {slot: draw(rng, slot, phase) for slot in POOLS}


def mutate(rng, parent, phase, rate):
    """Walk one or two slots, weighted toward the stylistic end.

    Uniform mutation across slots changes the subject as often as the grain,
    which reads as a new image every frame rather than an evolving one.
    """
    child = dict(parent)
    slots = list(POOLS)
    weights = [1, 1, 3, 3, 4, 4, 3]  # subject..mood, later slots move more
    for _ in range(1 if rng.random() > rate else 2):
        slot = rng.choices(slots, weights=weights, k=1)[0]
        child[slot] = draw(rng, slot, phase)
    # The phase pools shift underneath us, so re-draw any slot whose value is
    # no longer reachable in this phase -- otherwise dusk keeps dawn's palette.
    for slot in ("light", "palette"):
        if child[slot] not in POOLS[slot][phase]:
            child[slot] = draw(rng, slot, phase)
    return child


def crossover(rng, a, b):
    return {slot: (a if rng.random() < 0.5 else b)[slot] for slot in POOLS}


def render_prompt(g):
    return (
        f"{g['subject']} {g['setting']}, {g['light']}, {g['palette']}, "
        f"{g['lens']}, {g['texture']}, {g['mood']}, cinematic, highly detailed"
    )


def genome_key(g):
    return hashlib.sha256(render_prompt(g).encode()).hexdigest()[:16]


def novelty(g, history):
    """Fraction of slots differing from the most similar recent genome.

    Not a quality score. It only answers "have we just made this?", which is
    what keeps the walk from parking on one combination.
    """
    if not history:
        return 1.0
    best = 0.0
    for old in history:
        same = sum(1 for slot in POOLS if old.get(slot) == g[slot])
        best = max(best, 1.0 - same / len(POOLS))
    return best


def publish_piper(job_id, path, index, total, seed):
    """Announce a finished cell. Mirrors worker.py's _publish_piper_asset:
    the Go hub drops any event whose access_url is not under /outputs/."""
    payload = {
        "type": "asset.publish",
        "job_id": str(job_id),
        "asset": {
            "id": f"{job_id}:{index}",
            "name": pathlib.Path(path).name,
            "path": str(path),
            "media_type": "image/png",
            "index": int(index),
            "cell_index": int(index),
            "total": int(total),
            "access_url": "/outputs/" + pathlib.Path(path).name,
            "seed": str(seed),
        },
    }
    for attempt in range(3):
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
                conn.settimeout(2.0)
                conn.connect(PIPER_SOCKET)
                conn.sendall((json.dumps(payload, separators=(",", ":")) + "\n").encode())
                conn.shutdown(socket.SHUT_WR)
                raw = conn.recv(4096)
            if json.loads(raw.decode("utf-8", "replace").strip()).get("ok"):
                return True
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        time.sleep(0.15 * (attempt + 1))
    return False


def nexus_receipt(job_id, kind="flux.creative_drift"):
    """Ask Nexus to sign for a generation. Best-effort: the loop is a local
    producer, not an atlas submission, so a missing authority degrades to
    unreceipted rather than stopping the render."""
    host, _, port = NEXUS_ADDR.partition(":")
    try:
        with socket.create_connection((host, int(port)), timeout=3) as conn:
            body = {"type": "submit", "job": {"job_id": job_id, "kind": kind,
                                              "execution_owner": "creative_drift"}}
            conn.sendall((json.dumps(body) + "\n").encode())
            raw = conn.makefile("r").readline()
        receipt = json.loads(raw)
        return receipt.get("receipt_id") if receipt.get("verified") else None
    except (OSError, ValueError, json.JSONDecodeError):
        return None


# Knobs a running loop will honour, with the bounds each is clamped to. A
# control file is re-read every generation, so steering never means a restart
# (and never loses the resident pipeline, which is the expensive part).
CONTROLLABLE = {
    "steps": (1, 60),
    "width": (256, 1536),
    "height": (256, 1536),
    "guidance": (0.0, 12.0),
    "batch": (1, 16),
    "mutation_rate": (0.0, 1.0),
    "latent_max_cosine": (-0.75, 0.95),
    "style_hold_generations": (1, 24),
    "phase_hours": (0.01, 48.0),
    "sleep": (0.0, 3600.0),
}
# Free-form steering: pinned overrides a slot outright, phase forces the arc.
PASSTHROUGH = {"phase", "pinned", "paused", "style_directive", "era"}


def clamp(name, value):
    lo, hi = CONTROLLABLE[name]
    if isinstance(lo, int) and isinstance(hi, int):
        return max(lo, min(hi, int(value)))
    return max(lo, min(hi, float(value)))


def separate_latent(latent, previous, max_cosine=-0.20):
    """Push adjacent noise tensors apart by a measured angular distance.

    Independent Gaussian latents are almost orthogonal already, but the batch
    was allowed to land on either side of zero similarity while its prompts
    held the same subject. Keep the fresh tensor's norm and orthogonal content,
    then cap its cosine against the immediately previous frame. The result is
    still a high-dimensional noise sample, but never a near neighbour.
    """
    if previous is None or latent.numel() != previous.numel():
        return latent, None
    current = latent.float().reshape(-1)
    prior = previous.float().reshape(-1)
    current_norm = current.norm().clamp_min(1e-12)
    prior_norm = prior.norm().clamp_min(1e-12)
    cosine = float((current @ prior / (current_norm * prior_norm)).item())
    target = float(max_cosine)
    if cosine <= target:
        return latent, cosine
    prior_unit = prior / prior_norm
    orthogonal = current - (current @ prior_unit) * prior_unit
    orthogonal_norm = orthogonal.norm().clamp_min(1e-12)
    target = max(-0.95, min(0.95, target))
    moved = current_norm * (
        target * prior_unit
        + (1.0 - target * target) ** 0.5 * (orthogonal / orthogonal_norm)
    )
    return moved.reshape_as(latent).to(dtype=latent.dtype), target


def load_control(path, defaults):
    """Merge the control file over the launch defaults.

    A malformed or half-written file must never stop a running loop, so any
    failure falls back to the last good settings rather than raising. Values
    are clamped, not rejected: a typo'd 4000-step render would wedge the loop
    for an hour, which is worse than quietly rendering 60.
    """
    settings = dict(defaults)
    try:
        raw = json.loads(pathlib.Path(path).read_text())
    except (OSError, ValueError):
        return settings, None
    if not isinstance(raw, dict):
        return settings, None
    for key, value in raw.items():
        if key in CONTROLLABLE:
            try:
                settings[key] = clamp(key, value)
            except (TypeError, ValueError):
                pass
        elif key in PASSTHROUGH:
            settings[key] = value
    return settings, raw


def write_control_template(path, defaults):
    """Drop a discoverable file on first run. Never overwrite: the operator's
    edits are the whole point."""
    p = pathlib.Path(path)
    if p.exists():
        return
    body = {k: defaults[k] for k in CONTROLLABLE}
    body.update({"phase": "auto", "pinned": {}, "paused": False})
    p.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n")


def resume_movement(status_path):
    """Restore the aesthetic state so a daemon restart is not a creative reset."""
    try:
        status = json.loads(pathlib.Path(status_path).read_text())
    except (OSError, ValueError):
        return None, 0, None
    style = status.get("movement_style")
    required = {"medium", "medium_clause", "family", "tonal", "mood"}
    if not isinstance(style, dict) or not required.issubset(style):
        style = None
    try:
        age = max(0, int(status.get("style_age") or 0))
    except (TypeError, ValueError):
        age = 0
    return style, age, status.get("style_directive")


def load_picks(out_dir):
    """Human signal, if the gallery left any. Absent is the normal case."""
    path = pathlib.Path(out_dir) / "picks.json"
    try:
        return set(json.loads(path.read_text()))
    except (OSError, ValueError):
        return set()


def main():
    ap = argparse.ArgumentParser(description="Continuously generate drifting FLUX assets.")
    ap.add_argument("--out-dir", default=flux_paths.default_out_dir())
    ap.add_argument("--model-dir", default=flux_paths.default_model_dir())
    ap.add_argument("--batch", type=int, default=4, help="renders per generation")
    ap.add_argument("--steps", type=int, default=24)
    ap.add_argument("--width", type=int, default=1024)
    ap.add_argument("--height", type=int, default=1024)
    ap.add_argument("--guidance", type=float, default=3.5)
    ap.add_argument("--phase-hours", type=float, default=2.0, help="hours per phase of the arc")
    ap.add_argument("--mutation-rate", type=float, default=0.35)
    ap.add_argument("--latent-max-cosine", type=float, default=-0.20,
                    help="maximum cosine similarity between adjacent noise tensors")
    ap.add_argument("--style-hold-generations", type=int, default=10,
                    help="generations sharing one visual language before a one-axis change")
    ap.add_argument("--elite", type=int, default=8, help="genomes kept as parents")
    ap.add_argument("--seed", type=int, default=0, help="0 = clock-seeded")
    ap.add_argument("--max-generations", type=int, default=0, help="0 = forever")
    ap.add_argument("--sleep", type=float, default=0.0, help="pause between generations")
    ap.add_argument("--control", default="", help="live control JSON (default <out-dir>/drift-control.json)")
    args = ap.parse_args()

    out_dir = pathlib.Path(args.out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    ledger = out_dir / "creative-drift.jsonl"
    control_path = args.control or (out_dir / "drift-control.json")
    status_path = out_dir / "drift-status.json"
    rng = random.Random(args.seed or int(time.time()))

    launch = {k: getattr(args, k) for k in CONTROLLABLE}
    write_control_template(control_path, launch)

    import torch
    from diffusers import FluxPipeline

    print(f"loading {args.model_dir}", flush=True)
    pipe = FluxPipeline.from_pretrained(args.model_dir, torch_dtype=torch.bfloat16)
    pipe = pipe.to("cuda")
    pipe.set_progress_bar_config(disable=True)
    print("pipeline resident", flush=True)

    stopping = {"now": False}

    def _stop(_sig, _frm):
        # Finish the cell in flight; a half-written PNG would be announced to
        # the page as a real asset.
        stopping["now"] = True
        print("stop requested; finishing this generation", flush=True)

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    author_ahead = AuthorAhead(out_dir)
    started_at = time.time()
    history, elites = [], []
    generation = 0
    recent_worlds = []
    previous_latent = None
    movement_style, style_age, last_style_directive = resume_movement(status_path)
    active_era_id = None
    if movement_style:
        print(f"resumed movement: {movement_style['medium']} / {movement_style['mood']} "
              f"(age {style_age})", flush=True)

    while not stopping["now"]:
        generation += 1
        if args.max_generations and generation > args.max_generations:
            break
        # Re-read every generation: this is the whole steering surface, and it
        # must land without restarting the resident pipeline.
        live, raw = load_control(control_path, launch)
        if live.get("paused"):
            time.sleep(2)
            generation -= 1
            continue
        forced = (raw or {}).get("phase", "auto")
        phase = forced if forced in PHASES else phase_for(started_at, live["phase_hours"])
        try:
            era_spec = era.load((raw or {}).get("era"), pathlib.Path(__file__).parent.parent)
        except (OSError, ValueError) as exc:
            print(f"era refused: {exc}", flush=True)
            era_spec = None
        era_id = era_spec.get("id") if era_spec else None
        if era_id != active_era_id:
            active_era_id = era_id
            if era_spec:
                movement_style = era.new_style(rng, era_spec)
                style_age = 0
                print(f"era began: {era_spec['title']} — {era_spec['proposition']}", flush=True)
        job_id = f"drift-{int(time.time())}-{generation:04d}"
        receipt = nexus_receipt(job_id)
        picks = load_picks(out_dir)

        # Four distinct semantic worlds per landing. The old A/B dialogue held
        # each subject for two frames, which merely moved duplicates one cell
        # apart. Preserve the material vocabulary, but make every neighbouring
        # prompt begin from another subject family; carry the last three worlds
        # across generation boundaries so the seam cannot repeat either.
        # Variety in subject does not require resetting every aesthetic axis.
        # Hold a visual language long enough to form a movement, then mutate
        # only its surface or its light. This keeps the large semantic spacing
        # without turning the wall into an unrelated prompt sampler.
        directive = (raw or {}).get("style_directive") or {}
        directive_id = directive.get("id") if isinstance(directive, dict) else None
        directive_axis = directive.get("axis") if isinstance(directive, dict) else None
        if movement_style is None:
            movement_style = lang.new_style(rng)
            style_age = 0
        elif (directive_id and directive_id != last_style_directive
              and directive_axis in ("surface", "light")):
            movement_style = lang.evolve_style(rng, movement_style, directive_axis)
            style_age = 0
            last_style_directive = directive_id
            print(f"movement directive {directive_id}: mutate {directive_axis}", flush=True)
        elif style_age >= live["style_hold_generations"]:
            movement_style = lang.evolve_style(rng, movement_style)
            style_age = 0
        batch, sequences = [], []
        for i in range(live["batch"]):
            avoided = set(recent_worlds[-3:]) | {g["world"] for g in batch}
            sequence = lang.new_sequence(rng, avoid_world=avoided, style=movement_style)
            if era_spec:
                sequence = era.apply_sequence(rng, sequence, era_spec)
            v = lang.variation(rng, sequence, i)
            if era_spec:
                v = era.apply_variation(v, era_spec, i)
            batch.append(v)
            sequences.append(sequence)
        recent_worlds.extend(g["world"] for g in batch)
        style_age += 1
        # One authored frame per generation at most, per protocol rule 10: the
        # composer that reasons shares the exploration budget rather than
        # replacing the grammar. It is judged on the same sheet, by the same
        # panel, and earns its share or does not.
        allow_author = not era_spec or bool(era_spec.get("allow_author", True))
        authored = author_ahead.take() if allow_author and rng.random() < AUTHOR_RATE else None
        if authored:
            print(f"  authored: {authored['intent']}", flush=True)

        for index, sequence in enumerate(sequences):
            print(f"  {index + 1}: {lang.describe(sequence)}", flush=True)

        pinned = (raw or {}).get("pinned") or {}
        if isinstance(pinned, dict):
            for genome in batch:
                for slot, value in pinned.items():
                    if slot in POOLS and isinstance(value, str) and value:
                        genome[slot] = value

        hive_state = load_hive_state(out_dir)
        assignments = []
        for index, genome in enumerate(batch):
            if authored and index == 0:
                assignments.append({"arm": "author"})
            else:
                assignments.append(choose_hive_arm(rng, genome, hive_state))

        print(f"gen {generation} phase={phase} steps={live['steps']} "
              f"{live['width']}x{live['height']} batch={live['batch']} "
              f"receipt={receipt or 'none'}", flush=True)
        status_path.write_text(json.dumps({
            "generation": generation, "phase": phase, "settings": live,
            "pinned": pinned, "receipt": receipt, "movement_style": movement_style,
            "era_id": era_id, "era_title": era_spec.get("title") if era_spec else None,
            "collection": era_spec.get("collection") if era_spec else None,
            "style_age": style_age, "style_directive": last_style_directive,
            "updated": time.time(),
        }, indent=2, sort_keys=True) + "\n")

        for index, genome in enumerate(batch):
            if stopping["now"]:
                break
            prompt = lang.compose(genome)
            if authored and index == 0:
                prompt = authored["prompt"]
            seed = rng.randrange(1, 2**31)
            began = time.time()
            generator = torch.Generator("cuda").manual_seed(seed)
            latent, _latent_ids = pipe.prepare_latents(
                1, pipe.transformer.config.in_channels // 4,
                live["height"], live["width"], torch.bfloat16,
                pipe._execution_device, generator,
            )
            latent, latent_cosine = separate_latent(
                latent, previous_latent, live["latent_max_cosine"])
            previous_latent = latent.detach()
            image = pipe(
                prompt=prompt,
                width=live["width"],
                height=live["height"],
                num_inference_steps=live["steps"],
                guidance_scale=live["guidance"],
                latents=latent,
            ).images[0]
            name = artwork_filename(genome, generation, index, seed, era_spec)
            path = out_dir / name
            # Write beside the target then rename: the page watches this
            # directory, and a partial PNG would surface as a broken tile.
            tmp = path.with_suffix(".png.part")
            # PIL infers the encoder from the suffix, and ".part" means nothing
            # to it, so the format has to be named for the atomic write to work.
            image.save(tmp, format="PNG")
            tmp.rename(path)

            published = publish_piper(job_id, path, index, len(batch), seed)
            row = {
                "generation": generation, "index": index, "phase": phase,
                "job_id": job_id, "receipt": receipt, "seed": seed,
                "concept": lang.describe(genome), "genome": genome, "prompt": prompt, "file": name,
                "authored": bool(authored and index == 0),
                "hive": assignments[index],
                "era_id": era_id,
                "era_title": era_spec.get("title") if era_spec else None,
                "collection": era_spec.get("collection") if era_spec else None,
                "latent_cosine_to_previous": latent_cosine,

                "seconds": round(time.time() - began, 2),
                "published": published, "ts": time.time(),
            }
            with ledger.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, sort_keys=True) + "\n")
            with (out_dir / "trial-ledger.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps({"file": name, "ts": row["ts"], **assignments[index]},
                                   sort_keys=True) + "\n")
            print(f"  {name} {row['seconds']}s piper={published}", flush=True)



        # No elites: a sequence is the unit, and carrying survivors forward is
        # exactly what collapsed the first generator onto one look.
        # A sheet has to exist by default rather than on request -- judging a
        # change by the newest frame is how six rounds of "this is better"
        # shipped against a wall that was not. But rebuilding it every
        # generation cost thirteen seconds of a thirty-four second cycle once
        # the archive was restored, because it globbed and sorted every frame
        # ever made. Every third generation, over one 24-frame movement cohort,
        # is fresher than the eye's three-minute clock without mixing epochs.
        if generation % 3 == 0:
            try:
                subprocess.run(
                    [sys.executable, str(pathlib.Path(__file__).parent / "contact.py"),
                     "--dir", str(out_dir), "--out", str(out_dir / "_sheets" / "contact.jpg"),
                     "--n", "16", "--recent", "24"],
                    check=False, capture_output=True, timeout=120,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass

        if live["sleep"]:
            time.sleep(live["sleep"])

    print("drift stopped", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
