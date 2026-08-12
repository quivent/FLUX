import os
import subprocess
import time
import multiprocessing

os.makedirs("/home/ubuntu/arcane-princess-studio/renders", exist_ok=True)
os.makedirs("/home/ubuntu/arcane-princess-studio/thumbs", exist_ok=True)

prompts_gpu0 = [
    ("hyper_gpu0_01_supernova", 99001, "Arcane Princess haute couture synthesis, Italian supermodel beauty, standing inside a supernova crystal atrium, holding a radiating 3D procedural rose of liquid gold and starlight, cyan and magenta dual rim lighting"),
    ("hyper_gpu0_02_obsidian_throne", 99002, "Arcane Princess leaning back on an intricate obsidian and sapphire throne, light refracting through crystalline roses and cathedral glass windows, soft painterly 2D/3D Fortiche hybrid texture"),
    ("hyper_gpu0_03_cosmic_veil", 99003, "Arcane Princess wearing a translucent starburst silk veil, sapphire eyes blazing, holding a glowing 3D procedural rose with floating crimson velvet petals in zero gravity"),
    ("hyper_gpu0_04_gothic_spire", 99004, "Arcane Princess standing atop a high gothic spire overlooking an enchanted glass city at midnight, golden filigree gown reflecting starlight")
]

prompts_gpu1 = [
    ("hyper_gpu1_01_dragon_empress", 99011, "Arcane Princess in full obsidian armor and gold lace, standing beside a mechanical glass dragon in a fiery colosseum, golden rose petals swirling in a firestorm"),
    ("hyper_gpu1_02_abyssal_aquarium", 99012, "Arcane Princess in high-fashion obsidian gown standing inside an abyssal glass palace, giant bioluminescent leviathans swimming outside stained glass walls, cyan water caustics"),
    ("hyper_gpu1_03_solstice_cathedral", 99013, "Arcane Princess coronation ceremony inside a grand stained-glass cathedral during summer solstice, light rays bursting through crystal rose windows"),
    ("hyper_gpu1_04_mirror_stardust", 99014, "Arcane Princess touching an infinite recursive glass mirror dimension, crystalline stardust shattering around her as her reflection steps into reality")
]

def render_worker(gpu_id, prompts):
    for name, seed, prompt in prompts:
        filename = f"{name}.png"
        print(f"[HYPER GPU {gpu_id}] Rendering {filename} (Seed {seed})...")
        
        cmd = ["flux", "render", "--direct", "--seed", str(seed), prompt]
        env = os.environ.copy()
        env["CUDA_VISIBLE_DEVICES"] = str(gpu_id)
        
        t0 = time.time()
        res = subprocess.run(cmd, env=env, capture_output=True, text=True, cwd="/home/ubuntu/FLUX")
        t1 = time.time() - t0
        print(f"  -> [HYPER GPU {gpu_id}] Rendered {name} in {t1:.2f}s!")
        
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
            
            # Sync to R2 & Cherry
            subprocess.run(["gemstone", "store", "push", dest_render, f"surface/renders/{filename}"], capture_output=True)
            print(f"  -> [HYPER GPU {gpu_id}] Synced & Pushed: {filename}")

if __name__ == "__main__":
    print("[+] LAUNCHING HYPER-ACCELERATION DUAL-GPU ENGINE OVERDRIVE...")
    p0 = multiprocessing.Process(target=render_worker, args=(0, prompts_gpu0))
    p1 = multiprocessing.Process(target=render_worker, args=(1, prompts_gpu1))
    
    p0.start()
    p1.start()
    
    p0.join()
    p1.join()
    print("[+] HYPER-ACCELERATION DUAL-GPU ENGINE OVERDRIVE COMPLETE!")
