#!/usr/bin/env python3
"""Socket-first readiness probe for the Motion Atlas runtime."""
import argparse
import json
import os
import socket
import sys
import time
from pathlib import Path


def exchange(family, address, payload, timeout=3):
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    sock.connect(address)
    sock.sendall((json.dumps(payload, separators=(",", ":")) + "\n").encode())
    return sock, json.loads(sock.makefile().readline())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--model-dir", default=os.environ.get("FLUX_MODEL_DIR", "/models/flux1"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    root = Path(args.root)
    required = [
        "model_index.json", "text_encoder/model.safetensors",
        "transformer/diffusion_pytorch_model-00001-of-00003.safetensors",
        "transformer/diffusion_pytorch_model-00002-of-00003.safetensors",
        "transformer/diffusion_pytorch_model-00003-of-00003.safetensors",
        "vae/diffusion_pytorch_model.safetensors",
    ]
    checks = {}
    checks["model"] = {"ok": all((Path(args.model_dir) / rel).is_file() for rel in required), "path": args.model_dir}
    try:
        sock, row = exchange(socket.AF_UNIX, str(root / ".fluxd" / "flux.sock"), {"op": "ping"})
        sock.close()
        checks["worker"] = {"ok": bool(row.get("ok")), "loaded": bool(row.get("loaded")), "device": row.get("device")}
    except Exception as exc:
        checks["worker"] = {"ok": False, "error": str(exc)}
    piper_path = os.environ.get("PIPER_SOCKET", "/tmp/piper.sock")
    try:
        sock, row = exchange(socket.AF_UNIX, piper_path, {"type": "health"})
        sock.close()
        checks["piper"] = {"ok": bool(row.get("ok")), "socket": piper_path, "status": row.get("status")}
    except Exception as exc:
        checks["piper"] = {"ok": False, "socket": piper_path, "error": str(exc)}
    try:
        sock, row = exchange(socket.AF_INET, ("127.0.0.1", 9999), {"type": "health"})
        sock.close()
        checks["nexus"] = {"ok": bool(row.get("ok")), "status": row.get("status")}
    except Exception as exc:
        checks["nexus"] = {"ok": False, "error": str(exc)}
    try:
        subscriber, subscribed = exchange(socket.AF_UNIX, piper_path, {"type": "asset.subscribe", "consumer": "motion-probe"})
        event_id = f"probe-{time.time_ns()}"
        publisher, receipt = exchange(socket.AF_UNIX, piper_path, {
            "type": "asset.publish", "job_id": "motion-probe",
            "asset": {"id": event_id, "access_url": "/outputs/probe.png"},
        })
        publisher.close()
        event = json.loads(subscriber.makefile().readline())
        subscriber.close()
        checks["flow"] = {
            "ok": bool(subscribed.get("ok")) and bool(receipt.get("ok")) and event.get("asset_id") == event_id,
            "path": "Piper publish → Unix subscriber",
        }
    except Exception as exc:
        checks["flow"] = {"ok": False, "error": str(exc)}
    result = {"ok": all(row.get("ok") for row in checks.values()), "checks": checks}
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        for name, row in checks.items():
            mark = "PASS" if row.get("ok") else "FAIL"
            detail = row.get("device") or row.get("status") or row.get("path") or row.get("error") or ""
            print(f"{mark:4} {name:8} {detail}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
