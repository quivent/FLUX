#!/usr/bin/env python3
"""Eight-hour FLUX run sentinel with conservative automatic repair."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any

STATUS_PATH = Path("/run/flux-sentinel/status.json")
LEDGER_PATH = Path("/var/log/flux-run-sentinel.jsonl")
SERVICES = ("flux-server.service", "flux-worker.service", "nexus-runtime.service", "piper-runtime.service")


def emit(level: str, check: str, message: str, **details: Any) -> dict[str, Any]:
    row = {"ts": time.time(), "level": level, "check": check, "message": message, **details}
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")
    return row


def run(*args: str, timeout: int = 12) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, capture_output=True, timeout=timeout, check=False)


def systemd_check(name: str, repair: bool) -> dict[str, Any]:
    active = run("systemctl", "is-active", name).stdout.strip() == "active"
    repaired = False
    if not active and repair:
        repaired = run("systemctl", "restart", name, timeout=30).returncode == 0
        active = run("systemctl", "is-active", name).stdout.strip() == "active"
        emit("repaired" if active else "critical", name, "service restarted" if active else "service restart failed")
    return {"ok": active, "repaired": repaired}


def tcp_json(address: tuple[str, int], payload: dict[str, Any]) -> dict[str, Any]:
    with socket.create_connection(address, timeout=3) as conn:
        conn.settimeout(3)
        conn.sendall((json.dumps(payload) + "\n").encode())
        return json.loads(conn.makefile().readline())


def unix_json(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(3)
        conn.connect(path)
        conn.sendall((json.dumps(payload) + "\n").encode())
        return json.loads(conn.makefile().readline())


def endpoint_check() -> dict[str, Any]:
    try:
        with urllib.request.urlopen("http://127.0.0.1:7861/api/health", timeout=4) as response:
            body = json.load(response)
        return {"ok": response.status == 200 and bool(body.get("ok")), "worker_running": body.get("worker_running")}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def runtime_check(kind: str) -> dict[str, Any]:
    try:
        body = tcp_json(("127.0.0.1", 9999), {"type": "health"}) if kind == "nexus" else unix_json("/tmp/piper.sock", {"type": "health"})
        return {"ok": bool(body.get("ok")), "status": body.get("status")}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def gpu_check() -> dict[str, Any]:
    result = run("nvidia-smi", "--query-gpu=memory.used,memory.total,utilization.gpu", "--format=csv,noheader,nounits")
    values = [part.strip() for part in result.stdout.strip().split(",")]
    if result.returncode or len(values) != 3:
        return {"ok": False, "error": result.stderr.strip() or "nvidia-smi returned no GPU"}
    used, total, utilization = map(int, values)
    vllm = run("docker", "ps", "--filter", "ancestor=vllm/vllm-openai:latest", "--format", "{{.Names}}").stdout.split()
    return {"ok": not vllm, "memory_used_mib": used, "memory_total_mib": total, "utilization_percent": utilization, "unexpected_vllm": vllm}


def disk_check(path: str = "/root") -> dict[str, Any]:
    usage = shutil.disk_usage(path)
    percent = round(100 * usage.used / usage.total, 1)
    return {"ok": percent < 90, "used_percent": percent, "free_gib": round(usage.free / 2**30, 1)}


def snapshot(repair: bool) -> dict[str, Any]:
    checks: dict[str, Any] = {"services": {name: systemd_check(name, repair) for name in SERVICES}}
    checks["http"] = endpoint_check()
    checks["nexus"] = runtime_check("nexus")
    checks["piper"] = runtime_check("piper")
    checks["gpu"] = gpu_check()
    checks["disk"] = disk_check()
    for runtime, service in (("nexus", "nexus-runtime.service"), ("piper", "piper-runtime.service")):
        if not checks[runtime]["ok"] and repair:
            run("systemctl", "restart", service, timeout=30)
            time.sleep(1)
            checks[runtime] = runtime_check(runtime)
            emit("repaired" if checks[runtime]["ok"] else "critical", runtime, "runtime recovered" if checks[runtime]["ok"] else "runtime unavailable after restart")
    if not checks["http"]["ok"] and repair:
        run("systemctl", "restart", "flux-server.service", timeout=30)
        time.sleep(1)
        checks["http"] = endpoint_check()
        emit("repaired" if checks["http"]["ok"] else "critical", "http", "FLUX API recovered" if checks["http"]["ok"] else "FLUX API unavailable after restart")
    if checks["gpu"].get("unexpected_vllm"):
        emit("critical", "gpu", "unexpected vLLM container detected; left untouched", containers=checks["gpu"]["unexpected_vllm"])
    if not checks["disk"]["ok"]:
        emit("critical", "disk", "disk usage crossed 90%; no automatic deletion", **checks["disk"])
    ok = all(row.get("ok") for row in checks["services"].values()) and all(checks[name].get("ok") for name in ("http", "nexus", "piper", "gpu", "disk"))
    result = {"ok": ok, "updated_at": time.time(), "checks": checks}
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=float, default=float(os.environ.get("FLUX_SENTINEL_HOURS", "8")))
    parser.add_argument("--interval", type=int, default=15)
    parser.add_argument("--no-repair", action="store_true")
    args = parser.parse_args()
    deadline = time.monotonic() + args.hours * 3600 if args.hours > 0 else None
    emit("info", "sentinel", "run watch started", hours=args.hours, interval=args.interval)
    while deadline is None or time.monotonic() < deadline:
        snapshot(not args.no_repair)
        time.sleep(max(5, args.interval))
    emit("info", "sentinel", "run watch completed", hours=args.hours)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
