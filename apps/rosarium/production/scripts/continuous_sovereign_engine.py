import os
import subprocess
import time
import glob

print("[+] Starting Autonomous Continuous Sovereign Engine...")

prompts = [
    "Arcane Princess haute couture synthesis, Italian supermodel beauty, seated gracefully on a glowing translucent glass crystal throne, cathedral glass conservatory, blooming procedural 3D roses, floating crimson velvet rose petals, jewel-toned cyan and magenta rim lighting",
    "Arcane Princess close-up portrait, Italian supermodel facial structure, sapphire glowing eyes, leaning back on glass crystal throne, light refracting through crystalline roses and glass conservatory windows",
    "Full-length view of Arcane Princess in obsidian silk gown inside the glass crystal conservatory, floating crimson velvet rose petals reflecting in the polished glass floor, holding a radiating procedural 3D rose",
    "Dramatic midnight chiaroscuro portrait of Arcane Princess in her glass crystal throne room, starlight and moonlight refracting through crystal prisms, jewel-toned cyan and magenta edge highlights",
    "Arcane Princess standing in a grand stained-glass palace with glowing procedural roses blooming around her, high-fashion black and gold lace gown, jewel-toned cyan/magenta lighting",
    "Arcane Princess on a palace balcony looking over an enchanted starry night city, wind blowing her obsidian hair and golden veil, holding a glowing 3D rose, Fortiche painterly aesthetic",
    "Arcane Princess wearing a translucent crimson silk veil, holding a multi-petaled procedural 3D rose, glowing particles floating in dark glass atrium",
    "Close-up of Arcane Princess adorned with elaborate gold and sapphire filigree jewelry, procedural 3D rose floating near her shoulder, painterly 2D/3D hybrid style"
]

os.makedirs("/home/ubuntu/arcane-princess-studio/renders", exist_ok=True)
os.makedirs("/home/ubuntu/arcane-princess-studio/thumbs", exist_ok=True)

counter = 1

while True:
    prompt_idx = (counter - 1) % len(prompts)
    current_prompt = prompts[prompt_idx]
    seed = 42000 + counter
    filename = f"sovereign_masterpiece_{counter:03d}.png"
    
    print(f"[{counter}] Generating {filename} (Seed {seed})...")
    
    cmd = ["flux", "render", "--direct", "--seed", str(seed), current_prompt]
    env = os.environ.copy()
    env["CUDA_VISIBLE_DEVICES"] = "1"
    
    t0 = time.time()
    res = subprocess.run(cmd, env=env, capture_output=True, text=True, cwd="/home/ubuntu/FLUX")
    t1 = time.time() - t0
    print(f"  -> Rendered in {t1:.2f}s")
    
    output_dir = "/home/ubuntu/Models/flux-output"
    files = sorted([os.path.join(output_dir, f) for f in os.listdir(output_dir) if f.endswith(".png")], key=os.path.getmtime)
    if files:
        latest = files[-1]
        dest_render = f"/home/ubuntu/arcane-princess-studio/renders/{filename}"
        dest_thumb = f"/home/ubuntu/arcane-princess-studio/thumbs/{filename}"
        subprocess.run(["cp", latest, dest_render])
        subprocess.run(["cp", latest, dest_thumb])
        
        # Push to Cloudflare R2 / Cherry control plane
        subprocess.run(["gemstone", "store", "push", dest_render, f"surface/renders/{filename}"], capture_output=True)
        print(f"  -> Synced & Pushed to R2 / Cherry: {filename}")
        
    counter += 1
    time.sleep(1)
