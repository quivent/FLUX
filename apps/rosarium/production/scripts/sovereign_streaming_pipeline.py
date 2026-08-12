import os
import subprocess
import time
import json
import glob

print("[+] Starting Sovereign Unified Streaming Pipeline Engine...")

renders_dir = "/home/ubuntu/arcane-princess-studio/renders"
thumbs_dir = "/home/ubuntu/arcane-princess-studio/thumbs"
html_file = "/home/ubuntu/arcane-princess-studio/index.html"

os.makedirs(renders_dir, exist_ok=True)
os.makedirs(thumbs_dir, exist_ok=True)

# 4 Growth Directions
growth_collections = [
    {
        "name": "Seed 42 Masterpieces",
        "tag_prefix": "SEED 42 MASTERPIECE",
        "prompts": [
            "Arcane Princess haute couture synthesis, Italian supermodel beauty, seated gracefully on a glowing translucent glass crystal throne, cathedral glass conservatory, blooming procedural 3D roses, floating crimson velvet rose petals, jewel-toned cyan and magenta rim lighting",
            "Arcane Princess close-up portrait, Italian supermodel facial structure, sapphire glowing eyes, leaning back on glass crystal throne, light refracting through crystalline roses and glass conservatory windows",
            "Full-length view of Arcane Princess in obsidian silk gown inside the glass crystal conservatory, floating crimson velvet rose petals reflecting in the polished glass floor, holding a radiating procedural 3D rose"
        ]
    },
    {
        "name": "Mind-Blowing Visions",
        "tag_prefix": "MIND-BLOWING VISION",
        "prompts": [
            "Arcane Princess haute couture synthesis, Italian supermodel beauty, standing amidst shattering glass prisms during a total solar eclipse, holding a supercharged 3D procedural rose radiating intense cyan energy and electric violet lightning arcs, floating crimson velvet petals in zero gravity, Fortiche 3D/2D hybrid master texture",
            "Arcane Princess in high-fashion obsidian gown standing inside a massive underwater glass palace, giant bioluminescent deep-sea leviathans swimming outside stained glass walls, cyan water caustics illuminating her face, holding a glowing 3D underwater rose",
            "Arcane Princess standing atop a colossal mechanical glass dragon in a dark obsidian colosseum, glowing golden rose petals swirling in a firestorm, dramatic chiaroscuro cyan and magenta rim lighting"
        ]
    },
    {
        "name": "Crown Jewels",
        "tag_prefix": "CROWN JEWEL",
        "prompts": [
            "Arcane Princess haute couture synthesis, Italian supermodel beauty, wearing a blazing solar crown of 3D procedural roses, cathedral glass atrium bursting with golden light rays, chiaroscuro cyan and magenta rim lighting",
            "Arcane Princess leaning against a starlight glass balustrade, cosmic nebula galaxy floating inside her 3D procedural glass rose, bioluminescent dress of sapphire stardust",
            "Arcane Princess in full obsidian armor and gold lace, standing amidst a storm of floating crimson velvet petals in a glass colosseum, sapphire eyes blazing"
        ]
    }
]

cycle_count = 1

while True:
    col_idx = (cycle_count - 1) % len(growth_collections)
    active_col = growth_collections[col_idx]
    prompt_idx = (cycle_count - 1) % len(active_col["prompts"])
    current_prompt = active_col["prompts"][prompt_idx]
    
    seed = 90000 + cycle_count
    filename = f"stream_pipeline_{cycle_count:04d}.png"
    tag = f"{active_col['tag_prefix']} #{cycle_count:04d}"
    
    print(f"[{cycle_count}] Pipeline Synthesis: {filename} ({active_col['name']} • Seed {seed})...")
    
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
        dest_render = os.path.join(renders_dir, filename)
        dest_thumb = os.path.join(thumbs_dir, filename)
        
        subprocess.run(["cp", latest, dest_render])
        subprocess.run(["cp", latest, dest_thumb])
        
        # Push to R2 and Cherry control plane
        subprocess.run(["gemstone", "store", "push", dest_render, f"surface/renders/{filename}"], capture_output=True)
        print(f"  -> Synced & Pushed to R2 / Cherry: {filename}")
        
    cycle_count += 1
    time.sleep(1)
