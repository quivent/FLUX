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
import random
import signal
import socket
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import flux_paths  # noqa: E402

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
    "phase_hours": (0.01, 48.0),
    "sleep": (0.0, 3600.0),
}
# Free-form steering: pinned overrides a slot outright, phase forces the arc.
PASSTHROUGH = {"phase", "pinned", "paused"}


def clamp(name, value):
    lo, hi = CONTROLLABLE[name]
    if isinstance(lo, int) and isinstance(hi, int):
        return max(lo, min(hi, int(value)))
    return max(lo, min(hi, float(value)))


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

    started_at = time.time()
    history, elites = [], []
    generation = 0

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
        job_id = f"drift-{int(time.time())}-{generation:04d}"
        receipt = nexus_receipt(job_id)
        picks = load_picks(out_dir)

        # Parents: picked genomes first, else the elite pool, else fresh.
        picked_elites = [g for g in elites if genome_key(g) in picks] or elites
        batch = []
        for i in range(live["batch"]):
            if not picked_elites:
                batch.append(new_genome(rng, phase))
            elif len(picked_elites) > 1 and rng.random() < 0.25:
                batch.append(crossover(rng, *rng.sample(picked_elites, 2)))
            else:
                batch.append(mutate(rng, rng.choice(picked_elites), phase, live["mutation_rate"]))
        # One slot of pure novelty per generation keeps the pool from inbreeding.
        if rng.random() < 0.2:
            batch[-1] = new_genome(rng, phase)

        pinned = (raw or {}).get("pinned") or {}
        if isinstance(pinned, dict):
            for genome in batch:
                for slot, value in pinned.items():
                    if slot in POOLS and isinstance(value, str) and value:
                        genome[slot] = value

        print(f"gen {generation} phase={phase} steps={live['steps']} "
              f"{live['width']}x{live['height']} batch={live['batch']} "
              f"receipt={receipt or 'none'}", flush=True)
        status_path.write_text(json.dumps({
            "generation": generation, "phase": phase, "settings": live,
            "pinned": pinned, "receipt": receipt, "updated": time.time(),
        }, indent=2, sort_keys=True) + "\n")

        for index, genome in enumerate(batch):
            if stopping["now"]:
                break
            prompt = render_prompt(genome)
            seed = rng.randrange(1, 2**31)
            began = time.time()
            image = pipe(
                prompt=prompt,
                width=live["width"],
                height=live["height"],
                num_inference_steps=live["steps"],
                guidance_scale=live["guidance"],
                generator=torch.Generator("cuda").manual_seed(seed),
            ).images[0]
            name = f"drift-{generation:04d}-{index:02d}-{genome_key(genome)}-seed-{seed}.png"
            path = out_dir / name
            # Write beside the target then rename: the page watches this
            # directory, and a partial PNG would surface as a broken tile.
            tmp = path.with_suffix(".png.part")
            image.save(tmp)
            tmp.rename(path)

            published = publish_piper(job_id, path, index, len(batch), seed)
            row = {
                "generation": generation, "index": index, "phase": phase,
                "job_id": job_id, "receipt": receipt, "seed": seed,
                "genome": genome, "prompt": prompt, "file": name,
                "novelty": round(novelty(genome, history), 3),
                "seconds": round(time.time() - began, 2),
                "published": published, "ts": time.time(),
            }
            with ledger.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, sort_keys=True) + "\n")
            print(f"  {name} {row['seconds']}s novelty={row['novelty']} piper={published}", flush=True)

            history.append(genome)
            history[:] = history[-40:]

        # Carry the most novel of this generation forward as parents.
        batch.sort(key=lambda g: novelty(g, history), reverse=True)
        elites.extend(batch[: max(1, live["batch"] // 2)])
        elites[:] = elites[-args.elite:]
        if live["sleep"]:
            time.sleep(live["sleep"])

    print("drift stopped", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
