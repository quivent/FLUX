import os
import subprocess
import time

os.makedirs("/home/ubuntu/arcane-princess-studio/seed42_collection", exist_ok=True)
os.makedirs("/home/ubuntu/bloom-gallery/seed42_collection", exist_ok=True)

variations = [
    ("haute_couture_throne", "Arcane Princess haute couture synthesis, Italian supermodel beauty, seated on an obsidian crystal throne in an enchanted glass conservatory, holding a glowing procedural 3D rose, floating crimson rose petals, cyan and magenta rim lighting, Fortiche hybrid 3D/2D style"),
    ("haute_couture_close_up", "Extreme close-up portrait of Arcane Princess, Italian supermodel face with sapphire glowing eyes, intricate gold filigree headpiece, procedural 3D rose reflecting in her eyes, soft painterly brush strokes, cyan/magenta edge lighting"),
    ("haute_couture_velvet_cape", "Full-length standing posture of Arcane Princess wearing a flowing crimson velvet cape, carrying a floating luminous 3D rose, background of moonlit Arcane garden archways, painterly brushwork over 3D mesh"),
    ("haute_couture_glass_palace", "Arcane Princess in high-fashion black and gold lace gown, standing inside a grand stained-glass palace with glowing procedural roses blooming around her, jewel-toned cyan/magenta lighting"),
    ("haute_couture_midnight_balcony", "Arcane Princess on a palace balcony looking over an enchanted starry night city, wind blowing her obsidian hair and golden veil, holding a glowing 3D rose, Fortiche painterly aesthetic"),
    ("haute_couture_emerald_garden", "Arcane Princess in an emerald and obsidian haute couture dress, surrounded by glowing bioluminescent 3D flora and procedural roses, dramatic rim lighting"),
    ("haute_couture_mirror_reflections", "Arcane Princess posing in front of enchanted silver mirrors, multiple reflections showing different procedural rose arrangements, cyan/magenta dual lighting, high fashion editorial"),
    ("haute_couture_crimson_veil", "Arcane Princess wearing a translucent crimson silk veil, holding a multi-petaled procedural 3D rose, glowing particles floating in dark glass atrium"),
    ("haute_couture_gold_filigree", "Close-up of Arcane Princess adorned with elaborate gold and sapphire filigree jewelry, procedural 3D rose floating near her shoulder, painterly 2D/3D hybrid style"),
    ("haute_couture_starlight", "Arcane Princess under a celestial sky, starlight illuminating her pale skin and haute couture gown, holding a radiating 3D rose with crystalline petals"),
    ("haute_couture_royal_scepter", "Arcane Princess holding a golden scepter topped with a procedural glowing rose, standing in a regal dark marble hall, dramatic rim lighting"),
    ("haute_couture_moonlit_fountain", "Arcane Princess leaning gracefully against a dark marble fountain filled with floating glowing roses, painterly Fortiche texture over sculpted mesh")
]

print("[+] Starting Seed 42 Haute Couture Collection Generation...")

for idx, (name, prompt) in enumerate(variations, 1):
    print(f"[{idx}/{len(variations)}] Rendering {name}...")
    cmd = [
        "flux", "render", "--direct", "--seed", str(42420 + idx),
        prompt
    ]
    env = os.environ.copy()
    env["CUDA_VISIBLE_DEVICES"] = "1"
    
    start_t = time.time()
    res = subprocess.run(cmd, env=env, capture_output=True, text=True, cwd="/home/ubuntu/FLUX")
    elapsed = time.time() - start_t
    print(f"  -> Finished {name} in {elapsed:.2f}s")
    
    output_dir = "/home/ubuntu/Models/flux-output"
    files = sorted([os.path.join(output_dir, f) for f in os.listdir(output_dir) if f.endswith(".png")], key=os.path.getmtime)
    if files:
        latest = files[-1]
        dest_studio = f"/home/ubuntu/arcane-princess-studio/seed42_{name}.png"
        dest_gallery = f"/home/ubuntu/bloom-gallery/seed42_{name}.png"
        subprocess.run(["cp", latest, dest_studio])
        subprocess.run(["cp", latest, dest_gallery])
        print(f"  -> Synced {dest_studio}")

print("[+] Seed 42 Haute Couture Collection Complete!")
