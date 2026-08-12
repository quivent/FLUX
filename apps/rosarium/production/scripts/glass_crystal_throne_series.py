import os
import subprocess
import time

os.makedirs("/home/ubuntu/arcane-princess-studio/renders", exist_ok=True)
os.makedirs("/home/ubuntu/arcane-princess-studio/thumbs", exist_ok=True)

prompts = [
    ("glass_crystal_throne_01", 4201, "Arcane Princess haute couture synthesis, Italian supermodel beauty, seated gracefully on a glowing translucent glass crystal throne, cathedral glass conservatory, blooming procedural 3D roses, floating crimson velvet rose petals, jewel-toned cyan and magenta rim lighting"),
    ("glass_crystal_throne_02", 4202, "Arcane Princess close-up, Italian supermodel facial structure, leaning back on her glass crystal throne, light refracting through crystalline roses and glass conservatory windows, soft painterly 2D/3D hybrid brushwork"),
    ("glass_crystal_throne_03", 4203, "Full-length view of Arcane Princess in obsidian silk gown inside the glass crystal conservatory, floating crimson velvet rose petals reflecting in the polished glass floor, holding a radiating procedural 3D rose"),
    ("glass_crystal_throne_04", 4204, "Dramatic midnight chiaroscuro portrait of Arcane Princess in her glass crystal throne room, starlight and moonlight refracting through crystal prisms, jewel-toned cyan and magenta edge highlights")
]

print("[+] Rendering Glass Crystal Throne Room Series...")

for name, seed, prompt in prompts:
    filename = f"{name}.png"
    print(f"[*] Rendering {filename} (Seed {seed})...")
    
    cmd = ["flux", "render", "--direct", "--seed", str(seed), prompt]
    env = os.environ.copy()
    env["CUDA_VISIBLE_DEVICES"] = "1"
    
    t0 = time.time()
    res = subprocess.run(cmd, env=env, capture_output=True, text=True, cwd="/home/ubuntu/FLUX")
    t1 = time.time() - t0
    print(f"  -> Finished {name} in {t1:.2f}s")
    
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

print("[+] Glass Crystal Throne Room Series Complete!")
