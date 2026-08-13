import os
import subprocess
import time
import multiprocessing

os.makedirs("/home/ubuntu/arcane-princess-studio/renders", exist_ok=True)
os.makedirs("/home/ubuntu/arcane-princess-studio/thumbs", exist_ok=True)

prompts_gpu0 = [
    ("max_gpu0_01_solstice", 8801, "Arcane Princess haute couture synthesis, Italian supermodel beauty, standing in a crystal atrium during midsummer solstice, holding a blindingly radiant 3D procedural gold rose, cyan/magenta chiaroscuro"),
    ("max_gpu0_02_diamond_veil", 8802, "Arcane Princess wearing a floating diamond filigree veil, sapphire glowing eyes, light refracting in hyper-detailed glass prisms, painterly 2D/3D Fortiche hybrid texture"),
    ("max_gpu0_03_cyber_cathedral", 8803, "Arcane Princess in high-fashion obsidian gown standing inside a cyber-arcane cathedral with floating holograms of procedural 3D roses, deep cyan rim light"),
    ("max_gpu0_04_starburst_throne", 8804, "Arcane Princess leaning gracefully on a starburst glass crystal throne, floating crimson velvet rose petals in zero gravity, Italian supermodel facial perfection")
]

prompts_gpu1 = [
    ("max_gpu1_01_phoenix", 8811, "Arcane Princess wearing a phoenix-feather obsidian dress, procedural 3D fire rose blooming in her hand, dramatic golden and cyan dual rim lighting"),
    ("max_gpu1_02_lunar_fountain", 8812, "Arcane Princess sitting beside a glowing moonlit marble fountain inside an enchanted glass conservatory, reflecting in polished glass floors"),
    ("max_gpu1_03_celestial_scepter", 8813, "Arcane Princess holding a diamond royal scepter topped with a radiating procedural 3D rose, starlight and moonlight illuminating her face"),
    ("max_gpu1_04_empress_coronation", 8814, "Arcane Princess coronation ceremony inside a grand stained-glass palace, golden filigree crown adorned with sapphire crystals, Italian supermodel elegance")
]

def render_batch(gpu_id, prompts):
    for name, seed, prompt in prompts:
        filename = f"{name}.png"
        print(f"[GPU {gpu_id}] Rendering {filename} (Seed {seed})...")
        
        cmd = ["flux", "render", "--direct", "--seed", str(seed), prompt]
        env = os.environ.copy()
        env["CUDA_VISIBLE_DEVICES"] = str(gpu_id)
        
        t0 = time.time()
        res = subprocess.run(cmd, env=env, capture_output=True, text=True, cwd="/home/ubuntu/FLUX")
        t1 = time.time() - t0
        print(f"  -> [GPU {gpu_id}] Rendered {name} in {t1:.2f}s!")
        
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
            print(f"  -> [GPU {gpu_id}] Synced & Pushed: {filename}")

if __name__ == "__main__":
    print("[+] Launching Dual-GPU Parallel Maximization Synthesis (GPU 0 & GPU 1)...")
    p0 = multiprocessing.Process(target=render_batch, args=(0, prompts_gpu0))
    p1 = multiprocessing.Process(target=render_batch, args=(1, prompts_gpu1))
    
    p0.start()
    p1.start()
    
    p0.join()
    p1.join()
    print("[+] Dual-GPU Parallel Maximization Synthesis Complete!")
