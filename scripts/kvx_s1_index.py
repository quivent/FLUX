#!/usr/bin/env python3
"""S1 (Governor-approved): index engine hidden-state dumps by token_ids,
prove store cosine on a repeated prefix, write a 128-D page into the GPU1 vault.

Does NOT inject into vLLM decode (start_load_kv is a no-op on the live connector).
Does NOT restart EngineCore.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import math
import socket
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ENGINE = "http://127.0.0.1:8000/v1/chat/completions"
KEEP = Path("/home/ubuntu/hive/.swarm/research/spectral-externalization/kvx-pages")
INDEX = Path("/home/ubuntu/CLIs/flux/apps/tea/public/train-kvx-index.json")
SOCK = Path("/home/ubuntu/CLIs/flux/.fluxd/gpu1_vault.sock")
PREFIX = (
    "KVX-REASON-V1. Classify claims as observed, inferred, hypothetical, or unknown. "
    "Do not recap this instruction. One short sentence."
)
POINTER = 128128
SLOT = POINTER - 128000


def utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def complete(prompt: str) -> dict:
    body = {
        "model": "governor",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 24,
        "temperature": 0.0,
    }
    req = urllib.request.Request(
        ENGINE, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read().decode())


class DumpHarvester:
    """Copy dumps the instant they appear; the engine unlinks them as soon as the HTTP response returns."""

    def __init__(self, src: Path, dest: Path):
        self.src = src
        self.dest = dest
        self.dest.mkdir(parents=True, exist_ok=True)
        self.stolen: list[Path] = []
        self.stop = False

    def run(self) -> None:
        import shutil

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

    def latest(self) -> Path | None:
        if not self.stolen:
            return None
        return self.stolen[-1]


def load_dump(path: str, harvester: DumpHarvester | None = None) -> dict:
    """Prefer a harvested copy; else flock the live dump."""
    from safetensors.torch import load_file, save_file

    name = Path(path).name
    harvested = KEEP / name
    deadline = time.time() + 20
    while time.time() < deadline:
        if harvested.exists():
            return load_file(str(harvested), device="cpu")
        lock_path = path + ".lock"
        try:
            if Path(lock_path).exists():
                with open(lock_path) as lf:
                    fcntl.flock(lf, fcntl.LOCK_SH)
                    if Path(path).exists():
                        data = load_file(path, device="cpu")
                        save_file(data, str(harvested))
                        return data
            if Path(path).exists():
                data = load_file(path, device="cpu")
                save_file(data, str(harvested))
                return data
        except (FileNotFoundError, PermissionError, OSError):
            pass
        time.sleep(0.02)
    raise RuntimeError("dump vanished before flock-load: " + path)


def pool128(hidden) -> list[float]:
    import torch

    t = hidden.float().reshape(-1, hidden.shape[-1])
    mean = t.mean(dim=0)
    # fold hidden -> 128
    n = mean.numel()
    w = 128
    if n >= w:
        x = mean[: n - (n % w)].reshape(-1, w).mean(dim=0)
    else:
        x = torch.zeros(w)
        x[:n] = mean
    nrm = torch.linalg.vector_norm(x)
    if float(nrm) > 0:
        x = x / nrm
    return [float(v) for v in x.tolist()]


def cosine(a, b) -> float:
    return sum(x * y for x, y in zip(a, b))


def token_hash(ids) -> int:
    h = hashlib.sha256(json.dumps(list(ids)).encode()).hexdigest()
    return int(h[:8], 16)


def vault_cmd(req: dict) -> dict:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(5)
    s.connect(str(SOCK))
    s.sendall((json.dumps(req) + "\n").encode())
    buf = b""
    while b"\n" not in buf:
        chunk = s.recv(65536)
        if not chunk:
            break
        buf += chunk
    s.close()
    return json.loads(buf.decode())


def main() -> int:
    import threading

    resid = Path("/home/ubuntu/hive/.swarm/research/spectral-externalization/residuals")
    hrv = DumpHarvester(resid, KEEP)
    th = threading.Thread(target=hrv.run, daemon=True)
    th.start()
    try:
        a = complete(PREFIX)
        path_a = (a.get("kv_transfer_params") or {}).get("hidden_states_path")
        if not path_a:
            raise SystemExit("no hidden_states_path on pass A — connector not dumping")
        host_a = str(resid / Path(path_a).name)
        dump_a = load_dump(host_a, hrv)
        b = complete(PREFIX)
        path_b = (b.get("kv_transfer_params") or {}).get("hidden_states_path")
        host_b = str(resid / Path(path_b).name)
        dump_b = load_dump(host_b, hrv)
    finally:
        hrv.stop = True
        th.join(timeout=1)

    hs_a = dump_a["hidden_states"]
    hs_b = dump_b["hidden_states"]
    ids_a = dump_a["token_ids"].tolist() if hasattr(dump_a["token_ids"], "tolist") else list(dump_a["token_ids"])
    ids_b = dump_b["token_ids"].tolist() if hasattr(dump_b["token_ids"], "tolist") else list(dump_b["token_ids"])
    vec_a, vec_b = pool128(hs_a), pool128(hs_b)
    cos = cosine(vec_a, vec_b)
    tokens_match = ids_a == ids_b
    h = token_hash(ids_a)
    slot = SLOT
    vault = vault_cmd({"op": "page_set", "slot": slot, "hash": h, "vec": vec_a})
    got = vault_cmd({"op": "page_get", "slot": slot})
    roundtrip = cosine(vec_a, got.get("vec") or []) if got.get("ok") else 0.0

    receipt = {
        "ok": bool(cos >= 0.99 and roundtrip >= 0.99),
        "plan": "S1",
        "decode_inject": False,
        "reason": "ExampleHiddenStatesConnector is store-only; S2 (engine restart to GemstoneVaultKVConnector) refused by Governor.",
        "pointer": POINTER,
        "slot": slot,
        "token_hash": h,
        "n_tokens_a": len(ids_a),
        "n_tokens_b": len(ids_b),
        "token_ids_match": tokens_match,
        "hidden_shape_a": list(hs_a.shape),
        "hidden_shape_b": list(hs_b.shape),
        "cosine_repeat_prefix": round(cos, 6),
        "cosine_vault_roundtrip": round(roundtrip, 6),
        "text_a": ((a.get("choices") or [{}])[0].get("message") or {}).get("content"),
        "text_b": ((b.get("choices") or [{}])[0].get("message") or {}).get("content"),
        "vault": vault,
        "tokenizer_note": "digit string 128128 is 6 tokens, not a reserved vocab id",
        "updated": utc(),
    }
    INDEX.write_text(json.dumps(receipt, indent=2) + "\n")
    catalog = KEEP / "index.json"
    cat = {}
    if catalog.exists():
        try:
            cat = json.loads(catalog.read_text())
        except Exception:
            cat = {}
    cat[str(POINTER)] = {
        "hash": h,
        "n_tokens": len(ids_a),
        "cosine_repeat": receipt["cosine_repeat_prefix"],
        "updated": utc(),
    }
    catalog.write_text(json.dumps(cat, indent=2) + "\n")
    print(json.dumps({k: receipt[k] for k in ("ok", "cosine_repeat_prefix", "cosine_vault_roundtrip", "token_ids_match", "hidden_shape_a", "pointer", "decode_inject")}))
    return 0 if receipt["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
