import os
import subprocess
import json

vault_dir = "/home/ubuntu/bloom-gallery/renders"
gallery_dir = "/home/ubuntu/arcane-princess-studio/renders"

os.makedirs(gallery_dir, exist_ok=True)

vault_files = os.listdir(vault_dir) if os.path.exists(vault_dir) else []
print(f"[+] Total Vault Assets Found: {len(vault_files)}")

copied = 0
for f in vault_files:
    src_path = os.path.join(vault_dir, f)
    dst_path = os.path.join(gallery_dir, f)
    if not os.path.exists(dst_path) and os.path.isfile(src_path):
        subprocess.run(["cp", src_path, dst_path])
        copied += 1

print(f"[+] Copied {copied} missing vault assets to master gallery library.")

total_gallery_assets = os.listdir(gallery_dir)
print(f"[+] Total Master Gallery Library Count: {len(total_gallery_assets)} assets.")

# Push index.html to R2 and Cherry control plane
subprocess.run(["gemstone", "store", "push", "/home/ubuntu/arcane-princess-studio/index.html", "surface/index.html"], capture_output=True)
subprocess.run(["gemstone", "store", "push", "/home/ubuntu/arcane-princess-studio/index.html", "council_os/bloom-preservation-20260805/index.html"], capture_output=True)

print("[+] Vault Integration & Cherry Edge Sync Complete!")
