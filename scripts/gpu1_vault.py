#!/usr/bin/env python3
"""Bounded cudaMalloc vault on GPU 1. Do not OOM the serving Governor.

Governor next (2026-09-04): implement actual cudaMalloc for the vault.
GPU 1 already holds Governor ~59 GiB plus a prometheus sidecar ~20 GiB.
Free HBM is measured at start; we take at most 768 MiB and abort if free < 2 GiB.
"""
from __future__ import annotations

import json
import os
import select
import signal
import socket
import struct
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("CUDA_VISIBLE_DEVICES", "1")

RECEIPT = Path("/home/ubuntu/CLIs/flux/apps/tea/public/train-vault.json")
FLUXD = Path("/home/ubuntu/CLIs/flux/.fluxd/gpu1_vault.json")
PIDF = Path("/home/ubuntu/CLIs/flux/.fluxd/gpu1_vault.pid")
SOCK = Path("/home/ubuntu/CLIs/flux/.fluxd/gpu1_vault.sock")
BYTES = int(os.environ.get("VAULT_BYTES", str(768 * 1024 * 1024)))
PAGE_WIDTH = 128  # float16s per page (spectral projection)
STOP = False


def utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def smi() -> dict:
    out = subprocess.check_output(
        [
            "nvidia-smi",
            "-i",
            "1",
            "--query-gpu=memory.used,memory.free,memory.total,utilization.gpu",
            "--format=csv,noheader,nounits",
        ],
        text=True,
    ).strip()
    used, free, total, util = [float(x.strip()) for x in out.split(",")]
    return {"used_mib": used, "free_mib": free, "total_mib": total, "util": util}


def write_receipt(body: dict) -> None:
    RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    FLUXD.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(body, indent=2) + "\n"
    RECEIPT.write_text(text)
    FLUXD.write_text(text)


def handle(sig, frame):
    global STOP
    STOP = True


def main() -> int:
    signal.signal(signal.SIGTERM, handle)
    signal.signal(signal.SIGINT, handle)
    before = smi()
    if before["free_mib"] < 2048:
        write_receipt(
            {
                "ok": False,
                "cudaMalloc": False,
                "error": "free HBM < 2 GiB; refuse vault",
                "gpu": 1,
                "before": before,
                "updated": utc(),
            }
        )
        return 2
    import torch

    torch.cuda.set_device(0)
    n = BYTES // 2  # float16
    tensor = torch.empty(n, dtype=torch.float16, device="cuda")
    tensor.zero_()
    # Index: first 4 MiB as uint32 pointer map 128000-128512 → page offsets
    index_n = min(512, n // 2)
    ptrs = torch.arange(128000, 128000 + index_n, device="cuda", dtype=torch.int32)
    tensor.view(torch.int32)[:index_n] = ptrs
    torch.cuda.synchronize()
    after = smi()
    receipt = {
        "ok": True,
        "cudaMalloc": True,
        "gpu": 1,
        "pid": os.getpid(),
        "bytes": BYTES,
        "dtype": "float16",
        "layout": {
            "index_entries": int(index_n),
            "index_span": "128000+",
            "pages_bytes": BYTES - index_n * 4,
        },
        "before": before,
        "after": after,
        "delta_used_mib": after["used_mib"] - before["used_mib"],
        "note": "Bounded vault beside serving Governor. Not a second 31B. Not SGD.",
        "updated": utc(),
    }
    write_receipt(receipt)
    PIDF.write_text(str(os.getpid()))
    pages_base = (index_n * 4 + 255) // 256 * 128  # float16 offset, 256-byte align
    receipt["layout"]["pages_base_f16"] = pages_base
    receipt["layout"]["page_width"] = PAGE_WIDTH
    receipt["socket"] = str(SOCK)
    if SOCK.exists():
        SOCK.unlink()
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(SOCK))
    srv.listen(8)
    srv.setblocking(False)
    os.chmod(SOCK, 0o666)
    last_smi = time.time()
    while not STOP:
        r, _, _ = select.select([srv], [], [], 1.0)
        if r:
            conn, _ = srv.accept()
            try:
                raw = b""
                conn.settimeout(2)
                while b"\n" not in raw:
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    raw += chunk
                req = json.loads(raw.decode() or "{}")
                op = req.get("op")
                if op == "status":
                    resp = {"ok": True, "pid": os.getpid(), "cudaMalloc": True, "layout": receipt["layout"]}
                elif op == "page_set":
                    slot = int(req["slot"])
                    vec = req.get("vec") or []
                    if slot < 0 or slot >= index_n or len(vec) != PAGE_WIDTH:
                        resp = {"ok": False, "error": "bad slot or vec"}
                    else:
                        h = int(req.get("hash") or 0) & 0xFFFFFFFF
                        tensor.view(torch.int32)[slot] = h
                        start = pages_base + slot * PAGE_WIDTH
                        t = torch.tensor(vec, dtype=torch.float16, device="cuda")
                        tensor[start : start + PAGE_WIDTH] = t
                        torch.cuda.synchronize()
                        resp = {"ok": True, "slot": slot, "hash": h}
                elif op == "page_get":
                    slot = int(req["slot"])
                    start = pages_base + slot * PAGE_WIDTH
                    h = int(tensor.view(torch.int32)[slot].item()) & 0xFFFFFFFF
                    vec = tensor[start : start + PAGE_WIDTH].float().cpu().tolist()
                    resp = {"ok": True, "slot": slot, "hash": h, "vec": vec}
                else:
                    resp = {"ok": False, "error": "unknown op"}
                conn.sendall((json.dumps(resp) + "\n").encode())
            except Exception as e:
                try:
                    conn.sendall((json.dumps({"ok": False, "error": str(e)}) + "\n").encode())
                except Exception:
                    pass
            finally:
                conn.close()
        if time.time() - last_smi > 15:
            receipt["after"] = smi()
            receipt["updated"] = utc()
            receipt["alive"] = True
            write_receipt(receipt)
            last_smi = time.time()
    srv.close()
    try:
        SOCK.unlink()
    except FileNotFoundError:
        pass
    del tensor
    torch.cuda.empty_cache()
    write_receipt({**receipt, "ok": True, "cudaMalloc": False, "released": True, "updated": utc()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
