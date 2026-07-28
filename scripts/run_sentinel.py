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
FAILURES: dict[str, int] = {}
LAST_AGENT_AT = 0.0
LAST_PROGRESS: dict[str, tuple[int, float]] = {}


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
    failures = note_result(name, active)
    if not active and repair and failures >= 3:
        repaired = run("systemctl", "restart", name, timeout=30).returncode == 0
        active = run("systemctl", "is-active", name).stdout.strip() == "active"
        emit("repaired" if active else "critical", name, "service restarted" if active else "service restart failed")
        note_result(name, active)
    return {"ok": active, "repaired": repaired}


def note_result(name: str, ok: bool) -> int:
    FAILURES[name] = 0 if ok else FAILURES.get(name, 0) + 1
    return FAILURES[name]


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


def progress_check() -> dict[str, Any]:
    try:
        with urllib.request.urlopen("http://127.0.0.1:7861/api/jobs", timeout=4) as response:
            jobs = json.load(response).get("jobs") or []
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    now = time.time()
    running = [job for job in jobs if str(job.get("status") or "").lower() == "running"]
    stalled: list[dict[str, Any]] = []
    active_ids = set()
    for job in running:
        job_id = str(job.get("id") or "unknown")
        active_ids.add(job_id)
        step = int(job.get("atlas_done") or job.get("step") or job.get("images_done") or 0)
        previous_step, changed_at = LAST_PROGRESS.get(job_id, (step, now))
        if step != previous_step:
            changed_at = now
        LAST_PROGRESS[job_id] = (step, changed_at)
        if now - changed_at >= 600:
            stalled.append({"id": job_id, "step": step, "unchanged_seconds": round(now - changed_at)})
    for job_id in set(LAST_PROGRESS) - active_ids:
        LAST_PROGRESS.pop(job_id, None)
    return {"ok": not stalled, "running": len(running), "stalled": stalled}


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
    result = run("df", "-P", path)
    fields = result.stdout.strip().splitlines()[-1].split() if result.returncode == 0 else []
    usage = shutil.disk_usage(path)
    percent = float(fields[4].rstrip("%")) if len(fields) >= 5 else round(100 * usage.used / usage.total, 1)
    return {"ok": percent < 90, "used_percent": percent, "free_gib": round(usage.free / 2**30, 1)}


def launch_agent(result: dict[str, Any]) -> bool:
    global LAST_AGENT_AT
    now = time.time()
    if now - LAST_AGENT_AT < 1800:
        return False
    pid_path = STATUS_PATH.parent / "agent.pid"
    if pid_path.exists():
        try:
            os.kill(int(pid_path.read_text().strip()), 0)
            return False
        except (OSError, ValueError):
            pid_path.unlink(missing_ok=True)
    codex = Path("/root/.local/bin/codex")
    if not codex.is_file():
        emit("critical", "agent", "Codex executable missing; escalation not started")
        LAST_AGENT_AT = now
        return False
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    incident = STATUS_PATH.parent / f"incident-{stamp}.json"
    incident.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    output = Path("/var/log") / f"flux-incident-agent-{stamp}.txt"
    events = Path("/var/log") / f"flux-incident-agent-{stamp}.jsonl"
    prompt = (
        "Unattended FLUX incident audit. Diagnose only: do not modify files, restart services, "
        "kill processes, submit jobs, or change configuration. Inspect the incident at "
        f"{incident}, relevant systemd journals, GPU state, and FLUX/Nexus/Piper health. "
        "Produce a concise root-cause assessment and exact recommended repair steps."
    )
    handle = events.open("ab")
    proc = subprocess.Popen(
        [str(codex), "exec", "--sandbox", "read-only", "-C", "/root/FLUX", "--json", "-o", str(output), prompt],
        stdout=handle, stderr=subprocess.STDOUT, start_new_session=True,
        env={**os.environ, "CODEX_HOME": "/root/.codex"},
    )
    handle.close()
    pid_path.write_text(str(proc.pid) + "\n", encoding="utf-8")
    LAST_AGENT_AT = now
    emit("escalated", "agent", "read-only Codex incident audit started", pid=proc.pid, output=str(output), events=str(events))
    return True


def snapshot(repair: bool) -> dict[str, Any]:
    checks: dict[str, Any] = {"services": {name: systemd_check(name, repair) for name in SERVICES}}
    checks["http"] = endpoint_check()
    checks["progress"] = progress_check()
    checks["nexus"] = runtime_check("nexus")
    checks["piper"] = runtime_check("piper")
    checks["gpu"] = gpu_check()
    checks["disk"] = disk_check()
    for runtime, service in (("nexus", "nexus-runtime.service"), ("piper", "piper-runtime.service")):
        if not checks[runtime]["ok"] and repair and note_result(runtime, False) >= 3:
            run("systemctl", "restart", service, timeout=30)
            time.sleep(1)
            checks[runtime] = runtime_check(runtime)
            emit("repaired" if checks[runtime]["ok"] else "critical", runtime, "runtime recovered" if checks[runtime]["ok"] else "runtime unavailable after restart")
            note_result(runtime, checks[runtime]["ok"])
        elif checks[runtime]["ok"]:
            note_result(runtime, True)
    if not checks["http"]["ok"] and repair and note_result("http", False) >= 3:
        run("systemctl", "restart", "flux-server.service", timeout=30)
        time.sleep(1)
        checks["http"] = endpoint_check()
        emit("repaired" if checks["http"]["ok"] else "critical", "http", "FLUX API recovered" if checks["http"]["ok"] else "FLUX API unavailable after restart")
        note_result("http", checks["http"]["ok"])
    elif checks["http"]["ok"]:
        note_result("http", True)
    if checks["gpu"].get("unexpected_vllm"):
        emit("critical", "gpu", "unexpected vLLM container detected; left untouched", containers=checks["gpu"]["unexpected_vllm"])
    if not checks["disk"]["ok"]:
        emit("critical", "disk", "disk usage crossed 90%; no automatic deletion", **checks["disk"])
    ok = all(row.get("ok") for row in checks["services"].values()) and all(checks[name].get("ok") for name in ("http", "progress", "nexus", "piper", "gpu", "disk"))
    result = {"ok": ok, "updated_at": time.time(), "checks": checks}
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATUS_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(STATUS_PATH)
    sustained = note_result("overall", ok)
    if repair and not ok and sustained >= 4:
        launch_agent(result)
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
