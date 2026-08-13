import os
import subprocess
import time

os.makedirs("/home/ubuntu/arcane-princess-studio/renders", exist_ok=True)
os.makedirs("/home/ubuntu/arcane-princess-studio/thumbs", exist_ok=True)

# 15 distinct high-variety prompt variations locked into the Seed 42 visual formula
variations = [
    ("seed42_throne_room", 4201, "Arcane Princess haute couture synthesis, Italian supermodel beauty, seated gracefully on a obsidian crystal throne in an enchanted glass conservatory, holding a glowing procedural 3D rose, floating crimson velvet petals, cyan and magenta studio lighting"),
    ("seed42_sapphire_gaze", 4202, "Extreme close-up portrait of Arcane Princess, Italian supermodel facial structure, piercing sapphire glowing eyes, intricate gold filigree headpiece, procedural 3D rose reflecting in her eyes, soft painterly 2D/3D hybrid brushwork, cyan/magenta rim light"),
    ("seed42_crimson_cape", 4203, "Full-length standing posture of Arcane Princess wearing a sweeping crimson velvet cape over gold embroidery, holding a floating luminous 3D rose, moonlit Arcane garden archways, Fortiche painterly texture over 3D mesh"),
    ("seed42_stained_glass", 4204, "Arcane Princess in high-fashion black and gold lace gown, standing inside a cathedral of stained-glass with glowing procedural roses blooming around her, jewel-toned cyan/magenta lighting"),
    ("seed42_balcony_night", 4205, "Arcane Princess on a high palace balcony overlooking an enchanted starry night city, wind blowing her obsidian hair and translucent silk veil, holding a glowing 3D rose, dramatic chiaroscuro lighting"),
    ("seed42_emerald_haven", 4206, "Arcane Princess in an emerald and obsidian haute couture dress, surrounded by glowing bioluminescent 3D flora and procedural roses, intense cyan and magenta edge highlights"),
    ("seed42_silver_mirrors", 4207, "Arcane Princess posing in front of enchanted silver mirrors, multiple reflections showing different procedural rose arrangements, cyan/magenta dual lighting, high fashion editorial composition"),
    ("seed42_silk_veil", 4208, "Arcane Princess wearing a delicate translucent silk veil, holding a multi-petaled procedural 3D rose, glowing luminescent particles floating in dark glass atrium"),
    ("seed42_gold_jewelry", 4209, "Close-up of Arcane Princess adorned with elaborate gold and sapphire filigree jewelry, procedural 3D rose floating near her shoulder, painterly 2D/3D hybrid style"),
    ("seed42_starlight_gown", 4210, "Arcane Princess under a celestial star-filled sky, starlight illuminating her pale skin and haute couture gown, holding a radiating 3D rose with crystalline petals"),
    ("seed42_royal_scepter", 4211, "Arcane Princess holding a golden scepter topped with a procedural glowing rose, standing in a regal dark marble hall, dramatic rim lighting"),
    ("seed42_marble_fountain", 4212, "Arcane Princess leaning gracefully against a dark marble fountain filled with floating glowing roses, painterly Fortiche texture over sculpted mesh"),
    ("seed42_gothic_arch", 4213, "Arcane Princess framed by a massive gothic stone archway, floating glowing rose in hand, cyan and magenta rim light breaking through darkness"),
    ("seed42_velvet_cushion", 4214, "Arcane Princess reclining on a velvet chaise lounge in a royal suite, holding a procedural 3D rose, soft painterly shadows, jewel-toned rim light"),
    ("seed42_coronation_crown", 4215, "Arcane Princess being crowned with an obsidian and sapphire crown, holding a radiating 3D rose, high-contrast studio chiaroscuro lighting")
]

print("[+] Launching 15-Piece Seed 42 Variety Masterpiece Batch...")

# Kill previous rapid task if running
subprocess.run(["pkill", "-f", "rapid_seed42_batch.py"])

for idx, (name, seed, prompt) in enumerate(variations, 1):
    filename = f"{name}.png"
    print(f"[{idx}/15] Rendering {filename} (Seed {seed})...")
    
    cmd = ["flux", "render", "--direct", "--seed", str(seed), prompt]
    env = os.environ.copy()
    env["CUDA_VISIBLE_DEVICES"] = "1"
    
    t0 = time.time()
    res = subprocess.run(cmd, env=env, capture_output=True, text=True, cwd="/home/ubuntu/FLUX")
    t1 = time.time() - t0
    print(f"  -> Rendered {name} in {t1:.2f}s")
    
    output_dir = "/home/ubuntu/Models/flux-output"
    files = sorted([os.path.join(output_dir, f) for f in os.listdir(output_dir) if f.endswith(".png")], key=os.path.getmtime)
    if files:
        latest = files[-1]
        dest_render = f"/home/ubuntu/arcane-princess-studio/renders/{filename}"
        dest_thumb = f"/home/ubuntu/arcane-princess-studio/thumbs/{filename}"
        dest_brain = f"/home/ubuntu/.gemini/antigravity-cli/brain/01df9f0c-2832-4716-8eca-979d459909fc/{filename}"
        subprocess.run(["cp", latest, dest_render])
        subprocess.run(["cp", latest, dest_thumb])
        subprocess.run(["cp", latest, dest_brain])
        print(f"  -> Synced {filename}")

print("[+] 15-Piece Seed 42 Variety Masterpiece Batch Complete!")
