import os
import subprocess
import time

os.makedirs("/home/ubuntu/arcane-princess-studio/renders", exist_ok=True)
os.makedirs("/home/ubuntu/arcane-princess-studio/thumbs", exist_ok=True)

prompt = "Arcane Princess haute couture synthesis, Italian supermodel beauty, hand-painted 2D brushwork over 3D sculpted mesh, glowing procedural 3D rose in an enchanted Arcane glass garden, floating crimson velvet rose petals, jewel-toned cyan/magenta rim lighting"

print("[+] Launching Rapid 30-Piece Seed 42 Masterpiece Batch...")

for i in range(1, 31):
    seed = 4200 + i
    filename = f"seed42_rapid_{i:02d}.png"
    print(f"[{i}/30] Rapid Render {filename} (Seed {seed})...")
    
    cmd = ["flux", "render", "--direct", "--seed", str(seed), prompt]
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
        dest_brain = f"/home/ubuntu/.gemini/antigravity-cli/brain/01df9f0c-2832-4716-8eca-979d459909fc/{filename}"
        subprocess.run(["cp", latest, dest_render])
        subprocess.run(["cp", latest, dest_thumb])
        subprocess.run(["cp", latest, dest_brain])
        print(f"  -> Synced {filename}")

print("[+] Rapid 30-Piece Seed 42 Masterpiece Batch Complete!")
