import os
import glob
import json

renders_dir = "/home/ubuntu/arcane-princess-studio/renders"
all_files = sorted(os.listdir(renders_dir))

png_files = [f for f in all_files if f.endswith(".png")]
webp_files = [f for f in all_files if f.endswith(".webp")]

print(f"[+] Found {len(png_files)} PNG renders and {len(webp_files)} WebP turnaround frames.")

# Format as JavaScript array items
seed42_items = []
turnaround_items = []

for f in png_files:
    item = {"src": f"/renders/{f}", "tag": f.replace(".png", "").replace("_", " ").upper()}
    seed42_items.append(item)

for f in webp_files:
    item = {"src": f"/renders/{f}", "tag": f.replace(".webp", "").replace("_", " ").upper()}
    turnaround_items.append(item)

html_file = "/home/ubuntu/arcane-princess-studio/index.html"
with open(html_file, "r") as f:
    content = f.read()

# Replace COLLECTIONS object in JS
js_collections = f"""const COLLECTIONS = {{
            seed42: {json.dumps(seed42_items, indent=16)},
            turnaround: {json.dumps(turnaround_items, indent=16)}
        }};"""

import re
new_content = re.sub(r'const COLLECTIONS = \{[\s\S]*?\};', js_collections, content)

with open(html_file, "w") as f:
    f.write(new_content)

print(f"[+] Successfully updated {html_file} with ALL {len(seed42_items)} PNG renders and ALL {len(turnaround_items)} turnaround frames!")
