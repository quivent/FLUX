import os
from PIL import Image

renders_dir = "/home/ubuntu/arcane-princess-studio/renders"

blends_config = [
    {
        "name": "arcane_motion_blend_throne_series.gif",
        "fps": 12.5,
        "steps": 8,
        "files": [
            "seed42_throne_room.png",
            "glass_crystal_throne_01.png",
            "glass_crystal_throne_02.png",
            "glass_crystal_throne_03.png",
            "glass_crystal_throne_04.png",
            "seed42_haute_couture_throne.png"
        ]
    },
    {
        "name": "arcane_motion_blend_outcast_styles.gif",
        "fps": 12.5,
        "steps": 6,
        "files": [
            "princess_dark_cel_anim.png",
            "princess_mystical_tarot.png",
            "princess_noir_film_still.png",
            "princess_graphic_novel.png",
            "princess_baroque_fantasy.png",
            "princess_ink_wash.png",
            "princess_high_fashion.png",
            "princess_dark_fairytale.png",
            "princess_gothic_anime.png",
            "princess_arcane_outcast.png"
        ]
    },
    {
        "name": "arcane_motion_blend_crown_jewel.gif",
        "fps": 12.5,
        "steps": 8,
        "files": [
            "crown_jewel_01_solaris.png",
            "crown_jewel_02_nebula.png",
            "crown_jewel_03_valkyrie.png",
            "crown_jewel_04_chrysalis.png",
            "quantum_leap_masterpiece.png"
        ]
    }
]

for cfg in blends_config:
    gif_name = cfg["name"]
    print(f"[*] Processing Motion Blend: {gif_name}...")
    
    images = []
    for f in cfg["files"]:
        path = os.path.join(renders_dir, f)
        if os.path.exists(path):
            img = Image.open(path).convert("RGB").resize((640, 800), Image.Resampling.LANCZOS)
            images.append(img)
            
    if not images:
        print(f"  -> Skipping {gif_name}, no images found.")
        continue
        
    frames = []
    steps_per_transition = cfg["steps"]
    
    for i in range(len(images)):
        img_a = images[i]
        img_b = images[(i + 1) % len(images)]
        for step in range(steps_per_transition):
            alpha = step / float(steps_per_transition)
            blended = Image.blend(img_a, img_b, alpha)
            frames.append(blended)
            
    output_gif = os.path.join(renders_dir, gif_name)
    output_brain = f"/home/ubuntu/.gemini/antigravity-cli/brain/01df9f0c-2832-4716-8eca-979d459909fc/{gif_name}"
    
    frames[0].save(
        output_gif,
        save_all=True,
        append_images=frames[1:],
        duration=int(1000 / cfg["fps"]),
        loop=0
    )
    
    os.system(f"cp {output_gif} {output_brain}")
    os.system(f"gemstone store push {output_gif} surface/renders/{gif_name}")
    print(f"  -> Successfully generated & pushed: {gif_name} ({len(frames)} frames)")

print("[+] All Motion Blend Animations Successfully Complete!")
