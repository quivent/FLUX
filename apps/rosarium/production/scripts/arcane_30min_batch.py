import os
import subprocess
import time

styles = [
    ("arcane_outcast", "A dark black-clad outcast princess, fully human stunning adult outcast royal woman with ordinary human ears, Arcane-inspired hybrid animation still, sculpted 3D form with hand-painted 2D brushwork, black wool mantle, smooth obsidian crown, haunted luminous eyes, graphic shadows, jewel-toned rim light"),
    ("gothic_anime", "A dark black-clad outcast princess, fully human stunning adult outcast royal woman with ordinary human ears, gothic anime key visual, sharp elegant linework, smoky black roses, moonlit palace ruins, high-detail black costume, dramatic silver highlights, severe beauty"),
    ("dark_fairytale", "A dark black-clad outcast princess, fully human stunning adult outcast royal woman with ordinary human ears, dark fairytale oil painting, luminous pale skin, black velvet gown, smooth obsidian crown, candlelit royal chamber, old master lighting, refined brush texture"),
    ("high_fashion", "A dark black-clad outcast princess, fully human stunning adult outcast royal woman with ordinary human ears, high fashion fantasy editorial portrait, avant-garde black wool couture, silver embroidery, harsh flash photography, black marble set, icy royal stare"),
    ("ink_wash", "A dark black-clad outcast princess, fully human stunning adult outcast royal woman with ordinary human ears, expressive ink wash animation frame, bold black brush strokes, minimal moonlit background, pale face emerging from shadow, elegant melancholy"),
    ("baroque_fantasy", "A dark black-clad outcast princess, fully human stunning adult outcast royal woman with ordinary human ears, ornate baroque dark fantasy, black lace veil, smooth obsidian crown, cathedral shadows, gold and silver filigree, theatrical portrait lighting"),
    ("graphic_novel", "A dark black-clad outcast princess, fully human stunning adult outcast royal woman with ordinary human ears, graphic novel cover, bold black shapes, red accent flowers, angular face, hard rim light, crisp silhouette, dramatic contrast"),
    ("noir_film_still", "A dark black-clad outcast princess, fully human stunning adult outcast royal woman with ordinary human ears, noir fantasy film still, black crown silhouette, rain on palace glass, single eye catchlight, deep shadows, cinematic close-up"),
    ("mystical_tarot", "A dark black-clad outcast princess, fully human stunning adult outcast royal woman with ordinary human ears, mystical tarot card illustration, black rose emblem, smooth obsidian crown, roses, moon, silver border ornaments, solemn powerful beauty"),
    ("dark_cel_anim", "A dark black-clad outcast princess, fully human stunning adult outcast royal woman with ordinary human ears, dark cel animation close-up, crisp painted shadows, limited palette, expressive eyes, black cloak, moonlit wind, dangerous elegance")
]

os.makedirs("/home/ubuntu/arcane-princess-studio", exist_ok=True)
os.makedirs("/home/ubuntu/bloom-gallery", exist_ok=True)

print("[+] Starting 30-Minute Arcane Princess Batch Synthesis...")

for idx, (name, prompt) in enumerate(styles, 1):
    print(f"[{idx}/10] Rendering {name}...")
    cmd = [
        "flux", "render", "--direct", "--seed", str(idx * 100),
        prompt
    ]
    env = os.environ.copy()
    env["CUDA_VISIBLE_DEVICES"] = "1"
    
    start_t = time.time()
    res = subprocess.run(cmd, env=env, capture_output=True, text=True, cwd="/home/ubuntu/FLUX")
    elapsed = time.time() - start_t
    print(f"  -> Finished in {elapsed:.2f}s")
    
    # Copy latest output file to studio & bloom gallery
    output_dir = "/home/ubuntu/Models/flux-output"
    files = sorted([os.path.join(output_dir, f) for f in os.listdir(output_dir) if f.endswith(".png")], key=os.path.getmtime)
    if files:
        latest = files[-1]
        dest_studio = f"/home/ubuntu/arcane-princess-studio/princess_{name}.png"
        dest_gallery = f"/home/ubuntu/bloom-gallery/princess_{name}.png"
        subprocess.run(["cp", latest, dest_studio])
        subprocess.run(["cp", latest, dest_gallery])
        print(f"  -> Synced {dest_studio}")

print("[+] 30-Minute Batch Complete!")
