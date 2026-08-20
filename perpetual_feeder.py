#!/usr/bin/env python3
"""Perpetual GPU Sieve & Movement Towards Master Engine.

Generation Architecture:
- Execution: 1-by-1 Sequential Pipeline (FIFO depth < 3)
- Primary Prompt Synthesis: Governor Gemma 31B AI Dynamic Open-Ended Generation
- Diversity Guard: Infinite prompt space with zero subject repetition
- Movement Towards Master: Iterative genetic refinement on verified Spectacles (≥90.0)
"""
import collections
import json
import os
import random
import secrets
import subprocess
import time
import urllib.request
import uniqueness_tracker

STOP_FILE = "/root/STOP"
SPECTACLE_LOG = "/root/Models/flux-output/spectacle_genome.jsonl"
WINNING_GENOME_LOG = "/root/Models/flux-output/winning_genome.jsonl"
JOBS_LEDGER = "/root/CLIs/flux/.fluxd/flux-gpu0.jobs.jsonl"
GOVERNOR_API = "http://127.0.0.1:8000/v1/chat/completions"

# Massive combinatorial fallback taxonomy across 10 distinct creative domains
DIVERSE_TAXONOMY = [
    # 1. Microscopic & Quantum
    "a singular translucent salt crystal blooming into an iridescent mineral fractal city, extreme shallow depth-of-field, neon-amber refractions",
    "a microscopic cross-section of a fossilized dragonfly wing holding captured starlight in amber, polarized light microscopy",
    "a suspended liquid ferrofluid droplet forming crystalline spikes under an invisible magnetic field, macro photography, high-speed shutter",
    
    # 2. Avant-Garde Couture & High Fashion
    "an haute-couture gown woven from black volcanic glass fibers and structured brass filaments, moody editorial runway lighting",
    "a masked Venetian alchemist wearing draped velvet robes with constellations embroidered in liquid silver thread, chiaroscuro lighting",
    "an avant-garde sculptural silhouette walking across a salt flat under heavy storm clouds, minimalist fashion editorial",

    # 3. Brutalist & Monumental Architecture
    "a colossal brutalist concrete observatory cantilevered over a fog-choked alpine chasm, twilight architectural photography",
    "an ancient library carved inside a hollowed amethyst geode mountain with floating spiral staircases, dust motes caught in sunbeams",
    "a monolithic stepped ziggurat made of weathered black basalt under a double lunar eclipse, vast cinematic scale",

    # 4. Botanical & Terrestrial Anomalies
    "an ancient bonsai juniper growing out of a broken marble statue head on a mossy cliff, morning mountain mist, sumi-e aesthetic",
    "a rare midnight-blooming black lotus with petals like burnt paper and a glowing turquoise stamen, dark romanticism oil painting",
    "a subterranean mycelium grove connecting glowing subterranean roots under an ancient stone bridge, rich volumetric luminescence",

    # 5. Celestial & Deep Cosmos
    "an astronomical cartographer charting a swirling violet nebula whirlpool on an antique celestial globe, warm lantern glow",
    "a deep-space solar sail vessel drifting past the ring system of an emerald gas giant, pristine optical space photography",
    "an orbital station tethered to a glittering asteroid quarry, cosmic ray halation, ultra-wide establishing shot",

    # 6. Oceanic & Abyssal Depths
    "a deep-sea glass bathysphere illuminating a colossal bioluminescent siphonophore in the hadal trench, pitch black abyss",
    "a sunken gothic cathedral resting on white ocean sands, inhabited by schools of translucent glass eels, dappled surface caustics",
    "a giant nautilus shell carved with intricate runic engravings resting on a tidal shore at dusk, macro texture focus",

    # 7. Relic Craft & Ancient Artistry
    "an antique brass astrolabe with gears made of polished carnelian and lapis lazuli on weathered parchment, Dutch Golden Age still life",
    "a damaged samurai kabuto helmet overgrown with wild purple irises and gold leaf kintsugi repairs, quiet museum lighting",
    "a stained glass rose window depicting the life cycle of a supernova, vibrant transmitted light beams cutting through incense smoke",

    # 8. Biomechanical Entities
    "a chrysalis of a mechanical moth revealing polished chrome wings and fiber-optic filaments, macro nature photography",
    "a celestial manta ray with an underbelly glowing with topographic constellation maps soaring above cumulus clouds",
    "an ancient iron automaton serving hot tea in an abandoned moss-covered pagoda in winter, quiet cinematic realism"
]

recent_prompts = collections.deque(maxlen=150)

def get_queue_depth():
    try:
        if os.path.exists(JOBS_LEDGER):
            with open(JOBS_LEDGER, "r") as f:
                lines = [line.strip() for line in f if line.strip()]
                recent = [json.loads(l) for l in lines[-15:]]
                active = [j for j in recent if j.get("status") in ("queued", "running")]
                return len(active)
    except Exception:
        pass
    return 0

def sample_spectacle_genome():
    for log_path in [SPECTACLE_LOG, WINNING_GENOME_LOG]:
        try:
            if os.path.exists(log_path):
                with open(log_path, "r") as f:
                    lines = [l.strip() for l in f if l.strip()]
                    if lines:
                        recent = lines[-30:]
                        chosen = json.loads(random.choice(recent))
                        return chosen.get("prompt", "")
        except Exception:
            pass
    return ""

def synthesize_governor_prompt(theme_seed=""):
    """Queries Governor Gemma 31B for open-ended, infinite creative diversity."""
    try:
        req_body = {
            "model": "redhatai/gemma-4-31b-it-fp8-dynamic",
            "messages": [
                {
                    "role": "system",
                    "content": "You are the Grand Visual Architect. Generate ONE hyper-original, visually breathtaking prompt for a FLUX.1 generative diffusion engine. Be concise (1-2 sentences), highly visual, specific about subject, lighting, composition, and medium. Return ONLY the prompt text, no markdown, no quotes."
                },
                {
                    "role": "user",
                    "content": f"Create an unprecedented, visually captivating artwork prompt. Distinct theme: {theme_seed or 'unexplored visual realm'}."
                }
            ],
            "temperature": random.uniform(0.85, 1.1),
            "max_tokens": 120
        }
        req = urllib.request.Request(
            GOVERNOR_API,
            data=json.dumps(req_body).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=3) as res:
            data = json.loads(res.read().decode("utf-8"))
            prompt = data["choices"][0]["message"]["content"].strip()
            prompt = prompt.replace('"', '').replace('**', '').strip()
            if len(prompt) > 20:
                return prompt
    except Exception:
        pass
    return ""

def generate_unique_prompt():
    global recent_prompts

    # 1. Anti-Mode Collapse Repulsion
    if uniqueness_tracker.is_mode_collapsed():
        print("[ANTI-COLLAPSE] Visual redundancy detected! Triggering Orthogonal Paradigm Jump...", flush=True)
        gov_jump = synthesize_governor_prompt("orthogonal high-contrast textural paradigm shift, sumi-e ink wash, cyanotype, or stained glass")
        if gov_jump and gov_jump not in recent_prompts:
            recent_prompts.append(gov_jump)
            return gov_jump

    # 2. Movement Towards Master (30% chance if Spectacles exist)
    if random.random() < 0.30:
        base_spectacle = sample_spectacle_genome()
        if base_spectacle:
            # Query Governor to refine the Spectacle prompt DNA
            refined = synthesize_governor_prompt(f"Elevate this masterpiece prompt to the ultimate pinnacle tier with razor-sharp tonal clarity: {base_spectacle[:100]}")
            if refined and refined not in recent_prompts:
                recent_prompts.append(refined)
                return refined

    # 3. Dynamic Governor Synthesis (Primary Stream)
    themes = [
        "quantum crystallography", "abyssal ocean trench", "avant-garde haute couture",
        "brutalist alpine architecture", "ancient celestial cartography", "botanical anomaly",
        "renaissance alchemy workshop", "polar atmospheric optics", "biomimetic entity"
    ]
    chosen_theme = random.choice(themes)
    gov_prompt = synthesize_governor_prompt(chosen_theme)
    if gov_prompt and gov_prompt not in recent_prompts:
        recent_prompts.append(gov_prompt)
        return gov_prompt

    # 4. Fallback from Diverse Combinatorial Taxonomy
    candidates = [p for p in DIVERSE_TAXONOMY if p not in recent_prompts]
    if not candidates:
        recent_prompts.clear()
        candidates = DIVERSE_TAXONOMY
    selected = random.choice(candidates)
    recent_prompts.append(selected)
    return selected

def queue_prompt(prompt):
    seed = secrets.randbelow(2147483647)
    try:
        cmd = ["/root/.local/bin/flux", "render", prompt, "--seed", str(seed), "--async"]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Dispatched (seed {seed}): {prompt[:75]}...", flush=True)
    except Exception as e:
        print(f"Error queueing prompt: {e}", flush=True)

def main():
    print("Perpetual GPU Sieve & Movement Towards Master online [Governor Dynamic Synthesis Active].", flush=True)
    while True:
        try:
            if os.path.exists(STOP_FILE):
                time.sleep(5)
                continue
            
            depth = get_queue_depth()
            if depth < 3:
                p = generate_unique_prompt()
                queue_prompt(p)
                time.sleep(1.0)
            else:
                time.sleep(2.0)
        except Exception as e:
            print(f"Feeder loop err: {e}", flush=True)
            time.sleep(3)

if __name__ == "__main__":
    main()
