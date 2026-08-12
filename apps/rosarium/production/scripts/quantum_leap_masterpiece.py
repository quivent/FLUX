import os
import subprocess
import time

os.makedirs("/home/ubuntu/arcane-princess-studio/renders", exist_ok=True)
os.makedirs("/home/ubuntu/arcane-princess-studio/thumbs", exist_ok=True)

prompt = "Breathtaking Arcane Princess quantum masterpiece, Italian supermodel facial perfection, glowing sapphire eyes reflecting cosmic glass garden, hand-painted 2D Fortiche oil brushwork over hyper-detailed 3D sculpted mesh, procedural 3D rose radiating hyper-real crimson and gold light, cyan/magenta dual studio rim lighting, cinematic 8k resolution chiaroscuro"

print("[+] Launching Quantum Leap Tooling Execution...")

cmd = ["flux", "render", "--direct", "--seed", "42999", prompt]
env = os.environ.copy()
env["CUDA_VISIBLE_DEVICES"] = "1"

t0 = time.time()
res = subprocess.run(cmd, env=env, capture_output=True, text=True, cwd="/home/ubuntu/FLUX")
t1 = time.time() - t0
print(f"[+] Rendered Quantum Masterpiece in {t1:.2f}s!")

output_dir = "/home/ubuntu/Models/flux-output"
files = sorted([os.path.join(output_dir, f) for f in os.listdir(output_dir) if f.endswith(".png")], key=os.path.getmtime)
if files:
    latest = files[-1]
    filename = "quantum_leap_masterpiece.png"
    dest_render = f"/home/ubuntu/arcane-princess-studio/renders/{filename}"
    dest_thumb = f"/home/ubuntu/arcane-princess-studio/thumbs/{filename}"
    dest_brain = f"/home/ubuntu/.gemini/antigravity-cli/brain/01df9f0c-2832-4716-8eca-979d459909fc/{filename}"
    subprocess.run(["cp", latest, dest_render])
    subprocess.run(["cp", latest, dest_thumb])
    subprocess.run(["cp", latest, dest_brain])
    
    # Push to R2
    subprocess.run(["gemstone", "store", "push", dest_render, f"surface/renders/{filename}"], capture_output=True)
    print(f"  -> Synced & Pushed: {filename}")
