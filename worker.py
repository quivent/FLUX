import argparse
import json
import os
import pathlib
import platform
import socket
import shutil
import subprocess
import threading
import time
import uuid

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

import torch
from diffusers import FluxPipeline

import flux_ane


DEFAULT_MODEL_DIR = "/Users/joshkornreich/Models/flux1"
DEFAULT_OUT_DIR = "/Users/joshkornreich/Models/flux-output"


class CancelledJob(RuntimeError):
    pass


class Worker:
    def __init__(self, model_dir, out_dir, state_path, profile_path=None, backend="auto", preload=False):
        self.model_dir = pathlib.Path(model_dir)
        self.out_dir = pathlib.Path(out_dir)
        self.state_path = pathlib.Path(state_path)
        self.profile_path = pathlib.Path(profile_path) if profile_path else self.state_path.with_name("profile.json")
        self.default_backend = normalize_backend(backend)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.profile_path.parent.mkdir(parents=True, exist_ok=True)
        self.pipe = None
        self.pipe_device = None
        self.device = choose_torch_device(self.default_backend)
        self.lock = threading.Lock()
        self.jobs = self._load_jobs()
        self.profile = self._load_profile()
        if preload and self.default_backend in ("auto", "mps", "cpu"):
            self._load_pipe()

    def _load_jobs(self):
        if not self.state_path.exists():
            return {}
        jobs = {}
        for line in self.state_path.read_text().splitlines():
            try:
                job = json.loads(line)
            except json.JSONDecodeError:
                continue
            jobs[job["id"]] = job
        return jobs

    def _write_jobs(self):
        tmp = self.state_path.with_suffix(".tmp")
        with tmp.open("w") as f:
            for job in self.jobs.values():
                f.write(json.dumps(job, sort_keys=True) + "\n")
        tmp.replace(self.state_path)

    def _load_profile(self):
        if not self.profile_path.exists():
            return {"version": 1, "backends": {}}
        try:
            data = json.loads(self.profile_path.read_text())
        except (OSError, json.JSONDecodeError):
            return {"version": 1, "backends": {}}
        if not isinstance(data, dict):
            return {"version": 1, "backends": {}}
        data.setdefault("version", 1)
        data.setdefault("backends", {})
        return data

    def _write_profile(self):
        tmp = self.profile_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.profile, indent=2, sort_keys=True) + "\n")
        tmp.replace(self.profile_path)

    def _profile_key(self, job):
        return f"{int(job['width'])}x{int(job['height'])}:{int(job['steps'])}"

    def _record_profile(self, job, seconds):
        backend = job.get("backend", "unknown")
        key = self._profile_key(job)
        backends = self.profile.setdefault("backends", {})
        sizes = backends.setdefault(backend, {})
        record = sizes.setdefault(key, {"count": 0, "avg_seconds": 0.0, "last_seconds": 0.0})
        count = int(record.get("count", 0)) + 1
        old_avg = float(record.get("avg_seconds", 0.0))
        record["count"] = count
        record["avg_seconds"] = seconds if count == 1 else old_avg + ((seconds - old_avg) / count)
        record["last_seconds"] = seconds
        record["updated"] = time.time()
        self._write_profile()

    def _load_pipe(self, device=None):
        device = device or self.device
        if self.pipe is not None and self.pipe_device == device:
            return
        print(f"loading model={self.model_dir} backend={self.default_backend} device={device}", flush=True)
        pipe = FluxPipeline.from_pretrained(
            self.model_dir,
            torch_dtype=torch.bfloat16,
            local_files_only=True,
        )
        pipe.to(device)
        self.pipe = pipe
        self.pipe_device = device
        print("model_ready=true", flush=True)

    def status(self):
        return {
            "ok": True,
            "loaded": self.pipe is not None,
            "device": self.device,
            "backend": self.default_backend,
            "backends": backend_capabilities(),
            "profile": self.profile,
            "jobs": len(self.jobs),
        }

    def submit(self, payload):
        job_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        requested_backend = normalize_backend(payload.get("backend") or self.default_backend)
        probe_job = {
            "width": int(payload.get("width", 1024)),
            "height": int(payload.get("height", 1024)),
            "steps": int(payload.get("steps", 28)),
        }
        backend = self.resolve_backend(requested_backend, probe_job)
        job = {
            "id": job_id,
            "backend": backend,
            "requested_backend": requested_backend,
            "status": "queued",
            "created": time.time(),
            "prompt": payload["prompt"],
            "width": probe_job["width"],
            "height": probe_job["height"],
            "steps": probe_job["steps"],
            "guidance": float(payload.get("guidance", 3.5)),
            "seed": payload.get("seed"),
            "filename": payload.get("filename"),
            "output": "",
            "error": "",
            "phase": "queued",
            "step": 0,
            "total_steps": int(payload.get("steps", 28)),
            "cancel_requested": False,
        }
        self.jobs[job_id] = job
        self._write_jobs()
        threading.Thread(target=self._run_job, args=(job_id,), daemon=True).start()
        return {"ok": True, "job": job}

    def resolve_backend(self, requested, job):
        requested = normalize_backend(requested)
        if requested != "auto":
            return requested
        caps = backend_capabilities(self.model_dir)
        candidates = []
        if caps.get("mps"):
            candidates.append("mps")
        if caps.get("mlx"):
            candidates.append("mlx")
        if caps.get("ane_renderable"):
            candidates.append("ane")
        if caps.get("coreml_compiled"):
            candidates.append("coreml")
        if not candidates:
            return "cpu"

        key = self._profile_key(job)
        best_backend = ""
        best_seconds = 0.0
        for backend in candidates:
            record = self.profile.get("backends", {}).get(backend, {}).get(key, {})
            if int(record.get("count", 0)) < 1:
                continue
            seconds = float(record.get("avg_seconds", 0.0))
            if seconds <= 0:
                continue
            if best_backend == "" or seconds < best_seconds:
                best_backend = backend
                best_seconds = seconds
        if best_backend:
            return best_backend
        if caps.get("mps"):
            return "mps"
        return candidates[0]

    def _run_job(self, job_id):
        with self.lock:
            job = self.jobs[job_id]
            if job.get("status") == "cancelled":
                self._write_jobs()
                return
            job["status"] = "running"
            job["phase"] = "loading_model"
            job["step"] = 0
            job["total_steps"] = int(job["steps"])
            job["started"] = time.time()
            self._write_jobs()
            try:
                output = self._render(job)
                seconds = time.time() - float(job["started"])
                job["status"] = "done"
                job["phase"] = "done"
                job["step"] = int(job["steps"])
                job["output"] = str(output)
                job["seconds"] = seconds
                self._record_profile(job, seconds)
            except CancelledJob:
                job["status"] = "cancelled"
                job["phase"] = "cancelled"
                job["error"] = ""
            except Exception as exc:
                if job.get("cancel_requested"):
                    job["status"] = "cancelled"
                    job["phase"] = "cancelled"
                    job["error"] = ""
                else:
                    job["status"] = "error"
                    job["phase"] = "error"
                    job["error"] = repr(exc)
            finally:
                job["finished"] = time.time()
                self._write_jobs()

    def _render(self, job):
        backend = job.get("backend", "mps")
        if backend in ("mps", "cpu"):
            return self._render_torch(job, backend)
        if backend == "mlx":
            return self._render_mlx(job)
        if backend == "coreml":
            return self._render_coreml(job)
        if backend == "ane":
            return self._render_ane(job)
        raise ValueError(f"unknown backend {backend!r}")

    def _render_torch(self, job, backend):
        if backend == "cpu":
            self.device = "cpu"
        else:
            self.device = "mps" if torch.backends.mps.is_available() else "cpu"
        self._load_pipe(self.device)
        job["phase"] = "sampling"
        job["step"] = 0
        self._write_jobs()
        generator = None
        if job["seed"] not in (None, ""):
            generator = torch.Generator(device="cpu").manual_seed(int(job["seed"]))

        def on_step_end(_pipe, step, _timestep, callback_kwargs):
            if job.get("cancel_requested"):
                raise CancelledJob("job cancelled")
            job["phase"] = "sampling"
            job["step"] = int(step) + 1
            job["total_steps"] = int(job["steps"])
            self._write_jobs()
            return callback_kwargs

        image = self.pipe(
            prompt=job["prompt"],
            width=job["width"],
            height=job["height"],
            guidance_scale=job["guidance"],
            num_inference_steps=job["steps"],
            generator=generator,
            callback_on_step_end=on_step_end,
        ).images[0]
        job["phase"] = "saving"
        self._write_jobs()
        output = self._output_path(job)
        image.save(output)
        return output

    def _render_mlx(self, job):
        exe = shutil.which("mflux-generate")
        if not exe:
            local_exe = pathlib.Path(__file__).resolve().parent / ".venv/bin/mflux-generate"
            if local_exe.exists():
                exe = str(local_exe)
        if not exe:
            raise RuntimeError("mflux-generate is not installed")
        output = self._output_path(job)
        job["phase"] = "sampling"
        job["step"] = 0
        self._write_jobs()
        cmd = [
            exe,
            "--model",
            str(self.model_dir),
            "--base-model",
            "dev",
            "--prompt",
            job["prompt"],
            "--width",
            str(job["width"]),
            "--height",
            str(job["height"]),
            "--steps",
            str(job["steps"]),
            "--guidance",
            str(job["guidance"]),
            "--output",
            str(output),
        ]
        if job["seed"] not in (None, ""):
            cmd.extend(["--seed", str(job["seed"])])
        proc = subprocess.run(cmd, text=True, capture_output=True)
        job["step"] = int(job["steps"])
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or proc.stdout or "mflux failed").strip())
        return output

    def _render_coreml(self, job):
        compiled_dir = pathlib.Path(os.environ.get("FLUX_COREML_MODEL", self.model_dir / "coreml"))
        if not compiled_dir.exists():
            raise RuntimeError(
                "Core ML backend is installed but no compiled package is configured; "
                "set FLUX_COREML_MODEL to a converted FLUX package"
            )
        raise RuntimeError("Core ML FLUX runner is scaffolded; compiled-model invocation is not implemented yet")

    def _render_ane(self, job):
        package = flux_ane.select_render_package(self.model_dir, job["width"], job["height"])
        if not package:
            raise RuntimeError(
                "ANE backend has no validated full-pipeline package for this size; "
                "use `flux ane convert-vae` to build component packages, then add a renderable "
                "pipeline package only after Instruments confirms Neural Engine execution"
            )
        raise RuntimeError(
            "ANE full-pipeline invocation is not implemented yet; "
            f"validated package found: {package.get('name', 'unknown')}"
        )

    def _output_path(self, job):
        filename = job["filename"]
        if not filename:
            seed_part = "random" if job["seed"] in (None, "") else str(job["seed"])
            filename = f"flux-{job['backend']}-{job['id']}-seed-{seed_part}.png"
        return self.out_dir / filename

    def list_jobs(self):
        return {"ok": True, "jobs": list(self.jobs.values())}

    def list_profile(self):
        return {"ok": True, "profile": self.profile, "backends": backend_capabilities(self.model_dir)}

    def cancel(self, job_id):
        job = self.jobs.get(job_id)
        if not job:
            return {"ok": False, "error": f"unknown job {job_id!r}"}
        status = job.get("status")
        if status in ("done", "error", "cancelled"):
            return {"ok": True, "job": job, "changed": False}
        if status == "running":
            job["cancel_requested"] = True
            job["phase"] = "cancelling"
        else:
            job["status"] = "cancelled"
            job["phase"] = "cancelled"
            job["finished"] = time.time()
        self._write_jobs()
        return {"ok": True, "job": job, "changed": True}

    def prune(self, keep=20, statuses=None):
        keep = max(0, int(keep))
        statuses = set(statuses or ["done", "error", "cancelled"])
        jobs = list(self.jobs.values())
        removable = [job for job in jobs if job.get("status") in statuses]
        removable.sort(key=lambda job: float(job.get("created") or job.get("finished") or 0), reverse=True)
        keep_ids = {job["id"] for job in removable[:keep] if "id" in job}
        removed = []
        for job in removable[keep:]:
            job_id = job.get("id")
            if job_id and job_id not in keep_ids:
                removed.append(job_id)
                self.jobs.pop(job_id, None)
        if removed:
            self._write_jobs()
        return {"ok": True, "removed": removed, "jobs": list(self.jobs.values())}


def normalize_backend(value):
    value = (value or "auto").strip().lower()
    if value not in {"auto", "mps", "mlx", "coreml", "ane", "cpu"}:
        raise ValueError(f"unknown backend {value!r}")
    return value


def choose_torch_device(backend):
    if backend == "cpu":
        return "cpu"
    return "mps" if torch.backends.mps.is_available() else "cpu"


def backend_capabilities(model_dir=None):
    local_mflux = pathlib.Path(__file__).resolve().parent / ".venv/bin/mflux-generate"
    root = pathlib.Path(model_dir) if model_dir else pathlib.Path(DEFAULT_MODEL_DIR)
    coreml_env = os.environ.get("FLUX_COREML_MODEL", "")
    if coreml_env:
        coreml_dir = pathlib.Path(coreml_env)
    else:
        coreml_dir = root / "coreml"
    coreml_available = has_module("coremltools")
    coreml_compiled = coreml_available and coreml_dir.exists()
    ane_caps = flux_ane.capabilities(root)
    return {
        "mps": torch.backends.mps.is_available(),
        "cpu": True,
        "amx": platform.machine() == "arm64",
        "mlx": has_module("mlx") and (shutil.which("mflux-generate") is not None or local_mflux.exists()),
        "coreml": coreml_available,
        "coreml_compiled": coreml_compiled,
        "ane_candidate": bool(ane_caps.get("ane_packages")) or coreml_compiled,
        **ane_caps,
    }


def has_module(name):
    try:
        __import__(name)
        return True
    except Exception:
        return False


def serve(args):
    worker = Worker(args.model_dir, args.out_dir, args.state, profile_path=args.profile, backend=args.backend, preload=args.preload)
    sock_path = pathlib.Path(args.socket)
    sock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        sock_path.unlink()
    except FileNotFoundError:
        pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(sock_path))
    srv.listen(16)
    print(f"socket={sock_path}", flush=True)
    while True:
        conn, _ = srv.accept()
        with conn:
            req = json.loads(conn.makefile().readline())
            op = req.get("op")
            if op == "ping":
                resp = worker.status()
            elif op == "submit":
                resp = worker.submit(req)
            elif op == "jobs":
                resp = worker.list_jobs()
            elif op == "profile":
                resp = worker.list_profile()
            elif op == "cancel":
                resp = worker.cancel(req.get("id", ""))
            elif op == "prune":
                resp = worker.prune(req.get("keep", 20), req.get("statuses"))
            elif op == "stop":
                resp = {"ok": True}
                conn.sendall((json.dumps(resp) + "\n").encode())
                break
            else:
                resp = {"ok": False, "error": f"unknown op {op!r}"}
            conn.sendall((json.dumps(resp) + "\n").encode())
    srv.close()
    try:
        sock_path.unlink()
    except FileNotFoundError:
        pass


def main():
    parser = argparse.ArgumentParser(description="Persistent local FLUX worker")
    parser.add_argument("--socket", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--profile", default=None)
    parser.add_argument("--model-dir", default=os.environ.get("MODEL_DIR", DEFAULT_MODEL_DIR))
    parser.add_argument("--out-dir", default=os.environ.get("OUT_DIR", DEFAULT_OUT_DIR))
    parser.add_argument("--backend", choices=["auto", "mps", "mlx", "coreml", "ane", "cpu"], default=os.environ.get("FLUX_BACKEND", "auto"))
    parser.add_argument("--preload", action="store_true")
    serve(parser.parse_args())


if __name__ == "__main__":
    main()
