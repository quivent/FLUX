#!/usr/bin/env python3
"""S2 inject proof. Residuals stay on ExampleHiddenStatesConnector.

phase bake: greedy prefix, harvest dump, require vault attention files.
phase verify: after engine restart (empty GPU prefix cache, vault on disk),
              same greedy prefix must raise external_prefix_cache_hits and
              match bake text. Dump must still appear.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ENGINE = "http://127.0.0.1:8000"
METRICS = ENGINE + "/metrics"
CHAT = ENGINE + "/v1/chat/completions"
KEEP = Path("/tmp/kvx-s2")
RESIDUALS = Path("/home/ubuntu/hive/.swarm/research/spectral-externalization/residuals")
VAULT = Path("/home/ubuntu/hive/.swarm/research/spectral-externalization/kv-vault")
RECEIPT = Path("/home/ubuntu/CLIs/flux/apps/tea/public/train-kvx-inject.json")
PREFIX = (
    "KVX-INJECT-V1b. Classify claims as observed, inferred, hypothetical, or unknown. "
    "Do not recap this instruction. One short sentence."
)


def utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def metric(name: str) -> float:
    text = urllib.request.urlopen(METRICS, timeout=15).read().decode()
    for line in text.splitlines():
        if line.startswith("#"):
            continue
        if line.startswith(name + "{") or line.startswith(name + " "):
            return float(line.rsplit(" ", 1)[-1])
    return -1.0


def complete(prompt: str) -> dict:
    body = {
        "model": "governor",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 24,
        "temperature": 0.0,
    }
    req = urllib.request.Request(
        CHAT, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read().decode())


class DumpHarvester:
    def __init__(self, src: Path, dest: Path):
        self.src = src
        self.dest = dest
        self.dest.mkdir(parents=True, exist_ok=True)
        self.stolen: list[Path] = []
        self.stop = False

    def run(self) -> None:
        while not self.stop:
            try:
                for p in self.src.glob("*.safetensors"):
                    d = self.dest / p.name
                    if d.exists():
                        continue
                    try:
                        shutil.copy2(p, d)
                        self.stolen.append(d)
                    except (FileNotFoundError, PermissionError, OSError):
                        pass
            except Exception:
                pass
            time.sleep(0.01)


def vault_files() -> list[Path]:
    if not VAULT.exists():
        return []
    return [p for p in VAULT.rglob("*.safetensors") if "cache_only" not in p.name]


def write_receipt(data: dict) -> None:
    RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    RECEIPT.write_text(json.dumps(data, indent=2) + "\n")
    pub = Path("/home/ubuntu/CLIs/flux/apps/tea/src/train-kvx-inject.json")
    try:
        pub.write_text(RECEIPT.read_text())
    except OSError:
        pass


def phase_bake() -> int:
    hits0 = metric("vllm:external_prefix_cache_hits_total")
    h = DumpHarvester(RESIDUALS, KEEP / "s2-bake")
    t = threading.Thread(target=h.run, daemon=True)
    t.start()
    try:
        out = complete(PREFIX)
    finally:
        time.sleep(0.4)
        h.stop = True
        t.join(timeout=2)
    text = out["choices"][0]["message"]["content"]
    dumped = list(h.stolen)
    files = vault_files()
    hits1 = metric("vllm:external_prefix_cache_hits_total")
    receipt = {
        "ok": bool(files) and bool(dumped),
        "phase": "bake",
        "decode_inject": False,
        "text": text,
        "dump_files": [str(p) for p in dumped],
        "n_dump": len(dumped),
        "n_vault_attention": len(files),
        "vault_has_cache_only": any(
            "cache_only" in p.name for p in VAULT.rglob("*.safetensors")
        )
        if VAULT.exists()
        else False,
        "external_hits_before": hits0,
        "external_hits_after": hits1,
        "updated": utc(),
    }
    write_receipt(receipt)
    print(json.dumps(receipt, indent=2))
    return 0 if receipt["ok"] else 1


def phase_verify() -> int:
    bake = json.loads(RECEIPT.read_text()) if RECEIPT.exists() else {}
    hits0 = metric("vllm:external_prefix_cache_hits_total")
    h = DumpHarvester(RESIDUALS, KEEP / "s2-verify")
    t = threading.Thread(target=h.run, daemon=True)
    t.start()
    try:
        out = complete(PREFIX)
    finally:
        time.sleep(0.4)
        h.stop = True
        t.join(timeout=2)
    text = out["choices"][0]["message"]["content"]
    dumped = list(h.stolen)
    hits1 = metric("vllm:external_prefix_cache_hits_total")
    greedy_match = text == bake.get("text")
    hits_up = hits1 > hits0
    receipt = {
        "ok": bool(hits_up and greedy_match and dumped),
        "phase": "verify",
        "decode_inject": bool(hits_up and greedy_match),
        "text": text,
        "text_bake": bake.get("text"),
        "greedy_text_match": greedy_match,
        "external_hits_before": hits0,
        "external_hits_after": hits1,
        "external_prefix_cache_hits": hits_up,
        "n_dump": len(dumped),
        "dump_still_on": bool(dumped),
        "n_vault_attention": len(vault_files()),
        "must_not_store_only": hits_up,
        "updated": utc(),
    }
    write_receipt(receipt)
    print(json.dumps(receipt, indent=2))
    if not hits_up:
        print("FAIL: external_prefix_cache_hits did not rise (still cold prefill or local prefix only)")
        return 2
    if not greedy_match:
        print("FAIL: greedy text mismatch — injected KV is silent-wrong")
        return 3
    if not dumped:
        print("FAIL: residual dump missing after inject restack")
        return 4
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("phase", choices=["bake", "verify"])
    args = p.parse_args()
    os.makedirs(KEEP, exist_ok=True)
    if args.phase == "bake":
        return phase_bake()
    return phase_verify()


if __name__ == "__main__":
    raise SystemExit(main())
