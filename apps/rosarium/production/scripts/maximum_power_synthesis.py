import os
import subprocess
import time

os.makedirs("/home/ubuntu/arcane-princess-studio/renders", exist_ok=True)
os.makedirs("/home/ubuntu/arcane-princess-studio/thumbs", exist_ok=True)

crown_prompts = [
    ("crown_jewel_01_solaris", 7701, "Arcane Princess haute couture synthesis, Italian supermodel beauty, wearing a blazing solar crown of 3D procedural roses, cathedral glass atrium bursting with golden light rays, chiaroscuro cyan and magenta rim lighting"),
    ("crown_jewel_02_nebula", 7702, "Arcane Princess leaning against a starlight glass balustrade, cosmic nebula galaxy floating inside her 3D procedural glass rose, bioluminescent dress of sapphire stardust"),
    ("crown_jewel_03_valkyrie", 7703, "Arcane Princess in full obsidian armor and gold lace, standing amidst a storm of floating crimson velvet petals in a glass colosseum, sapphire eyes blazing"),
    ("crown_jewel_04_chrysalis", 7704, "Arcane Princess emerging from a glowing 3D crystal rose chrysalis, light refracting in hyper-detailed glass prisms, painterly Fortiche 2D/3D hybrid master texture")
]

print("[+] MAXIMUM POWER SYNTHESIS ENGINE UNLEASHED ON GPU 1...")

for name, seed, prompt in crown_prompts:
    filename = f"{name}.png"
    print(f"[*] Rendering {filename} (Seed {seed})...")
    
    cmd = ["flux", "render", "--direct", "--seed", str(seed), prompt]
    env = os.environ.copy()
    env["CUDA_VISIBLE_DEVICES"] = "1"
    
    t0 = time.time()
    res = subprocess.run(cmd, env=env, capture_output=True, text=True, cwd="/home/ubuntu/FLUX")
    t1 = time.time() - t0
    print(f"  -> Rendered {name} in {t1:.2f}s!")
    
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
        
        # Sync to R2
        subprocess.run(["gemstone", "store", "push", dest_render, f"surface/renders/{filename}"], capture_output=True)
        print(f"  -> Synced & Pushed to R2: {filename}")

print("[+] MAXIMUM POWER SYNTHESIS COMPLETE!")
