import os
import json
import re

html_file = "/home/ubuntu/arcane-princess-studio/index.html"

# List of 17 recovered blackwell lane renders
blackwell_files = [
    "blackwell-lane1-suzanne-v1.png",
    "blackwell-lane2-suzanne-v1.png",
    "blackwell-lane1-20260802t213246z.png",
    "blackwell-lane1-20260802t213309z.png",
    "blackwell-lane1-20260802t213330z.png",
    "blackwell-lane1-20260802t213351z.png",
    "blackwell-lane1-20260802t212413z.png",
    "blackwell-lane2-20260802t213246z.png",
    "blackwell-lane2-20260802t213307z.png",
    "blackwell-lane2-20260802t213328z.png",
    "blackwell-lane2-20260802t213348z.png",
    "blackwell-lane2-20260802t212413z.png",
    "blackwell-lane3-20260802t213246z.png",
    "blackwell-lane3-20260802t213307z.png",
    "blackwell-lane3-20260802t213327z.png",
    "blackwell-lane3-20260802t213346z.png",
    "blackwell-lane3-20260802t212606z.png"
]

blackwell_items = [{"src": f"/renders/{f}", "tag": f"BLACKWELL RECOVERY • {f.replace('.png', '').upper()}"} for f in blackwell_files]

with open(html_file, "r") as f:
    content = f.read()

# Update COLLECTIONS in JS
new_js_tab = f"""        const COLLECTIONS = {{
            seed42: [
                {{ src: "/renders/seed42_throne_room.png", tag: "SEED 42 • CRYSTAL THRONE" }},
                {{ src: "/renders/seed42_masterpiece_01.png", tag: "SEED 42 • MASTERPIECE #01" }},
                {{ src: "/renders/seed42_sapphire_gaze.png", tag: "SEED 42 • SAPPHIRE GAZE" }},
                {{ src: "/renders/seed42_crimson_cape.png", tag: "SEED 42 • CRIMSON CAPE" }},
                {{ src: "/renders/seed42_stained_glass.png", tag: "SEED 42 • STAINED GLASS" }},
                {{ src: "/renders/seed42_balcony_night.png", tag: "SEED 42 • BALCONY NIGHT" }},
                {{ src: "/renders/seed42_emerald_haven.png", tag: "SEED 42 • EMERALD HAVEN" }},
                {{ src: "/renders/seed42_gold_jewelry.png", tag: "SEED 42 • GOLD FILIGREE" }}
            ],
            blackwell: {json.dumps(blackwell_items, indent=16)},
            stream: [
                {{ src: "/renders/sovereign_masterpiece_001.png", tag: "5.8s RENDER • #001" }},
                {{ src: "/renders/sovereign_masterpiece_002.png", tag: "5.8s RENDER • #002" }},
                {{ src: "/renders/sovereign_masterpiece_003.png", tag: "5.8s RENDER • #003" }},
                {{ src: "/renders/sovereign_masterpiece_004.png", tag: "5.8s RENDER • #004" }}
            ],
            turnaround: [
                {{ src: "/arcane_princess_haute_couture.png", tag: "VAULT • HAUTE COUTURE" }},
                {{ src: "/arcane_rose_princess_frame_001.png", tag: "VAULT • ROSE KEYFRAME" }},
                {{ src: "/renders/gpu_5_frame_1290.png", tag: "360° TURNAROUND #1290" }},
                {{ src: "/renders/gpu_6_frame_1332.png", tag: "360° TURNAROUND #1332" }},
                {{ src: "/renders/gpu_7_frame_1319.png", tag: "360° TURNAROUND #1319" }}
            ]
        }};"""

def replace_collections(text, new_block):
    start = text.find("const COLLECTIONS = {")
    end = text.find("};", start) + 2
    if start != -1 and end != -1:
        return text[:start] + new_block + text[end:]
    return text

content = replace_collections(content, new_js_tab)

# Add Blackwell Tab button in HTML
tab_buttons = """        <div class="tab-bar">
            <button class="tab-btn active" onclick="switchCollection('seed42')">🌟 Seed 42 Masterpieces</button>
            <button class="tab-btn" onclick="switchCollection('blackwell')">🏛️ Blackwell Recovery Vault (17)</button>
            <button class="tab-btn" onclick="switchCollection('stream')">⚡ Continuous Stream (5.8s)</button>
            <button class="tab-btn" onclick="switchCollection('turnaround')">👑 3,180 Princess Vault</button>
        </div>"""

content = re.sub(r'<div class="tab-bar">[\s\S]*?</div>', tab_buttons, content)

with open(html_file, "w") as f:
    f.write(content)

print(f"[+] Successfully added Blackwell Recovery Vault tab to {html_file}!")
