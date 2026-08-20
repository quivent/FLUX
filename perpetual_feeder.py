#!/usr/bin/env python3
"""Perpetual GPU Sieve & Movement Towards Master Engine.

Generative Hierarchy:
1. Macro Strategy: Set by Governor Gemma 31B (Council Shard).
2. Exploratory Generation: High-variety combinatorial grammar across subjects & mediums.
3. Movement Towards Master: When a frame achieves Spectacle status (≥90.0), this engine
   initiates targeted mastery convergence iterations to push quality past ≥98.0 (Masterpiece).
"""
import json
import os
import random
import secrets
import subprocess
import time
import urllib.request

STOP_FILE = "/root/STOP"
SPECTACLE_LOG = "/root/Models/flux-output/spectacle_genome.jsonl"
WINNING_GENOME_LOG = "/root/Models/flux-output/winning_genome.jsonl"

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
    "soft diffused dawn light breaking through heavy mountain fog",
    "bright midday sun, sharp clean shadows, pure atmospheric clarity"
]

COMPOSITIONS = [
    "wide establishing shot, cinematic composition, breathtaking scale",
    "intimate close-up portrait, high micro-detail, emotive focus",
    "dynamic diagonal perspective, sense of motion, Dutch angle",
    "centered symmetrical framing, iconic monumentality, serene balance",
    "macro photography, ultra-shallow depth of field, razor-sharp focus"
]

MASTERY_REFINERS = [
    "pristine tonal depth, masterwork dynamic luminance, razor-sharp boundary contours",
    "flawless optical clarity, ethereal atmospheric grading, hyper-precise impasto tooth",
    "supreme architectural poise, immaculate spectral harmony, zero chromatic distortion",
    "museum-grade silver-halide halation, sublime atmospheric depth, iconic mastery"
]

seen_prompts = set()

def get_queue_depth():
    try:
        req = urllib.request.urlopen("http://127.0.0.1:7861/api/jobs", timeout=2)
        data = json.loads(req.read().decode())
        jobs = data.get("jobs", [])
        active = [j for j in jobs if j.get("status") in ("queued", "running")]
        return len(active)
    except Exception:
        return 0

def sample_spectacle_genome():
    for log_path in [SPECTACLE_LOG, WINNING_GENOME_LOG]:
        try:
            if os.path.exists(log_path):
                with open(log_path, "r") as f:
                    lines = [l.strip() for l in f if l.strip()]
                    if lines:
                        recent = lines[-20:]
                        chosen = json.loads(random.choice(recent))
                        return chosen.get("prompt", "")
        except Exception:
            pass
    return ""

def generate_unique_prompt():
    global seen_prompts
    
    # 40% Chance: Movement Towards Master (Refining a Spectacle into a Masterpiece)
    if random.random() < 0.40:
        base_spectacle = sample_spectacle_genome()
        if base_spectacle:
            parts = [p.strip() for p in base_spectacle.split(",") if p.strip()]
            if len(parts) >= 2:
                subj = parts[0]
                media = parts[1]
                light = random.choice(LIGHTING) if random.random() < 0.4 else (parts[2] if len(parts) > 2 else random.choice(LIGHTING))
                mastery_refiner = random.choice(MASTERY_REFINERS)
                comp = parts[3] if len(parts) > 3 else random.choice(COMPOSITIONS)
                
                # Polish into Master candidate
                master_prompt = f"{subj}, {media}, {light}, {comp}, {mastery_refiner}"
                if master_prompt not in seen_prompts:
                    seen_prompts.add(master_prompt)
                    return master_prompt

    # Exploratory Generation
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
    print("Perpetual GPU Sieve & Movement Towards Master online.", flush=True)
    while True:
        if os.path.exists(STOP_FILE):
            print(f"Stop signal ({STOP_FILE}) detected. Pausing...", flush=True)
            time.sleep(5)
            continue
        
        depth = get_queue_depth()
        if depth < 3:
            p = generate_unique_prompt()
            queue_prompt(p)
            time.sleep(1.0)
        else:
            time.sleep(2.0)

if __name__ == "__main__":
    main()
