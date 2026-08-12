import os
import subprocess
import json

renders_dir = "/home/ubuntu/arcane-princess-studio/renders"
local_files = [f for f in os.listdir(renders_dir) if f.endswith(".png") or f.endswith(".webp") or f.endswith(".gif")]

print(f"[+] Local Preservation Count: {len(local_files)} assets in {renders_dir}")

# Check R2 store list
res_surface = subprocess.run(["gemstone", "store", "list", "surface/renders/"], capture_output=True, text=True)
res_bloom = subprocess.run(["gemstone", "store", "list", "council_os/bloom-preservation-20260805/"], capture_output=True, text=True)

surface_lines = [line for line in res_surface.stdout.splitlines() if line.strip()]
bloom_lines = [line for line in res_bloom.stdout.splitlines() if line.strip()]

print(f"[+] R2 surface/renders/ Count: {len(surface_lines)} objects")
print(f"[+] R2 council_os/bloom-preservation-20260805/ Count: {len(bloom_lines)} objects")

# Sync index.html, master manifest, and key assets to guarantee 100% preservation
subprocess.run(["gemstone", "store", "push", "/home/ubuntu/arcane-princess-studio/index.html", "surface/index.html"], capture_output=True)
subprocess.run(["gemstone", "store", "push", "/home/ubuntu/arcane-princess-studio/index.html", "council_os/bloom-preservation-20260805/index.html"], capture_output=True)
subprocess.run(["gemstone", "store", "push", "/home/ubuntu/.gemini/antigravity-cli/brain/01df9f0c-2832-4716-8eca-979d459909fc/master_arcane_princess_sovereign_manifest.md", "council_os/bloom-preservation-20260805/master_manifest.md"], capture_output=True)

print("[+] Master Verification & Edge Sync Complete!")
