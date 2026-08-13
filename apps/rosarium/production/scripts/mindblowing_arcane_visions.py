import os
import subprocess
import time

os.makedirs("/home/ubuntu/arcane-princess-studio/renders", exist_ok=True)

visions = [
    ("vision_01_shattering_eclipse", 9901, "Arcane Princess haute couture synthesis, Italian supermodel beauty, standing amidst shattering glass prisms during a total solar eclipse, holding a supercharged 3D procedural rose radiating intense cyan energy and electric violet lightning arcs, floating crimson velvet petals in zero gravity, Fortiche 3D/2D hybrid master texture"),
    ("vision_02_leviathan_aquarium", 9902, "Arcane Princess in high-fashion obsidian gown standing inside a massive underwater glass palace, giant bioluminescent deep-sea leviathans swimming outside stained glass walls, cyan water caustics illuminating her face, holding a glowing 3D underwater rose"),
    ("vision_03_dragon_reliquary", 9903, "Arcane Princess standing atop a colossal mechanical glass dragon in a dark obsidian colosseum, glowing golden rose petals swirling in a firestorm, dramatic chiaroscuro cyan and magenta rim lighting"),
    ("vision_04_mirror_dimension", 9904, "Arcane Princess touching the surface of an infinite recursive glass mirror dimension, her reflection stepping out into reality with glowing sapphire eyes, crystalline stardust shattering around her")
]

print("[+] Launching Mind-Blowing Arcane Visions Series...")

for name, seed, prompt in visions:
    filename = f"{name}.png"
    print(f"[*] Rendering {filename} (Seed {seed})...")
    
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
        dest_brain = f"/home/ubuntu/.gemini/antigravity-cli/brain/01df9f0c-2832-4716-8eca-979d459909fc/{filename}"
        subprocess.run(["cp", latest, dest_render])
        subprocess.run(["cp", latest, dest_brain])
        
        # Sync to R2
        subprocess.run(["gemstone", "store", "push", dest_render, f"surface/renders/{filename}"], capture_output=True)
        print(f"  -> Synced & Pushed: {filename}")

print("[+] Mind-Blowing Arcane Visions Series Complete!")
