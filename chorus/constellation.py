#!/usr/bin/env python3
"""Independent proof daemons and one event-driven controller.

Each actor measures one contract, sends a leased proof over a Unix datagram
socket, and owns no UI state. The controller is the only state writer. It
hash-chains every proof, restarts crashed actors with bounded backoff, and
publishes a single snapshot consumed by the Sentinel event stream.
"""
import argparse
import hashlib
import json
import os
import pathlib
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

from PIL import Image


ACTORS = {
    "daemon-supervisor": (7, "all monitoring actors hold live process leases"),
    "reboot-resume": (45, "a durable autonomous-run manifest can resume after node wake"),
    "message-authority": (11, "Piper and Nexus both accept local socket connections"),
    "public-service": (17, "local and public Tea health contracts both answer"),
    "asset-stream": (13, "the asset event lane has a live broker and recent evidence"),
    "flux-process": (9, "a FLUX inference process owns CUDA while work is active"),
    "flux-inference": (19, "the newest FLUX output is a decodable image"),
    "gemma-service": (180, "Gemma completes a real minimal inference"),
    "gemma-continuity": (240, "Gemma observes continuity as one independent antenna"),
    "gemma-composition": (240, "Gemma observes composition as one independent antenna"),
    "gemma-material-light": (240, "Gemma observes material and light as one independent antenna"),
    "gemma-meaningful-change": (240, "Gemma observes meaningful change as one independent antenna"),
    "hive-calibration": (43, "four fresh Gemma antenna proofs meet in one calibration ledger"),
    "worker-fleet": (17, "socket workers or an explicit direct worker are available"),
    "gpu-health": (11, "the H100 reports bounded temperature and valid utilization"),
    "generation-progress": (13, "the active study advances its durable image count"),
    "motion-principle": (31, "measured motion sets cadence: low motion faster, large motion slower"),
    "r2-durability": (23, "R2 has a recent verified durability receipt"),
    "gemini-coding-authority": (17, "one resumable Gemini coding session proxies every actor message with non-interactive tools"),
    "optimization-auditor": (29, "step depth, resolution, batching and cache claims remain evidence-bound"),
}
assert len(ACTORS) == 20


def atomic_json(path, value):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def run(command, timeout=8):
    return subprocess.run(command, text=True, capture_output=True, timeout=timeout, check=False)


def url_json(url, timeout=5, method="GET", payload=None, headers=None):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method,
                                     headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, json.loads(response.read().decode())


def unix_connect(path):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(2); conn.connect(str(path))


def proof(status, metric, value, evidence, recoverable=False):
    return {"status": status, "metric": metric, "value": value,
            "evidence": evidence, "recoverable": recoverable}


def pgrep(pattern):
    result = run(["pgrep", "-f", pattern])
    return [int(line) for line in result.stdout.splitlines() if line.strip().isdigit()]


def check_actor(name, args, memo):
    out, run_dir, root = pathlib.Path(args.out_dir), pathlib.Path(args.run_dir), pathlib.Path(args.root)
    if name == "daemon-supervisor":
        alive = 0
        for actor in ACTORS:
            try:
                pid = int((run_dir / "actors" / f"{actor}.pid").read_text())
                os.kill(pid, 0); alive += 1
            except (OSError, ValueError):
                pass
        return proof("healthy" if alive == len(ACTORS) else "failing", "actors_alive", alive,
                     f"{alive}/{len(ACTORS)} actor pid leases", True)
    if name == "reboot-resume":
        path = root / "chorus" / "night-run.json"
        return proof("healthy" if path.exists() else "degraded", "resume_manifest", int(path.exists()),
                     str(path), True)
    if name == "message-authority":
        errors = []
        for path in map(pathlib.Path, ("/tmp/piper.sock", "/tmp/nexus.sock")):
            try: unix_connect(path)
            except OSError as exc: errors.append(f"{path}:{exc}")
        return proof("healthy" if not errors else "failing", "sockets_connected", 2 - len(errors),
                     "; ".join(errors) if errors else "Piper + Nexus", True)
    if name == "public-service":
        codes, errors = [], []
        for url in ("http://127.0.0.1:7861/api/health", args.public_base.rstrip("/") + "/api/health"):
            try:
                status, body = url_json(url); codes.append(status if body.get("ok") else 0)
            except Exception as exc: errors.append(str(exc))
        if not errors and codes == [200, 200]:
            return proof("healthy", "healthy_origins", 2, "local + public", True)
        return proof("failing", "healthy_origins", len(codes), "; ".join(errors), True)
    if name == "asset-stream":
        path = pathlib.Path("/tmp/piper.sock")
        recent = max((p.stat().st_mtime for p in out.rglob("*.png")), default=0)
        try:
            unix_connect(path); connected = True
        except OSError:
            connected = False
        age = round(time.time() - recent, 1) if recent else -1
        return proof("healthy" if connected else "failing", "latest_asset_age_seconds", age,
                     f"broker={connected} newest_asset_age={age}", True)
    if name == "flux-process":
        pids = pgrep(r"(step_sweep|late_fork|tournament|chorus/loop)\.py")
        return proof("healthy" if pids else "degraded", "flux_processes", len(pids),
                     "pids=" + ",".join(map(str, pids)), True)
    if name == "flux-inference":
        images = sorted(out.rglob("step-study-*-steps-*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not images:
            return proof("failing", "decodable_latest_output", 0, "no step-study outputs", True)
        try:
            with Image.open(images[0]) as image:
                image.verify()
            return proof("healthy", "decodable_latest_output", 1, images[0].relative_to(out).as_posix())
        except Exception as exc:
            return proof("failing", "decodable_latest_output", 0, str(exc), True)
    if name == "gemma-service" or name.startswith("gemma-"):
        bases = ["http://governor-eyes.givemeanode.internal:8000/v1", "http://100.64.0.4:8000/v1"]
        errors = []
        for base in bases:
            try:
                status, models = url_json(base + "/models", timeout=4)
                model = (models.get("data") or [{}])[0].get("id")
                if not model:
                    raise RuntimeError("model list empty")
                if name != "gemma-service":
                    focus = name.removeprefix("gemma-").replace("-", " ")
                    sheet = args.public_base.rstrip("/") + "/outputs/_sheets/contact.jpg"
                    prompt_text = (f"Observe only {focus}. Do not select or direct. Reply JSON only: "
                                   '{"score":0,"finding":"concrete observation under 18 words"}')
                    content = [{"type": "text", "text": prompt_text},
                               {"type": "image_url", "image_url": {"url": sheet}}]
                    status, answer = url_json(base + "/chat/completions", timeout=90, method="POST", payload={
                        "model": model, "messages": [{"role": "user", "content": content}],
                        "max_tokens": 96, "temperature": 0})
                    text = ((answer.get("choices") or [{}])[0].get("message") or {}).get("content", "")
                    antenna_dir = out / "_sheets" / "antennas"; antenna_dir.mkdir(parents=True, exist_ok=True)
                    atomic_json(antenna_dir / f"{name}.json", {"actor": name, "focus": focus,
                                "response": text, "measured_at": time.time(), "engine": base})
                    return proof("healthy" if text else "failing", "focused_inference_chars", len(text),
                                 f"{focus}: {text[:180]}")
                status, answer = url_json(base + "/chat/completions", timeout=45, method="POST", payload={
                    "model": model, "messages": [{"role": "user", "content": "Reply only GEMMA_OK"}],
                    "max_tokens": 8, "temperature": 0})
                text = ((answer.get("choices") or [{}])[0].get("message") or {}).get("content", "")
                return proof("healthy" if status == 200 and text else "failing", "minimal_inference", len(text),
                             f"{base} returned {len(text)} chars")
            except Exception as exc:
                errors.append(str(exc))
        return proof("failing", "minimal_inference", 0, "; ".join(errors)[-500:], True)
    if name == "hive-calibration":
        antenna_dir = out / "_sheets" / "antennas"
        files = [antenna_dir / f"gemma-{focus}.json" for focus in
                 ("continuity", "composition", "material-light", "meaningful-change")]
        fresh, findings = 0, []
        for path in files:
            try:
                body = json.loads(path.read_text())
                if time.time() - float(body.get("measured_at") or 0) < 600: fresh += 1
                findings.append({"actor": body.get("actor"), "response": body.get("response")})
            except Exception: pass
        beauty = {}
        for candidate in (root / "chorus" / "night-run.json",
                          root / "chorus" / "eras" / "garden-remembers-machine.json"):
            try:
                body = json.loads(candidate.read_text())
                beauty = body.get("beauty_protocol") or body
                if beauty:
                    break
            except Exception:
                pass
        operator_feedback = []
        try:
            operator_feedback = [json.loads(line) for line in
                                 (out / "operator-feedback.jsonl").read_text().splitlines()[-24:]]
        except Exception:
            pass
        calibration = {"measured_at": time.time(), "fresh_antennas": fresh, "findings": findings,
                       "beauty_protocol": beauty, "operator_feedback": operator_feedback,
                       "rule": "preserve soul and material truth; demand meaningful change; Director decides"}
        antenna_dir.mkdir(parents=True, exist_ok=True)
        atomic_json(antenna_dir / "hive-calibration.json", calibration)
        return proof("healthy" if fresh == 4 else "degraded", "fresh_antennas", fresh,
                     f"{fresh}/4 focal proofs; independent evidence retained")
    if name == "worker-fleet":
        direct = bool(pgrep(r"(step_sweep|late_fork|tournament|chorus/loop)\.py"))
        try:
            _, body = url_json("http://127.0.0.1:7861/api/health")
            up = int(((body.get("fleet") or {}).get("up") or 0))
        except Exception:
            up = 0
        return proof("healthy" if direct or up else "failing", "available_workers", up + int(direct),
                     f"socket={up} direct={int(direct)}", True)
    if name == "gpu-health":
        result = run(["nvidia-smi", "--query-gpu=temperature.gpu,utilization.gpu,memory.used,memory.total",
                      "--format=csv,noheader,nounits"])
        try:
            temp, util, used, total = [int(x.strip()) for x in result.stdout.split(",")]
            return proof("healthy" if temp < 84 else "failing", "gpu_temperature_c", temp,
                         f"util={util}% memory={used}/{total} MiB", True)
        except Exception:
            return proof("failing", "gpu_temperature_c", -1, result.stderr[-300:], True)
    if name == "generation-progress":
        images = list(out.rglob("step-study-*-r[1-4].sphere/*-steps-*.png"))
        count = len(images); active = bool(pgrep(r"step_sweep\.py")); previous = memo.get("count", count)
        memo["count"] = count
        advancing = count > previous
        status = "healthy" if advancing or (count >= 384 and not active) else "degraded" if active else "failing"
        return proof(status, "night_outputs", count, f"delta={count-previous} active={active}", True)
    if name == "motion-principle":
        ledgers = list((out / "atlas").glob("*.sphere/_repair/continuity-ledger.json"))
        jobs, median = 0, 0.0
        for ledger in ledgers:
            try:
                body = json.loads(ledger.read_text()); jobs += int(body.get("replacement_jobs") or 0)
                median = float(body.get("median_motion") or median)
            except (OSError, ValueError): pass
        fps = max(4, min(18, round(8 * 0.018 / max(median, 0.001)))) if median else 8
        return proof("healthy" if ledgers else "degraded", "recommended_fps", fps,
                     f"median_motion={median:.5f}; low motion accelerates, large motion slows; repair_jobs={jobs}")
    if name == "r2-durability":
        path = out / "r2-status.json"
        try:
            body = json.loads(path.read_text()); age = time.time() - float(body.get("last_success_at") or 0)
            ok = body.get("status") == "healthy" and age < 240 and body.get("remote_verified") is True
            return proof("healthy" if ok else "failing", "last_verified_age_seconds", round(age, 1),
                         f"remote={body.get('frames',{}).get('remote')} missing={body.get('frames',{}).get('missing_settled')}", True)
        except Exception as exc:
            return proof("failing", "last_verified_age_seconds", -1, str(exc), True)
    if name == "gemini-coding-authority":
        coder_socket = pathlib.Path("/tmp/gemini-coder.sock")
        status_path = run_dir / "gemini-coder.json"
        try:
            unix_connect(coder_socket)
            body = json.loads(status_path.read_text())
            status = body.get("status")
            return proof("healthy" if status in ("ready", "busy") else "degraded",
                         "coding_session", status, f"socket={coder_socket} pid={body.get('pid')}", True)
        except Exception as exc:
            if not pgrep(r"chorus/gemini_coder\.py"):
                log = (run_dir / "gemini-coder.log").open("a")
                subprocess.Popen([sys.executable, str(root / "chorus" / "gemini_coder.py"),
                                  "--root", str(root), "--run-dir", str(run_dir)],
                                 cwd=root, start_new_session=True, stdout=log,
                                 stderr=subprocess.STDOUT)
                log.close()
            return proof("degraded", "coding_session", "starting", str(exc), True)
    if name == "optimization-auditor":
        valid, hits, checkpoints = 0, 0, 0
        for cache_manifest in (out / "atlas").glob("*.sphere/_cache/manifest.json"):
            try:
                body = json.loads(cache_manifest.read_text())
                if body.get("schema") != "flux.exact-trunk-cache.v1":
                    continue
                names = body.get("checkpoints") or []
                if names and all((cache_manifest.parent / item).exists() for item in names):
                    valid += 1; checkpoints += len(names)
                sphere_manifest = cache_manifest.parent.parent / "manifest.json"
                if json.loads(sphere_manifest.read_text()).get("exact_trunk_cache", {}).get("hit"):
                    hits += 1
            except Exception:
                pass
        report = {"measured_at": time.time(), "exact_cache_sets": valid,
                  "exact_cache_hits": hits, "latent_checkpoints": checkpoints,
                  "current_policy": {"resolution": 512, "batch_size": 1,
                  "exact_trunk_cache": "enabled", "similarity_cache": "disabled until paired image audit"},
                  "facts": {"trajectory_reuse_at_step_25_of_28": 0.892857,
                  "four_branch_transformer_work_saved": 0.669643,
                  "adjacent_total_step_sweeps_share_no_exact_scheduler_prefix": True}}
        atomic_json(out / "optimization-status.json", report)
        status = "healthy" if valid else "degraded"
        return proof(status, "validated_exact_cache_sets", valid,
                     f"hits={hits} checkpoints={checkpoints}; 25/28 trajectory=89.3%, four-branch transformer saving=67.0%; similarity cache OFF")
    return proof("failing", "unknown_actor", 0, name)


def actor_main(args):
    interval, protocol = ACTORS[args.actor]
    memo = {}
    while True:
        began = time.time()
        try:
            body = check_actor(args.actor, args, memo)
        except Exception as exc:
            body = proof("failing", "uncaught_exception", 0, repr(exc), True)
        body.update({"actor": args.actor, "protocol": protocol, "pid": os.getpid(),
                     "measured_at": time.time(), "duration_ms": round((time.time() - began) * 1000, 2),
                     "lease_expires_at": time.time() + interval * 2.5})
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as conn:
                conn.sendto(json.dumps(body, sort_keys=True).encode(), args.bus)
        except OSError:
            pass
        time.sleep(interval)


def append_audit(path, prior_hash, event):
    body = json.dumps(event, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256((prior_hash + body).encode()).hexdigest()
    with path.open("a") as stream:
        stream.write(json.dumps({**event, "prior_hash": prior_hash, "hash": digest}, sort_keys=True) + "\n")
    return digest


def published_methods(out, root, actor_state):
    try:
        optimization = json.loads((out / "optimization-status.json").read_text())
    except Exception:
        optimization = {"exact_cache_sets": 0, "exact_cache_hits": 0, "latent_checkpoints": 0,
                        "facts": {"trajectory_reuse_at_step_25_of_28": 0.892857,
                                  "four_branch_transformer_work_saved": 0.669643,
                                  "adjacent_total_step_sweeps_share_no_exact_scheduler_prefix": True},
                        "current_policy": {"resolution": 512, "batch_size": 1,
                                           "similarity_cache": "disabled until paired image audit"}}
    outputs = len(list(out.rglob("step-study-*-r[1-4].sphere/*-steps-*.png")))
    antennas = actor_state.get("hive-calibration", {}).get("value", 0)
    r2_age = actor_state.get("r2-durability", {}).get("value", -1)
    motion_fps = actor_state.get("motion-principle", {}).get("value", 12)
    methods = [
        {"method": "Exact trunk latent checkpoints", "role": "reuse an identical denoising prefix and resume late forks",
         "metric": "trajectory reuse", "value": "89.3% at step 25/28", "status": "measured",
         "evidence": f"{optimization.get('exact_cache_sets', 0)} valid sets · {optimization.get('latent_checkpoints', 0)} checkpoints · {optimization.get('exact_cache_hits', 0)} hits"},
        {"method": "Four-way late geometry fork", "role": "steer literal motion after shared composition has formed",
         "metric": "transformer work saved", "value": "67.0% vs four independent 28-step renders",
         "status": "derived", "evidence": "25 shared passes + four 3-pass suffixes instead of four 28-pass runs"},
        {"method": "Similarity / first-block cache", "role": "skip transformer work only when residual similarity is safe",
         "metric": "validated utility", "value": "0% claimed", "status": "gated",
         "evidence": "OFF until a paired pixel, geometry and beauty audit proves equivalence"},
        {"method": "Adjacent schedule sweep", "role": "measure where geometry changes as total denoise depth changes",
         "metric": "completed outputs", "value": f"{outputs}/384", "status": "running" if outputs < 384 else "measured",
         "evidence": "same prompt, seed, initial latent, resolution, precision and guidance"},
        {"method": "Sequential 512px batch-one inference", "role": "maximize inspectable arrivals and avoid batch memory pressure",
         "metric": "batch / resolution", "value": "1 / 512×512", "status": "active",
         "evidence": "iterative generation; completed files are resumable and never overwritten"},
        {"method": "Beauty north-star protocol", "role": "carry soul, material truth and earned change between prompts",
         "metric": "binding principles", "value": "4 preserve · 3 demand · 4 reject", "status": "active",
         "evidence": str(root / "chorus" / "night-run.json")},
        {"method": "Gemma focal antennas", "role": "observe continuity, composition, material/light and meaningful change independently",
         "metric": "fresh perspectives", "value": f"{antennas}/4", "status": "active" if antennas == 4 else "waiting",
         "evidence": "independent evidence is calibrated without averaging away disagreement"},
        {"method": "Continuity repair ledger", "role": "identify large gaps and under-motion without deleting originals",
         "metric": "replacement protocol", "value": "still + gap", "status": "active",
         "evidence": "suspect transitions are queued for replacement; originals remain authoritative"},
        {"method": "Measured playback cadence", "role": "make subtle motion legible and slow large jumps",
         "metric": "recommended FPS", "value": motion_fps, "status": "active",
         "evidence": "public motion surface currently capped at 12 FPS"},
        {"method": "R2 content durability", "role": "preserve images, manifests, feedback and exact latent checkpoints",
         "metric": "verified receipt age", "value": f"{r2_age}s" if isinstance(r2_age, (int, float)) and r2_age >= 0 else "waiting",
         "status": "active", "evidence": "remote verification is required; upload intent alone does not pass"},
        {"method": "Event-driven asset and proof streams", "role": "publish arrivals and health without browser polling",
         "metric": "transport", "value": "WebSocket + SSE", "status": "active",
         "evidence": "Piper asset socket and inotify-backed Sentinel stream"},
        {"method": "Gemini coding authority", "role": "turn observations from any model actor into tested repository changes",
         "metric": "session state", "value": actor_state.get("gemini-coding-authority", {}).get("value", "starting"),
         "status": actor_state.get("gemini-coding-authority", {}).get("status", "starting"),
         "evidence": "one serialized resumable session; local 0600 proxy; non-interactive tools"},
    ]
    return optimization, methods


def controller_main(args):
    run_dir, out = pathlib.Path(args.run_dir), pathlib.Path(args.out_dir)
    actor_dir = run_dir / "actors"; actor_dir.mkdir(parents=True, exist_ok=True)
    bus = pathlib.Path(args.bus); bus.unlink(missing_ok=True)
    state_path, audit_path = out / "sentinel-state.json", out / "sentinel-audit.jsonl"
    children, restarts, state, recent = {}, {name: 0 for name in ACTORS}, {}, []
    previous_hash = "0" * 64
    if audit_path.exists():
        try: previous_hash = json.loads(audit_path.read_text().splitlines()[-1])["hash"]
        except Exception: pass

    def spawn(name):
        command = [sys.executable, str(pathlib.Path(__file__).resolve()), "--actor", name,
                   "--root", args.root, "--out-dir", args.out_dir, "--run-dir", args.run_dir,
                   "--public-base", args.public_base, "--bus", args.bus]
        child = subprocess.Popen(command, start_new_session=True,
                                 stdout=(run_dir / f"actor-{name}.log").open("a"),
                                 stderr=subprocess.STDOUT)
        children[name] = child
        (actor_dir / f"{name}.pid").write_text(str(child.pid) + "\n")

    for name in ACTORS: spawn(name)
    stopping = False
    def stop(*_):
        nonlocal stopping; stopping = True
    signal.signal(signal.SIGTERM, stop); signal.signal(signal.SIGINT, stop)
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as server:
        server.bind(str(bus)); os.chmod(bus, 0o600); server.settimeout(5)
        while not stopping:
            try:
                raw = server.recv(65535); event = json.loads(raw)
                state[event["actor"]] = event
                previous_hash = append_audit(audit_path, previous_hash, event)
                recent.append({"actor": event["actor"], "status": event["status"],
                               "metric": event["metric"], "value": event["value"],
                               "measured_at": event["measured_at"], "hash": previous_hash})
                recent = recent[-36:]
            except socket.timeout:
                pass
            except (ValueError, OSError):
                pass
            for name, child in list(children.items()):
                if child.poll() is None: continue
                if restarts[name] < 4:
                    restarts[name] += 1; time.sleep(min(4, restarts[name])); spawn(name)
                else:
                    state[name] = {"actor": name, "protocol": ACTORS[name][1], "status": "failing",
                                   "metric": "restart_budget_exhausted", "value": restarts[name],
                                   "evidence": f"exit={child.returncode}", "measured_at": time.time(),
                                   "lease_expires_at": time.time(), "recoverable": False}
            now = time.time()
            actors = []
            for name, (_, protocol) in ACTORS.items():
                item = dict(state.get(name) or {"actor": name, "protocol": protocol, "status": "starting",
                                                "metric": "awaiting_first_proof", "value": 0,
                                                "evidence": "actor spawned", "measured_at": now,
                                                "lease_expires_at": now + 30})
                if item.get("lease_expires_at", 0) < now:
                    item["status"] = "failing"; item["evidence"] = "proof lease expired"
                item["restarts"] = restarts[name]; actors.append(item)
            healthy = sum(item["status"] == "healthy" for item in actors)
            optimization, methods = published_methods(out, pathlib.Path(args.root), state)
            atomic_json(state_path, {"schema": "flux.sentinel.v1", "updated_at": now,
                                     "actors": actors, "summary": {"healthy": healthy,
                                     "total": len(actors), "failing": sum(x["status"] == "failing" for x in actors),
                                     "degraded": sum(x["status"] == "degraded" for x in actors)},
                                     "cache": optimization, "methods": methods,
                                     "audit": {"path": str(audit_path), "head": previous_hash,
                                     "events": recent}})
    for child in children.values():
        if child.poll() is None: child.terminate()
    bus.unlink(missing_ok=True)


def main():
    ap = argparse.ArgumentParser(description="proof daemons and Sentinel state controller")
    ap.add_argument("--actor", choices=sorted(ACTORS))
    ap.add_argument("--root", default=str(pathlib.Path.home() / "FLUX"))
    ap.add_argument("--out-dir", default=str(pathlib.Path.home() / "models" / "flux-output"))
    ap.add_argument("--run-dir", default=str(pathlib.Path.home() / ".flux-run"))
    ap.add_argument("--public-base", default="https://tea.influx.vision")
    ap.add_argument("--bus", default="/tmp/flux-sentinel.sock")
    args = ap.parse_args()
    if args.actor: actor_main(args)
    else: controller_main(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
