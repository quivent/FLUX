import os
import json

renders_dir = "/home/ubuntu/arcane-princess-studio/renders"
all_files = sorted(os.listdir(renders_dir))

# Prioritize Masterpieces at the top
priority_files = [
    "quantum_leap_masterpiece.png",
    "seed42_throne_room.png",
    "seed42_masterpiece_01.png",
    "seed42_masterpiece_02.png",
    "seed42_masterpiece_03.png",
    "seed42_masterpiece_04.png",
    "seed42_masterpiece_05.png",
    "seed42_masterpiece_06.png",
    "seed42_sapphire_gaze.png",
    "seed42_crimson_cape.png",
    "seed42_stained_glass.png",
    "seed42_balcony_night.png",
    "seed42_emerald_haven.png",
    "seed42_silver_mirrors.png",
    "seed42_silk_veil.png",
    "seed42_gold_jewelry.png",
    "blackwell-lane1-suzanne-v1.png",
    "blackwell-lane2-suzanne-v1.png",
    "vision_01_shattering_eclipse.png",
    "vision_02_leviathan_aquarium.png",
    "vision_03_dragon_reliquary.png",
    "vision_04_mirror_dimension.png"
]

grid_items = []

# First add priority files
for f in priority_files:
    if os.path.exists(os.path.join(renders_dir, f)):
        grid_items.append({"src": f"/renders/{f}", "tag": f.replace(".png", "").replace("_", " ").upper()})

# Then add all other files
for f in all_files:
    if f not in priority_files and (f.endswith(".png") or f.endswith(".webp") or f.endswith(".jpg")):
        grid_items.append({"src": f"/renders/{f}", "tag": f.replace(".png", "").replace(".webp", "").replace("_", " ").upper()})

print(f"[+] Total Aggregated Master Stream Items: {len(grid_items)}")

html_file = "/home/ubuntu/arcane-princess-studio/index.html"
with open(html_file, "r") as f:
    content = f.read()

# Generate new index.html with single unbroken master grid
new_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Atelier Sovereign — The Master Princess Stream</title>
    <meta name="description" content="Atelier Sovereign: Unbroken single-stream visual gallery featuring all Arcane Princess renders.">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Cinzel:wght@500;700;900&family=Inter:wght@300;400;600;700&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg-void: #030408;
            --bg-glass: rgba(10, 14, 26, 0.85);
            --bg-card: rgba(16, 22, 36, 0.7);
            --accent-cyan: #00f0ff;
            --accent-magenta: #ff007f;
            --accent-gold: #ffb700;
            --border-glow: rgba(0, 240, 255, 0.25);
            --text-main: #f0f6fc;
            --text-muted: #8b949e;
            --font-serif: 'Cinzel', serif;
            --font-sans: 'Inter', sans-serif;
            --font-mono: 'JetBrains Mono', monospace;
        }}

        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            background-color: var(--bg-void);
            color: var(--text-main);
            font-family: var(--font-sans);
            min-height: 100vh;
            overflow-x: hidden;
            line-height: 1.6;
        }}

        .ambient-glow {{
            position: fixed; inset: 0; pointer-events: none; z-index: 0;
            background: 
                radial-gradient(circle at 15% 15%, rgba(0, 240, 255, 0.15), transparent 45%),
                radial-gradient(circle at 85% 85%, rgba(255, 0, 127, 0.12), transparent 45%),
                radial-gradient(circle at 50% 50%, rgba(138, 43, 226, 0.08), transparent 60%);
        }}

        header {{
            position: fixed; top: 0; left: 0; right: 0; z-index: 100;
            display: flex; justify-content: space-between; align-items: center;
            padding: 1rem 3rem;
            background: rgba(3, 4, 8, 0.92);
            backdrop-filter: blur(20px);
            border-bottom: 1px solid rgba(255, 255, 255, 0.08);
        }}
        .brand {{
            display: flex; align-items: center; gap: 0.8rem; text-decoration: none;
        }}
        .brand-logo {{
            width: 34px; height: 34px; border-radius: 50%;
            background: linear-gradient(135deg, var(--accent-cyan), var(--accent-magenta));
            box-shadow: 0 0 18px var(--accent-cyan);
        }}
        .brand-title {{
            font-family: var(--font-serif); font-size: 1.4rem; font-weight: 900;
            letter-spacing: 3px; color: #fff; text-transform: uppercase;
        }}
        .live-badge {{
            display: flex; align-items: center; gap: 0.6rem;
            padding: 0.4rem 1.2rem; border-radius: 20px;
            background: rgba(0, 240, 255, 0.1); border: 1px solid var(--accent-cyan);
            font-family: var(--font-mono); font-size: 0.82rem; color: var(--accent-cyan);
        }}
        .pulse-dot {{
            width: 9px; height: 9px; border-radius: 50%; background: var(--accent-cyan);
            box-shadow: 0 0 10px var(--accent-cyan);
            animation: pulse 1.5s infinite;
        }}
        @keyframes pulse {{ 0%, 100% {{ opacity: 1; transform: scale(1); }} 50% {{ opacity: 0.3; transform: scale(0.7); }} }}

        .main-viewport {{
            position: relative; z-index: 1; padding: 100px 3rem 100px;
            max-width: 2560px; margin: 0 auto;
        }}

        .masonry-grid {{
            display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 1.8rem;
        }}
        .grid-item {{
            position: relative; aspect-ratio: 4/5; border-radius: 14px; overflow: hidden;
            background: var(--bg-card); border: 1px solid rgba(255, 255, 255, 0.08);
            cursor: pointer; transition: all 0.35s cubic-bezier(0.4, 0, 0.2, 1);
        }}
        .grid-item:hover {{
            transform: translateY(-8px) scale(1.02);
            border-color: var(--accent-cyan);
            box-shadow: 0 15px 35px rgba(0, 240, 255, 0.3);
        }}
        .grid-item img {{
            width: 100%; height: 100%; object-fit: cover; display: block;
        }}
        .item-badge {{
            position: absolute; top: 1rem; right: 1rem;
            padding: 0.35rem 0.8rem; border-radius: 8px;
            background: rgba(3, 4, 8, 0.85); backdrop-filter: blur(8px);
            font-family: var(--font-mono); font-size: 0.72rem; color: var(--accent-cyan);
            border: 1px solid var(--border-glow);
        }}

        .lightbox {{
            position: fixed; inset: 0; z-index: 1000;
            background: rgba(3, 4, 8, 0.95); backdrop-filter: blur(25px);
            display: none; justify-content: center; align-items: center; padding: 2rem;
        }}
        .lightbox.active {{ display: flex; }}
        .lightbox-content {{
            position: relative; max-width: 90vw; max-height: 90vh; border-radius: 14px; overflow: hidden;
            border: 1px solid var(--accent-cyan); box-shadow: 0 0 60px rgba(0, 240, 255, 0.35);
        }}
        .lightbox-content img {{ max-width: 90vw; max-height: 85vh; display: block; object-fit: contain; }}
        .close-btn {{
            position: absolute; top: 1.5rem; right: 1.5rem;
            background: rgba(255, 255, 255, 0.1); border: 1px solid rgba(255, 255, 255, 0.2);
            color: #fff; font-size: 1.8rem; width: 48px; height: 48px; border-radius: 50%;
            cursor: pointer; display: flex; align-items: center; justify-content: center; transition: all 0.3s;
        }}
        .close-btn:hover {{ background: var(--accent-magenta); }}
    </style>
</head>
<body>

    <div class="ambient-glow"></div>

    <header>
        <a href="/" class="brand">
            <div class="brand-logo"></div>
            <span class="brand-title">Atelier Sovereign — All Renders Stream</span>
        </a>
        <div class="live-badge">
            <div class="pulse-dot"></div>
            <span>TOTAL AGGREGATED STREAM ({len(grid_items)} RENDERS)</span>
        </div>
    </header>

    <div class="main-viewport">
        <div class="masonry-grid" id="masonryGrid">
            <!-- Populated dynamically via JS -->
        </div>
    </div>

    <div class="lightbox" id="lightbox">
        <button class="close-btn" onclick="closeModal()">&times;</button>
        <div class="lightbox-content">
            <img id="modalImg" src="" alt="Enlarged View">
        </div>
    </div>

    <script>
        const ALL_ITEMS = {json.dumps(grid_items, indent=8)};

        function renderGrid() {{
            const grid = document.getElementById('masonryGrid');
            grid.innerHTML = ALL_ITEMS.map(item => `
                <div class="grid-item" onclick="openModal('${{item.src}}')">
                    <span class="item-badge">${{item.tag}}</span>
                    <img src="${{item.src}}" alt="${{item.tag}}" loading="lazy">
                </div>
            `).join('');
        }}

        function openModal(src) {{
            document.getElementById('modalImg').src = src;
            document.getElementById('lightbox').classList.add('active');
        }}
        function closeModal() {{
            document.getElementById('lightbox').classList.remove('active');
        }}

        document.addEventListener('DOMContentLoaded', renderGrid);
    </script>
</body>
</html>
"""

with open(html_file, "w") as f:
    f.write(new_html)

print("[+] Successfully built ALL-IN-ONE Master Stream Page!")
