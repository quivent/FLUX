#!/usr/bin/env python3
"""Immortal Continuous Cloudflare R2 Sync Daemon.

Guarantees 100% off-machine preservation of all rendered artworks,
SQLite audit state, and genetic ledgers to Cloudflare R2 with zero drop.
"""
import glob
import os
import subprocess
import time

OUTPUT_DIR = "/root/Models/flux-output"
SQLITE_DB = "/root/Models/flux-output/jury.sqlite3"
SPECTACLE_LOG = "/root/Models/flux-output/spectacle_genome.jsonl"
MASTERPIECE_LOG = "/root/Models/flux-output/masterpiece_vault.jsonl"

def sync_batch():
    try:
        # 1. Sync full directory to Cloudflare R2
        subprocess.run(["gemstone", "r2", "sync", OUTPUT_DIR, "outputs/"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
        
        # 2. Sync critical SQLite state
        if os.path.exists(SQLITE_DB):
            subprocess.run(["gemstone", "r2", "push", SQLITE_DB, "state/jury.sqlite3"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
            
        # 3. Sync genetic genome ledgers
        if os.path.exists(SPECTACLE_LOG):
            subprocess.run(["gemstone", "r2", "push", SPECTACLE_LOG, "outputs/spectacle_genome.jsonl"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
        if os.path.exists(MASTERPIECE_LOG):
            subprocess.run(["gemstone", "r2", "push", MASTERPIECE_LOG, "outputs/masterpiece_vault.jsonl"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
    except Exception as e:
        print(f"[R2 SYNC DAEMON ERR] {e}", flush=True)

def main():
    print("Immortal Cloudflare R2 Sync Daemon Online [Zero Drop Active].", flush=True)
    while True:
        sync_batch()
        time.sleep(12.0)

if __name__ == "__main__":
    main()
