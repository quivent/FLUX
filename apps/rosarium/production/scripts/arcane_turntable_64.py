import os
import subprocess
import time

os.makedirs("/home/ubuntu/arcane-princess-studio/renders", exist_ok=True)

prompt_base = "Arcane Princess 360 turntable orbit frame, Fortiche 3D/2D hybrid animation style, sculpted 3D mesh with hand-painted brushwork, glowing procedural 3D rose in an enchanted Arcane glass garden, cyan and magenta rim lighting, smooth angle rotation"

print("[+] Starting 64-Frame Turntable Orbit Generation...")

for frame in range(1, 65):
    angle = (frame - 1) * (360.0 / 64.0)
    prompt = f"{prompt_base}, azimuth angle {angle:.1f} degrees"
    
    cmd = [
        "flux", "render", "--direct", "--seed", str(4200 + frame),
        prompt
    ]
    env = os.environ.copy()
    env["CUDA_VISIBLE_DEVICES"] = "1"
    
    start_t = time.time()
    res = subprocess.run(cmd, env=env, capture_output=True, text=True, cwd="/home/ubuntu/FLUX")
    elapsed = time.time() - start_t
    print(f"  -> Frame {frame:02d}/64 (Azimuth {angle:.1f}°) generated in {elapsed:.2f}s")
    
    output_dir = "/home/ubuntu/Models/flux-output"
    files = sorted([os.path.join(output_dir, f) for f in os.listdir(output_dir) if f.endswith(".png")], key=os.path.getmtime)
    if files:
        latest = files[-1]
        dest = f"/home/ubuntu/arcane-princess-studio/renders/frame_{frame:02d}.png"
        subprocess.run(["cp", latest, dest])

print("[+] 64-Frame Turntable Orbit Complete!")
