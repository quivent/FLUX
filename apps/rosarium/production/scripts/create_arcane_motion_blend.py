import os
from PIL import Image

renders_dir = "/home/ubuntu/arcane-princess-studio/renders"

# Top frames to morph/blend together
source_files = [
    "quantum_leap_masterpiece.png",
    "seed42_throne_room.png",
    "vision_01_shattering_eclipse.png",
    "vision_02_leviathan_aquarium.png",
    "vision_03_dragon_reliquary.png",
    "vision_04_mirror_dimension.png",
    "seed42_sapphire_gaze.png",
    "seed42_crimson_cape.png"
]

images = []
for f in source_files:
    path = os.path.join(renders_dir, f)
    if os.path.exists(path):
        img = Image.open(path).convert("RGB").resize((640, 800), Image.Resampling.LANCZOS)
        images.append(img)

print(f"[+] Loaded {len(images)} masterpiece keyframes for motion blend...")

frames = []
steps_per_transition = 8

for i in range(len(images)):
    img_a = images[i]
    img_b = images[(i + 1) % len(images)]
    
    for step in range(steps_per_transition):
        alpha = step / float(steps_per_transition)
        blended = Image.blend(img_a, img_b, alpha)
        frames.append(blended)

output_gif = "/home/ubuntu/arcane-princess-studio/renders/arcane_motion_blend_master.gif"
output_thumb = "/home/ubuntu/arcane-princess-studio/thumbs/arcane_motion_blend_master.gif"
output_brain = "/home/ubuntu/.gemini/antigravity-cli/brain/01df9f0c-2832-4716-8eca-979d459909fc/arcane_motion_blend_master.gif"

print(f"[+] Compiling {len(frames)}-frame smooth animated motion blend GIF...")
frames[0].save(
    output_gif,
    save_all=True,
    append_images=frames[1:],
    duration=80,  # 80ms per frame = 12.5 fps smooth loop
    loop=0
)

os.system(f"cp {output_gif} {output_thumb}")
os.system(f"cp {output_gif} {output_brain}")

# Push to Cloudflare R2
os.system(f"gemstone store push {output_gif} surface/renders/arcane_motion_blend_master.gif")

print("[+] Motion Blend Master GIF Successfully Created & Pushed!")
