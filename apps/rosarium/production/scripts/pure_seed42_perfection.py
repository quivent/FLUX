import os
import subprocess
import time

os.makedirs("/home/ubuntu/arcane-princess-studio", exist_ok=True)
os.makedirs("/home/ubuntu/bloom-gallery", exist_ok=True)

# Exact prompt structure of arcane_princess_seed42.png with slight camera/pose variations
prompts = [
    ("seed42_masterpiece_01", 42, "Arcane Princess haute couture synthesis, Italian supermodel beauty, hand-painted 2D brushwork over 3D sculpted mesh, glowing procedural 3D rose in an enchanted Arcane glass garden, floating crimson velvet rose petals, jewel-toned cyan/magenta rim lighting"),
    ("seed42_masterpiece_02", 43, "Arcane Princess haute couture portrait, Italian supermodel beauty, close-up face with glowing cyan catchlight, hand-painted 2D brushwork over 3D sculpted mesh, holding a glowing procedural 3D rose, jewel-toned cyan/magenta rim lighting"),
    ("seed42_masterpiece_03", 44, "Arcane Princess haute couture full shot, Italian supermodel beauty in an obsidian silk gown, hand-painted 2D brushwork over 3D sculpted mesh, enchanted Arcane glass garden, glowing 3D roses, jewel-toned cyan/magenta rim lighting"),
    ("seed42_masterpiece_04", 45, "Arcane Princess haute couture side profile, Italian supermodel beauty, golden filigree headpiece, hand-painted 2D brushwork over 3D sculpted mesh, floating crimson velvet rose petals, jewel-toned cyan/magenta rim lighting"),
    ("seed42_masterpiece_05", 46, "Arcane Princess haute couture sitting on crystal throne, Italian supermodel beauty, hand-painted 2D brushwork over 3D sculpted mesh, holding glowing procedural 3D rose, glass greenhouse background, jewel-toned cyan/magenta rim lighting"),
    ("seed42_masterpiece_06", 47, "Arcane Princess haute couture wearing crimson velvet cloak, Italian supermodel beauty, hand-painted 2D brushwork over 3D sculpted mesh, surrounded by bioluminescent glowing 3D roses, jewel-toned cyan/magenta rim lighting")
]

print("[+] Generating Pure Seed 42 Perfection Series...")

for name, seed, prompt in prompts:
    print(f"[*] Rendering {name} (Seed {seed})...")
    cmd = ["flux", "render", "--direct", "--seed", str(seed), prompt]
    env = os.environ.copy()
    env["CUDA_VISIBLE_DEVICES"] = "1"
    
    t0 = time.time()
    res = subprocess.run(cmd, env=env, capture_output=True, text=True, cwd="/home/ubuntu/FLUX")
    t1 = time.time() - t0
    print(f"  -> Finished in {t1:.2f}s")
    
    output_dir = "/home/ubuntu/Models/flux-output"
    files = sorted([os.path.join(output_dir, f) for f in os.listdir(output_dir) if f.endswith(".png")], key=os.path.getmtime)
    if files:
        latest = files[-1]
        dest1 = f"/home/ubuntu/arcane-princess-studio/{name}.png"
        dest2 = f"/home/ubuntu/bloom-gallery/{name}.png"
        subprocess.run(["cp", latest, dest1])
        subprocess.run(["cp", latest, dest2])
        print(f"  -> Synced {dest1}")

print("[+] Pure Seed 42 Perfection Series Complete!")
