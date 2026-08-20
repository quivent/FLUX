#!/usr/bin/env python3
"""Perpetual GPU Sieve: High-variety, zero-repeat non-stop generative engine.

Imports the rich grammar from chorus.language and generates 100% unique seeds
and prompt evolutions.
"""
import json
import os
import random
import secrets
import subprocess
import time
import urllib.request

STOP_FILE = "/root/STOP"

# Rich combinatorial dictionary inspired by chorus/language.py
SUBJECTS = [
    "a solitary cybernetic tea master pouring steam into a glowing porcelain bowl",
    "an intricate mechanical sakura tree with glass petals and fiber-optic roots",
    "a submerged glowing coral pagoda surrounded by bioluminescent manta rays",
    "an ethereal celestial oracle crowned with orbiting holographic aurora rings",
    "a chitinous clockwork beetle encrusted with emerald gemstones and brass gears",
    "an ancient wanderer gazing into a swirling violet nebula whirlpool",
    "a biomechanical koi fish swimming through a liquid crystal atmospheric river",
    "an arcane rose sculpted from dark obsidian with molten gold veins",
    "a futuristic monolithic tower rising above floating cloud terraces",
    "a surreal quantum kaleidoscope landscape with geometric iridescent prisms",
    "a cloaked astral sorceress holding a miniature burning star in her palm",
    "a serene samurai in polished porcelain armor under falling crimson leaves",
    "a street musician playing an iridescent glass violin in neon-lit rain",
    "an overgrown greenhouse filled with bioluminescent carnivorous flora",
    "a delicate paper origami dragon soaring through a sunlit cloudscape",
    "an alchemist workshop filled with glowing amber vials and brass astrolabes",
    "a majestic cybernetic stag with crystalline antlers in a snowy forest",
    "a vintage train crossing a colossal stone viaduct into a starry twilight",
    "a deep-sea diver discovering a luminous sunken cathedral of mirrors",
    "a towering biomechanical golem standing guardian over an ancient ruin"
]

MEDIA_STYLES = [
    "90s anime film still, hand-painted background, soft film grain, warm analogue colour",
    "sumi-e ink wash, wet black ink blooming into absorbent paper, decisive stroke",
    "woodblock print, carved flat colour planes, bold outlines, bare paper highlights",
    "risograph print, two inks slightly misregistered, coarse halftone texture",
    "cinematic 35mm film still, Kodak Portra 400, shallow depth of field, dramatic rim lighting",
    "octane render, volumetric mist, iridescent subsurface scattering, 8k masterpiece",
    "stained glass panel, black leading, saturated transmitted light, flat jewel colour",
    "gouache painting, opaque matte pigment, ragged brush edges, simplified shapes",
    "charcoal drawing, rough black strokes, smudged tone, textured paper tooth",
    "tintype photograph, silver halation, shallow focus, hand-poured chemical edges",
    "haute-couture surrealist oil painting, rich impasto texture, dark romanticism",
    "cyanotype print, deep Prussian blue, crisp silhouettes, brush-coated edges"
]

LIGHTING = [
    "lit by the warm glow of dusk and a single amber lantern",
    "bathed in cool moonlight and shimmering neon reflections",
    "dramatic low-angle sunlight cutting through drifting volumetric mist",
    "under a violet lunar eclipse with faint iridescent bioluminescence",
    "bright midday sun, sharp clean shadows, pure atmospheric clarity",
    "soft diffused dawn light breaking through heavy mountain fog"
]

COMPOSITIONS = [
    "wide establishing shot, cinematic composition, breathtaking scale",
    "intimate close-up portrait, high micro-detail, emotive focus",
    "dynamic diagonal perspective, sense of motion, Dutch angle",
    "centered symmetrical framing, iconic monumentality, serene balance",
    "macro photography, ultra-shallow depth of field, razor-sharp focus"
]

seen_prompts = set()

def get_queue_depth():
    try:
        req = urllib.request.urlopen("http://127.0.0.1:7861/api/jobs", timeout=2)
        data = json.loads(req.read().decode())
        jobs = data.get("jobs", [])
        active = [j for j in jobs if j.get("status") in ("queued", "running")]
        return len(active)
    except Exception as e:
        return 0

def generate_unique_prompt():
    global seen_prompts
    for _ in range(50):
        subj = random.choice(SUBJECTS)
        media = random.choice(MEDIA_STYLES)
        light = random.choice(LIGHTING)
        comp = random.choice(COMPOSITIONS)
        prompt = f"{subj}, {media}, {light}, {comp}"
        if prompt not in seen_prompts:
            if len(seen_prompts) > 1000:
                seen_prompts.clear()
            seen_prompts.add(prompt)
            return prompt
    return prompt

def queue_prompt(prompt):
    seed = secrets.randbelow(2147483647)
    try:
        cmd = ["/root/.local/bin/flux", "render", prompt, "--seed", str(seed), "--async"]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Dispatched (seed {seed}): {prompt[:65]}...", flush=True)
    except Exception as e:
        print(f"Error queueing prompt: {e}", flush=True)

def main():
    print("Perpetual GPU Sieve online. Zero-repeat generator active.", flush=True)
    while True:
        if os.path.exists(STOP_FILE):
            print(f"Stop signal ({STOP_FILE}) detected. Pausing...", flush=True)
            time.sleep(5)
            continue

        depth = get_queue_depth()
        if depth < 3:
            needed = 3 - depth
            for _ in range(needed):
                prompt = generate_unique_prompt()
                queue_prompt(prompt)
                time.sleep(0.5)

        time.sleep(2)

if __name__ == "__main__":
    main()
