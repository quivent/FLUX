import argparse
import functools
import gc
import inspect
import json
import math
import os
import pathlib
import platform
import socket
import shutil
import subprocess
import threading
import time
import unittest.mock
import uuid

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

import torch
from diffusers import FluxImg2ImgPipeline, FluxPipeline
from PIL import Image
import numpy as np

import flux_ane


DEFAULT_MODEL_DIR = "/Users/joshkornreich/Models/flux1"
DEFAULT_OUT_DIR = "/Users/joshkornreich/Models/flux-output"


class CancelledJob(RuntimeError):
    pass


def _publish_piper_asset(job_id, path, index, total, *, access_url=None, seed=None):
    socket_path = os.environ.get("PIPER_SOCKET", "/tmp/piper.sock")
    payload = {
        "type": "asset.publish",
        "job_id": str(job_id),
        "asset": {
            "id": f"{job_id}:{index}",
            "name": pathlib.Path(path).name,
            "path": str(path),
            "media_type": "image/png",
            "index": int(index),
            "total": int(total),
            "access_url": access_url or f"/outputs/atlas/{job_id}.sphere/{pathlib.Path(path).name}",
            "seed": "" if seed is None else str(seed),
        },
    }
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(1.0)
            conn.connect(socket_path)
            conn.sendall((json.dumps(payload, separators=(",", ":")) + "\n").encode())
            conn.shutdown(socket.SHUT_WR)
            conn.recv(4096)
        return True
    except OSError:
        return False


def _finite_int(value, fallback, minimum, maximum):
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = fallback
    return max(minimum, min(maximum, n))


def _finite_float(value, fallback, minimum, maximum):
    try:
        n = float(value)
    except (TypeError, ValueError):
        n = fallback
    if not math.isfinite(n):
        n = fallback
    return max(minimum, min(maximum, n))


def _six6(value):
    out = []
    for item in value or []:
        try:
            n = float(item)
        except (TypeError, ValueError):
            n = 0.0
        out.append(n if math.isfinite(n) else 0.0)
    return (out + [0.0] * 6)[:6]


def _so4(coeffs):
    planes = ((0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3))
    matrix = np.zeros((4, 4), dtype=float)
    for k, (i, j) in enumerate(planes):
        matrix[i, j] += coeffs[k]
        matrix[j, i] -= coeffs[k]
    return matrix


def _expm_real(matrix):
    vals, vecs = np.linalg.eig(matrix)
    return (vecs @ np.diag(np.exp(vals)) @ np.linalg.inv(vecs)).real


def _gram_schmidt(vectors):
    out = []
    for vector in vectors:
        w = vector
        for q in out:
            w = w - (w @ q) * q
        norm = w.norm()
        if float(norm) <= 0:
            raise ValueError("latent basis collapsed during gram-schmidt")
        out.append(w / norm)
    return out


def _sphere_probe(row, col, n_rows, n_cols, traversal="spherical_outward", coupling=1.0):
    if traversal == "spherical_outward":
        theta = (2 * math.pi * (row + coupling * col / n_cols + 0.5)) / n_rows
        azimuth = (2 * math.pi * (col + coupling * row / n_rows + 0.5)) / n_cols
    else:
        theta = (2 * math.pi * row) / n_rows
        azimuth = (2 * math.pi * col) / n_cols
    return theta, azimuth


def _atlas_render_order(index_start, index_end, n_rows, n_cols, order):
    order = str(order or "column_serpentine").lower()
    if order in ("raster", "row", "row_major"):
        return list(range(index_start, index_end))

    start_row = index_start // n_cols
    end_row = (index_end - 1) // n_cols if index_end > index_start else start_row
    rows = range(start_row, min(n_rows, end_row + 1))

    if order in ("row_serpentine", "serpentine"):
        indices = []
        for row in rows:
            cols = range(n_cols - 1, -1, -1) if row % 2 else range(n_cols)
            for col in cols:
                idx = row * n_cols + col
                if index_start <= idx < index_end:
                    indices.append(idx)
        return indices

    if order not in ("column_serpentine", "column", "theta"):
        raise ValueError(f"unknown atlas traversal_order {order!r}")

    indices = []
    for col in range(n_cols):
        row_iter = reversed(list(rows)) if col % 2 else rows
        for row in row_iter:
            idx = row * n_cols + col
            if index_start <= idx < index_end:
                indices.append(idx)
    return indices


def _vdc_base2(n):
    v = 0.0
    denom = 1.0
    while n:
        denom *= 2.0
        n, remainder = divmod(n, 2)
        v += remainder / denom
    return v


def _atlas_sample_order(indices, render_count, sample_mode):
    if render_count <= 0 or render_count >= len(indices):
        return indices
    sample_mode = str(sample_mode or "contiguous").lower()
    if sample_mode in ("sparse", "nested_sparse", "dyadic", "fill"):
        picked = []
        seen = set()
        k = 1
        while len(picked) < render_count and k <= len(indices) * 4:
            pos = min(len(indices) - 1, int(_vdc_base2(k) * len(indices)))
            idx = indices[pos]
            if idx not in seen:
                picked.append(idx)
                seen.add(idx)
            k += 1
        if len(picked) < render_count:
            for idx in indices:
                if idx not in seen:
                    picked.append(idx)
                    seen.add(idx)
                    if len(picked) >= render_count:
                        break
        return picked
    if sample_mode in ("stride", "even"):
        if render_count == 1:
            return [indices[len(indices) // 2]]
        return [indices[min(len(indices) - 1, round(i * (len(indices) - 1) / (render_count - 1)))] for i in range(render_count)]
    return indices[:render_count]


def _atlas_order_delta_summary(indices, n_rows, n_cols, traversal, coupling, sample_limit=4096):
    if len(indices) < 2:
        return {"samples": 0, "median_radians": 0.0, "max_radians": 0.0}
    samples = []
    prev_theta = None
    prev_azimuth = None
    for idx in indices[:sample_limit]:
        row = idx // n_cols
        col = idx % n_cols
        theta, azimuth = _sphere_probe(row, col, n_rows, n_cols, traversal, coupling)
        if prev_theta is not None:
            dtheta = abs(theta - prev_theta)
            dtheta = min(dtheta, 2 * math.pi - dtheta)
            dazimuth = abs(azimuth - prev_azimuth)
            dazimuth = min(dazimuth, 2 * math.pi - dazimuth)
            samples.append(math.hypot(dtheta, dazimuth))
        prev_theta = theta
        prev_azimuth = azimuth
    if not samples:
        return {"samples": 0, "median_radians": 0.0, "max_radians": 0.0}
    samples = sorted(samples)
    return {
        "samples": len(samples),
        "median_radians": samples[len(samples) // 2],
        "max_radians": max(samples),
    }


def _atlas_latent(mode, theta, azimuth, basis, radius, shape, dtype, job):
    e0, e1, e2, e3 = basis
    shell_scale = _finite_float(job.get("shell_scale"), 1.0, 0.01, 4.0)
    radius = radius * shell_scale
    if mode == "omega":
        rates = _six6(job.get("rates"))
        offsets = _six6(job.get("offsets"))
        start = np.array([1.0, 0.0, 0.0, 0.0])
        if azimuth:
            start = _expm_real(_so4([0.0, 0.0, float(azimuth), 0.0, 0.0, 0.0])) @ start
        coeff = _expm_real(float(theta) * _so4(rates)) @ _expm_real(_so4(offsets)) @ start
        latent = (coeff[0] * e0 + coeff[1] * e1 + coeff[2] * e2 + coeff[3] * e3) * radius
    else:
        arc = _finite_float(job.get("arc"), 1.5708, -8.0, 8.0)
        amp = _finite_float(job.get("amp"), 1.0, -8.0, 8.0)
        spin = _finite_float(job.get("spin"), 2.0, -32.0, 32.0)
        base = _finite_float(job.get("base"), 0.0, -8.0, 8.0)
        orbit = _finite_float(job.get("orbit"), 1.0 if mode == "elliptic" else 0.0, -32.0, 32.0)
        if mode == "screw":
            latent = (radius / math.sqrt(2.0)) * (
                math.cos(spin * theta) * e0 + math.sin(spin * theta) * e1
                + math.cos(theta) * e2 + math.sin(theta) * e3
            )
        else:
            if mode == "sway":
                a = amp * (1.0 - math.cos(theta)) / 2.0
            elif mode == "oscillatory":
                a = amp * math.sin(theta)
            else:
                a = base if base else arc
            phi = orbit * theta + azimuth
            latent = radius * (
                math.cos(a) * e0
                + math.sin(a) * math.cos(phi) * e1
                + math.sin(a) * math.sin(phi) * e2
                + 0.0 * e3
            )
    seed_lock = _finite_float(job.get("seed_lock"), 0.0, 0.0, 0.95)
    if seed_lock:
        home = e0 * radius
        latent = (1.0 - seed_lock) * latent + seed_lock * home
        norm = latent.norm()
        if float(norm) > 0:
            latent = latent * (radius / norm)
    return latent.reshape(shape).to(dtype)


class Worker:
    def __init__(self, model_dir, out_dir, state_path, profile_path=None, backend="auto", preload=False, kind="flux"):
        self.model_dir = pathlib.Path(model_dir)
        self.out_dir = pathlib.Path(out_dir)
        self.state_path = pathlib.Path(state_path)
        self.profile_path = pathlib.Path(profile_path) if profile_path else self.state_path.with_name("profile.json")
        self.default_backend = normalize_backend(backend)
        self.kind = str(kind or "flux").lower()
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.profile_path.parent.mkdir(parents=True, exist_ok=True)
        self.pipe = None
        self.pipe_device = None
        self.pipe_adapter_config = None
        self.img2img_pipe = None
        self.img2img_device = None
        self.first_block_cache_stats = None
        self.device = choose_torch_device(self.default_backend)
        self.lock = threading.Lock()
        self.jobs_lock = threading.RLock()
        self.jobs = self._load_jobs()
        self.profile = self._load_profile()
        if preload and self.default_backend in ("auto", "mps", "cpu"):
            if self.kind == "img2img":
                self._load_img2img_pipe()
            else:
                self._load_pipe()

    def _load_img2img_pipe(self, device=None):
        device = device or self.device
        if self.img2img_pipe is not None and self.img2img_device == device:
            return
        print(f"loading_img2img_model={self.model_dir} backend={self.default_backend} device={device}", flush=True)
        pipe = FluxImg2ImgPipeline.from_pretrained(
            str(self.model_dir),
            torch_dtype=torch.bfloat16,
            local_files_only=True,
        )
        pipe.to(device)
        self.img2img_pipe = pipe
        self.img2img_device = device
        print("img2img_model_ready=true", flush=True)

    def _load_jobs(self):
        if not self.state_path.exists():
            return {}
        jobs = {}
        for line in self.state_path.read_text().splitlines():
            try:
                job = json.loads(line)
            except json.JSONDecodeError:
                continue
            if job.get("status") in ("queued", "running", "cancelling"):
                job["status"] = "error"
                job["phase"] = "interrupted"
                job["error"] = "worker restarted before this job finished"
                job["finished"] = time.time()
            jobs[job["id"]] = job
        return jobs

    def _write_jobs(self):
        with self.jobs_lock:
            rows = [dict(job) for job in self.jobs.values()]
        tmp = self.state_path.with_suffix(".tmp")
        with tmp.open("w") as f:
            for job in rows:
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
            str(self.model_dir),
            torch_dtype=torch.bfloat16,
            local_files_only=True,
        )
        pipe.to(device)
        self.pipe = pipe
        self.pipe_device = device
        self.pipe_adapter_config = None
        print("model_ready=true", flush=True)

    def _discard_pipe_adapter(self):
        if self.pipe is None:
            self.pipe_adapter_config = None
            self.first_block_cache_stats = None
            return
        self.pipe = None
        self.pipe_device = None
        self.pipe_adapter_config = None
        self.first_block_cache_stats = None
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    def _apply_flux_cache_compat(self, first_block_utils):
        transformer = self.pipe.transformer
        if getattr(transformer, "_is_cached", False):
            return
        single_blocks = transformer.single_transformer_blocks
        signature = inspect.signature(single_blocks[0].forward)
        if "encoder_hidden_states" not in signature.parameters:
            from para_attn.first_block_cache.diffusers_adapters.flux import apply_cache_on_transformer

            apply_cache_on_transformer(transformer)
            return

        class CurrentFluxCachedBlocks(first_block_utils.CachedTransformerBlocks):
            def call_remaining_transformer_blocks(this, hidden_states, encoder_hidden_states, *args, **kwargs):
                original_hidden = hidden_states
                original_encoder = encoder_hidden_states
                for block in this.transformer_blocks[1:]:
                    encoder_hidden_states, hidden_states = block(
                        hidden_states, encoder_hidden_states, *args, **kwargs
                    )
                for block in this.single_transformer_blocks or ():
                    encoder_hidden_states, hidden_states = block(
                        hidden_states, encoder_hidden_states, *args, **kwargs
                    )
                hidden_states = hidden_states.reshape(-1).contiguous().reshape(original_hidden.shape)
                encoder_hidden_states = encoder_hidden_states.reshape(-1).contiguous().reshape(original_encoder.shape)
                return (
                    hidden_states,
                    encoder_hidden_states,
                    hidden_states - original_hidden,
                    encoder_hidden_states - original_encoder,
                )

        cached_blocks = torch.nn.ModuleList([
            CurrentFluxCachedBlocks(
                transformer.transformer_blocks,
                transformer.single_transformer_blocks,
                transformer=transformer,
                return_hidden_states_first=False,
            )
        ])
        original_forward = transformer.forward

        @functools.wraps(original_forward)
        def cached_forward(this, *args, **kwargs):
            with unittest.mock.patch.object(this, "transformer_blocks", cached_blocks), unittest.mock.patch.object(
                this, "single_transformer_blocks", torch.nn.ModuleList()
            ):
                return original_forward(*args, **kwargs)

        transformer.forward = cached_forward.__get__(transformer)
        transformer._is_cached = True

    def _configure_pipe_adapter(self, adapter_name, *, cache_threshold=0.12, cache_downsample=1, cache_warmup=0):
        adapter_name = (adapter_name or "none").replace("_", "-").lower()
        if adapter_name in ("", "none", "off"):
            return "none"
        if adapter_name not in ("first-block-cache", "teacache", "para-attn", "atlas-xframe-cache", "xframe-cache"):
            raise ValueError(f"unknown atlas adapter {adapter_name!r}")
        canonical = "atlas-xframe-cache" if adapter_name in ("atlas-xframe-cache", "xframe-cache") else "first-block-cache"
        config = {
            "adapter": canonical,
            "residual_diff_threshold": float(cache_threshold),
            "downsample_factor": int(cache_downsample),
            "warmup_steps": int(cache_warmup),
        }
        if self.pipe_adapter_config is not None and self.pipe_adapter_config != config:
            raise RuntimeError(
                "first-block-cache is already applied with a different config; restart fluxd before changing it"
            )
        from para_attn.first_block_cache import utils as first_block_utils

        if not hasattr(first_block_utils, "_flux_original_get_can_use_cache"):
            first_block_utils._flux_original_get_can_use_cache = first_block_utils.get_can_use_cache

            def counted_get_can_use_cache(*args, **kwargs):
                result = first_block_utils._flux_original_get_can_use_cache(*args, **kwargs)
                stats = getattr(self, "first_block_cache_stats", None)
                if stats is not None:
                    stats["checks"] = int(stats.get("checks", 0)) + 1
                    if result:
                        stats["hits"] = int(stats.get("hits", 0)) + 1
                    else:
                        stats["misses"] = int(stats.get("misses", 0)) + 1
                return result

            first_block_utils.get_can_use_cache = counted_get_can_use_cache

        if not hasattr(first_block_utils.CacheContext, "_flux_original_get_buffer"):
            first_block_utils.CacheContext._flux_original_get_buffer = first_block_utils.CacheContext.get_buffer
            first_block_utils.CacheContext._flux_original_set_buffer = first_block_utils.CacheContext.set_buffer
            first_block_utils.CacheContext._flux_original_remove_buffer = first_block_utils.CacheContext.remove_buffer

            def atlas_buffer_name(ctx, name):
                step_keyed = getattr(ctx, "atlas_step_keyed_buffers", False)
                if not step_keyed or name not in (
                    "first_hidden_states_residual",
                    "hidden_states_residual",
                    "encoder_hidden_states_residual",
                ):
                    return name
                steps = max(1, int(getattr(ctx, "num_inference_steps", 1) or 1))
                step = max(0, int(ctx.get_current_step()) % steps)
                return f"{name}_step_{step}"

            def step_keyed_get_buffer(ctx, name):
                return ctx._flux_original_get_buffer(atlas_buffer_name(ctx, name))

            def step_keyed_set_buffer(ctx, name, buffer):
                return ctx._flux_original_set_buffer(atlas_buffer_name(ctx, name), buffer)

            def step_keyed_remove_buffer(ctx, name):
                return ctx._flux_original_remove_buffer(atlas_buffer_name(ctx, name))

            first_block_utils.CacheContext.get_buffer = step_keyed_get_buffer
            first_block_utils.CacheContext.set_buffer = step_keyed_set_buffer
            first_block_utils.CacheContext.remove_buffer = step_keyed_remove_buffer

        if canonical == "atlas-xframe-cache":
            self._apply_flux_cache_compat(first_block_utils)
        else:
            from para_attn.first_block_cache.diffusers_adapters import apply_cache_on_pipe

            apply_cache_on_pipe(
                self.pipe,
                residual_diff_threshold=config["residual_diff_threshold"],
                downsample_factor=config["downsample_factor"],
                warmup_steps=config["warmup_steps"],
            )
        self.pipe_adapter_config = config
        return canonical

    def status(self):
        return {
            "ok": True,
            "kind": self.kind,
            "loaded": self.pipe is not None,
            "img2img_loaded": self.img2img_pipe is not None,
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
            "model_family": str(payload.get("model_family") or payload.get("model") or "dev").lower(),
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
        with self.jobs_lock:
            self.jobs[job_id] = job
            self._write_jobs()
        threading.Thread(target=self._run_job, args=(job_id,), daemon=True).start()
        return {"ok": True, "job": job}

    def submit_img2img(self, payload):
        job_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
        requested_backend = normalize_backend(payload.get("backend") or self.default_backend)
        if requested_backend == "auto":
            backend = "mps" if torch.backends.mps.is_available() else "cpu"
        elif requested_backend in ("mps", "cpu"):
            backend = requested_backend
        else:
            raise ValueError("img2img socket currently supports auto, mps, or cpu backends")
        image_path = pathlib.Path(str(payload.get("image") or payload.get("input") or "")).expanduser()
        if not image_path.exists():
            raise ValueError(f"input image not found: {image_path}")
        width = int(payload.get("width") or 0)
        height = int(payload.get("height") or 0)
        if width <= 0 or height <= 0:
            with Image.open(image_path) as probe:
                width, height = probe.size
        steps = _finite_int(payload.get("steps"), 28, 1, 120)
        strength = _finite_float(payload.get("strength"), 0.55, 0.01, 0.99)
        effective_steps = max(1, min(steps, int(math.ceil(steps * strength))))
        job = {
            "id": job_id,
            "kind": "img2img",
            "backend": backend,
            "requested_backend": requested_backend,
            "model_family": str(payload.get("model_family") or payload.get("model") or "dev").lower(),
            "status": "queued",
            "created": time.time(),
            "prompt": payload["prompt"],
            "image": str(image_path),
            "primary_image": str(payload.get("primary_image") or payload.get("primary") or image_path),
            "image2": str(payload.get("image2") or ""),
            "identity_image": str(payload.get("identity_image") or ""),
            "posture_image": str(payload.get("posture_image") or ""),
            "backdrop_image": str(payload.get("backdrop_image") or ""),
            "blend_image": str(payload.get("blend_image") or ""),
            "conditioning": str(payload.get("conditioning") or "single source image"),
            "width": width,
            "height": height,
            "steps": steps,
            "scheduled_steps": steps,
            "effective_steps": effective_steps,
            "guidance": float(payload.get("guidance", 5.0)),
            "strength": strength,
            "seed": payload.get("seed"),
            "filename": payload.get("filename"),
            "output": "",
            "error": "",
            "phase": "queued",
            "step": 0,
            "total_steps": effective_steps,
            "cancel_requested": False,
        }
        with self.jobs_lock:
            self.jobs[job_id] = job
            self._write_jobs()
        threading.Thread(target=self._run_job, args=(job_id,), daemon=True).start()
        return {"ok": True, "job": job}

    def submit_atlas(self, payload):
        draft = payload.get("draft") if isinstance(payload.get("draft"), dict) else {}
        requested_id = str(payload.get("id") or draft.get("id") or "").strip()
        job_id = requested_id or "spheremap_socket_" + time.strftime("%Y%m%d-%H%M%S") + "_" + uuid.uuid4().hex[:6]
        with self.jobs_lock:
            existing = self.jobs.get(job_id)
        if existing and existing.get("status") in ("queued", "running"):
            return {"ok": True, "job": existing, "already": True}

        requested_backend = normalize_backend(payload.get("backend") or self.default_backend)
        size = _finite_int(payload.get("size") or draft.get("size"), 384, 128, 2048)
        steps = _finite_int(payload.get("steps") or draft.get("steps"), 40, 1, 120)
        n_rows = _finite_int(draft.get("n_rows"), 1024, 1, 1_000_000)
        n_cols = _finite_int(draft.get("n_cols"), 64, 1, 1_000_000)
        grid_total = n_rows * n_cols
        n_latent = _finite_int(payload.get("n_latent") or draft.get("n_latent"), grid_total, 1, grid_total)
        index_start = _finite_int(payload.get("index_start"), int(draft.get("index_start") or 0), 0, n_latent)
        requested_end = payload.get("index_end") or draft.get("index_end") or n_latent
        index_end = _finite_int(requested_end, n_latent, index_start, n_latent)
        limit = int(payload.get("limit") or 0)
        if limit > 0:
            index_end = min(index_end, index_start + limit)
        render_count = _finite_int(payload.get("render_count") or draft.get("render_count"), 0, 0, n_latent)
        batch_size = _finite_int(payload.get("batch_size") or draft.get("batch_size"), 1, 1, 64)
        run_total = max(0, index_end - index_start)
        if render_count > 0:
            run_total = min(run_total, render_count)
        traversal_order = str(payload.get("traversal_order") or draft.get("traversal_order") or "column_serpentine")
        sample_mode = str(payload.get("sample_mode") or draft.get("sample_mode") or "contiguous").lower()
        adapter = str(payload.get("adapter") or draft.get("adapter") or "none").lower()
        cache_threshold = _finite_float(payload.get("cache_threshold") or draft.get("cache_threshold"), 0.12, 0.0, 1.0)
        cache_downsample = _finite_int(payload.get("cache_downsample") or draft.get("cache_downsample"), 1, 1, 64)
        cache_warmup = _finite_int(payload.get("cache_warmup") or draft.get("cache_warmup"), 0, 0, steps)
        out_dir = self.out_dir / "atlas" / f"{job_id}.sphere"
        job = {
            "id": job_id,
            "kind": "atlas_sphere",
            "backend": self.resolve_backend(requested_backend, {"width": size, "height": size, "steps": steps}),
            "requested_backend": requested_backend,
            "status": "queued",
            "created": time.time(),
            "prompt": str(payload.get("prompt") or draft.get("prompt") or "atlas sphere study"),
            "subject": str(draft.get("subject") or "atlas sphere"),
            "width": size,
            "height": size,
            "steps": steps,
            "guidance": _finite_float(payload.get("guidance") or draft.get("guidance"), 3.5, 0.0, 20.0),
            "seed": str(payload.get("seed") or draft.get("seed_a") or 7),
            "seed_b": int(draft.get("seed_b") or 23),
            "seed_c": int(draft.get("seed_c") or 51),
            "seed_d": int(draft.get("seed_d") or 89),
            "mode": str(draft.get("mode") or "omega"),
            "traversal": str(draft.get("traversal") or "spherical_outward"),
            "traversal_order": traversal_order,
            "sample_mode": sample_mode,
            "adapter": adapter,
            "cache_threshold": cache_threshold,
            "cache_downsample": cache_downsample,
            "cache_warmup": cache_warmup,
            "n_rows": n_rows,
            "n_cols": n_cols,
            "n_latent": n_latent,
            "grid_total": grid_total,
            "index_start": index_start,
            "index_end": index_end,
            "render_count": render_count,
            "batch_size": batch_size,
            "precision": "bf16",
            "atlas_total": run_total,
            "atlas_done": 0,
            "rates": draft.get("rates") or [],
            "offsets": draft.get("offsets") or [],
            "view_prompts": draft.get("view_prompts") or [],
            "shell_scale": _finite_float(draft.get("shell_scale"), 1.0, 0.01, 4.0),
            "shell_coupling": _finite_float(draft.get("shell_coupling"), 1.0, -16.0, 16.0),
            "seed_lock": _finite_float(draft.get("seed_lock"), 0.0, 0.0, 0.95),
            "output": str(out_dir),
            "error": "",
            "phase": "queued",
            "step": 0,
            "total_steps": max(1, run_total),
            "cancel_requested": False,
        }
        for key in ("arc", "amp", "spin", "base", "orbit"):
            if key in draft:
                job[key] = draft[key]
        with self.jobs_lock:
            self.jobs[job_id] = job
            self._write_jobs()
        threading.Thread(target=self._run_atlas_job, args=(job_id,), daemon=True).start()
        return {"ok": True, "job": job, "already": False}

    def resolve_backend(self, requested, job):
        requested = normalize_backend(requested)
        if requested != "auto":
            return requested
        caps = backend_capabilities(self.model_dir)
        candidates = []
        if caps.get("cuda"):
            candidates.append("cuda")
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
        if caps.get("cuda"):
            return "cuda"
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
            job["total_steps"] = int(job.get("effective_steps") or job["steps"])
            job["started"] = time.time()
            self._write_jobs()
            try:
                output = self._render(job)
                try:
                    output_rel = pathlib.Path(output).resolve().relative_to(self.out_dir.resolve())
                    access_url = "/outputs/" + output_rel.as_posix()
                except (OSError, ValueError):
                    access_url = ""
                job["piper_asset_ready"] = _publish_piper_asset(
                    job["id"], output, 0, 1, access_url=access_url, seed=job.get("seed")
                )
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

    def _run_atlas_job(self, job_id):
        with self.lock:
            job = self.jobs[job_id]
            if job.get("status") == "cancelled":
                self._write_jobs()
                return
            job["status"] = "running"
            job["phase"] = "loading_model"
            job["started"] = time.time()
            self._write_jobs()
            try:
                output = self._render_atlas(job)
                seconds = time.time() - float(job["started"])
                job["status"] = "done"
                job["phase"] = "done"
                job["step"] = int(job["total_steps"])
                job["output"] = str(output)
                job["seconds"] = seconds
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
                if self.pipe_adapter_config is not None:
                    self._discard_pipe_adapter()

    def _render(self, job):
        if job.get("kind") == "img2img":
            return self._render_img2img(job)
        backend = job.get("backend", "mps")
        if backend in ("cuda", "mps", "cpu"):
            return self._render_torch(job, backend)
        if backend == "mlx":
            return self._render_mlx(job)
        if backend == "coreml":
            return self._render_coreml(job)
        if backend == "ane":
            return self._render_ane(job)
        raise ValueError(f"unknown backend {backend!r}")

    def _render_img2img(self, job):
        backend = job.get("backend", "mps")
        if backend == "cpu":
            self.device = "cpu"
        else:
            self.device = "mps" if torch.backends.mps.is_available() else "cpu"
        self._load_img2img_pipe(self.device)
        job["phase"] = "loading_image"
        self._write_jobs()
        image = Image.open(job["image"]).convert("RGB")
        width = int(job["width"])
        height = int(job["height"])
        if image.size != (width, height):
            image = image.resize((width, height), Image.Resampling.LANCZOS)
        generator = None
        if job["seed"] not in (None, ""):
            generator = torch.Generator(device="cpu").manual_seed(int(job["seed"]))
        effective_steps = max(1, min(int(job["steps"]), int(math.ceil(int(job["steps"]) * float(job["strength"])))))
        job["phase"] = "sampling"
        job["step"] = 0
        job["total_steps"] = effective_steps
        job["effective_steps"] = effective_steps
        job["scheduled_steps"] = int(job["steps"])
        self._write_jobs()

        step_counter = {"n": 0}

        def on_step_end(_pipe, step, _timestep, callback_kwargs):
            if job.get("cancel_requested"):
                raise CancelledJob("job cancelled")
            step_counter["n"] += 1
            job["phase"] = "sampling"
            job["step"] = min(effective_steps, step_counter["n"])
            job["total_steps"] = effective_steps
            self._write_jobs()
            return callback_kwargs

        out_image = self.img2img_pipe(
            prompt=job["prompt"],
            image=image,
            width=width,
            height=height,
            strength=float(job["strength"]),
            guidance_scale=float(job["guidance"]),
            num_inference_steps=int(job["steps"]),
            generator=generator,
            callback_on_step_end=on_step_end,
        ).images[0]
        job["phase"] = "saving"
        self._write_jobs()
        output = self._output_path(job)
        out_image.save(output)
        return output

    def _render_atlas(self, job):
        backend = job.get("backend", "mps")
        if backend not in ("cuda", "mps", "cpu"):
            raise RuntimeError("atlas sphere supports cuda/mps/cpu socket backends only")
        if backend == "cpu":
            self.device = "cpu"
        elif backend == "cuda":
            if not torch.cuda.is_available():
                raise RuntimeError("atlas sphere requested cuda, but PyTorch cannot see a CUDA GPU")
            self.device = "cuda"
        else:
            self.device = "mps" if torch.backends.mps.is_available() else "cpu"
        requested_adapter = str(job.get("adapter") or "none").replace("_", "-").lower()
        if requested_adapter in ("", "none", "off") and self.pipe_adapter_config is not None:
            self._discard_pipe_adapter()
        self._load_pipe(self.device)

        out_dir = pathlib.Path(job["output"])
        out_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = out_dir / "manifest.json"
        progress_path = out_dir / "progress.json"
        size = int(job["width"])
        steps = int(job["steps"])
        n_rows = int(job["n_rows"])
        n_cols = int(job["n_cols"])
        index_start = int(job["index_start"])
        index_end = int(job["index_end"])
        range_total = max(0, index_end - index_start)
        mode = str(job.get("mode") or "omega")
        traversal = str(job.get("traversal") or "spherical_outward")
        traversal_order = str(job.get("traversal_order") or "column_serpentine")
        adapter_name = str(job.get("adapter") or "none").lower()
        cache_threshold = float(job.get("cache_threshold") or 0.12)
        cache_downsample = max(1, int(job.get("cache_downsample") or 1))
        cache_warmup = max(0, int(job.get("cache_warmup") or 0))
        coupling = float(job.get("shell_coupling") or 1.0)
        nc = int(self.pipe.transformer.config.in_channels) // 4
        dtype = torch.bfloat16
        render_started = time.time()
        self.first_block_cache_stats = None

        def mk(seed):
            generator = torch.Generator(device="cpu").manual_seed(int(seed))
            latents, _ = self.pipe.prepare_latents(1, nc, size, size, dtype, self.device, generator)
            return latents.detach().cpu().float()

        raw = [mk(job.get("seed") or 7), mk(job.get("seed_b") or 23), mk(job.get("seed_c") or 51), mk(job.get("seed_d") or 89)]
        flat = [x.flatten() for x in raw]
        radius = float(flat[0].norm())
        basis = [x.to(self.device) for x in _gram_schmidt(flat)]
        shape = raw[0].shape
        render_order = _atlas_render_order(index_start, index_end, n_rows, n_cols, traversal_order)
        render_count = int(job.get("render_count") or 0)
        sample_mode = str(job.get("sample_mode") or "contiguous")
        render_order = _atlas_sample_order(render_order, render_count, sample_mode)
        total = len(render_order)
        order_summary = _atlas_order_delta_summary(render_order, n_rows, n_cols, traversal, coupling)
        def prompt_for_cell(row, col):
            view_prompts = job.get("view_prompts") if isinstance(job.get("view_prompts"), list) else []
            view_prompts = [str(x).strip() for x in view_prompts if str(x).strip()]
            if not view_prompts:
                return job["prompt"]
            bucket = min(len(view_prompts) - 1, int((col / max(1, n_cols)) * len(view_prompts)))
            return view_prompts[bucket]

        encode_started = time.time()
        prompt_cache = {}
        prompts_to_encode = [str(job["prompt"])]
        if isinstance(job.get("view_prompts"), list):
            prompts_to_encode.extend(str(x).strip() for x in job.get("view_prompts") if str(x).strip())
        for prompt_text in dict.fromkeys(prompts_to_encode):
            prompt_cache[prompt_text] = self.pipe.encode_prompt(
                prompt=prompt_text,
                device=self.device,
                num_images_per_prompt=1,
                max_sequence_length=512,
            )[:2]
        prompt_encode_seconds = time.time() - encode_started
        if adapter_name.replace("_", "-") in ("first-block-cache", "teacache", "para-attn", "atlas-xframe-cache", "xframe-cache"):
            self.first_block_cache_stats = {"checks": 0, "hits": 0, "misses": 0}
            adapter_name = self._configure_pipe_adapter(
                adapter_name,
                cache_threshold=cache_threshold,
                cache_downsample=cache_downsample,
                cache_warmup=cache_warmup,
            )
        elif adapter_name not in ("", "none", "off"):
            raise ValueError(f"unknown atlas adapter {adapter_name!r}")

        manifest = {
            "kind": "atlas_sphere",
            "job_id": job["id"],
            "subject": job.get("subject"),
            "prompt": job.get("prompt"),
            "seed_a": int(job.get("seed") or 7),
            "n_rows": n_rows,
            "n_cols": n_cols,
            "n_latent": int(job.get("n_latent") or n_rows * n_cols),
            "grid_total": int(job.get("grid_total") or n_rows * n_cols),
            "index_start": index_start,
            "index_end": index_end,
            "range_total": range_total,
            "render_count": render_count,
            "batch_size": int(job.get("batch_size") or 1),
            "precision": "bf16",
            "render_total": total,
            "sample_mode": sample_mode,
            "size": size,
            "steps": steps,
            "mode": mode,
            "traversal": traversal,
            "traversal_order": traversal_order,
            "traversal_order_summary": order_summary,
            "adapter": adapter_name,
            "cache_threshold": cache_threshold,
            "cache_downsample": cache_downsample,
            "cache_warmup": cache_warmup,
            "prompt_embedding_cached": True,
            "prompt_cache_size": len(prompt_cache),
            "view_prompt_buckets": len(job.get("view_prompts") or []),
            "prompt_encode_seconds": prompt_encode_seconds,
            "shell_scale": job.get("shell_scale"),
            "shell_coupling": coupling,
            "seed_lock": job.get("seed_lock"),
            "rates": job.get("rates") or [],
            "offsets": job.get("offsets") or [],
            "out_dir": str(out_dir),
        }
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

        def cell_path(i):
            return out_dir / f"cell_{i:05d}.png"

        def write_progress(done, current_index):
            elapsed = max(0.0, time.time() - render_started)
            rendered_this_run = max(0, done - existing)
            cells_per_second = rendered_this_run / elapsed if elapsed > 0 and rendered_this_run > 0 else 0.0
            remaining = max(0, total - done)
            cache_stats = self.first_block_cache_stats or {}
            cache_checks = int(cache_stats.get("checks", 0))
            cache_hits = int(cache_stats.get("hits", 0))
            job["atlas_done"] = done
            job["step"] = done
            job["total_steps"] = max(1, total)
            job["phase"] = "sampling"
            job["cells_per_hour"] = cells_per_second * 3600
            job["eta_seconds"] = remaining / cells_per_second if cells_per_second > 0 else 0
            if cache_checks:
                job["cache_checks"] = cache_checks
                job["cache_hits"] = cache_hits
                job["cache_misses"] = int(cache_stats.get("misses", 0))
                job["cache_hit_rate"] = cache_hits / cache_checks
            progress = {
                "job_id": job["id"],
                "current": done,
                "total": total,
                "current_index": current_index,
                "elapsed_seconds": elapsed,
                "cells_per_hour": cells_per_second * 3600,
                "eta_seconds": remaining / cells_per_second if cells_per_second > 0 else 0,
                "last_cell_seconds": job.get("last_cell_seconds", 0),
                "last_cell_role": job.get("last_cell_role", ""),
                "last_cell_steps": job.get("last_cell_steps", 0),
                "adapter": adapter_name,
                "cache_checks": cache_checks,
                "cache_hits": cache_hits,
                "cache_misses": int(cache_stats.get("misses", 0)),
                "cache_hit_rate": cache_hits / cache_checks if cache_checks else 0.0,
                "order": traversal_order,
                "ts": time.time(),
            }
            progress_path.write_text(json.dumps(progress, indent=2, sort_keys=True) + "\n")
            self._write_jobs()

        existing = sum(1 for i in render_order if cell_path(i).exists())
        write_progress(existing, index_start - 1)
        done = existing
        previous_prompt_text = None
        xframe_cache_context = None
        xframe_cache_utils = None
        if adapter_name == "atlas-xframe-cache":
            from para_attn.first_block_cache import utils as xframe_cache_utils

            xframe_cache_context = xframe_cache_utils.create_cache_context(
                residual_diff_threshold=cache_threshold,
                downsample_factor=cache_downsample,
                warmup_steps=cache_warmup,
                num_inference_steps=steps,
            )
            xframe_cache_context.atlas_step_keyed_buffers = True
        for order_pos, i in enumerate(render_order):
            if job.get("cancel_requested"):
                raise CancelledJob("atlas job cancelled")
            if cell_path(i).exists():
                continue
            row = i // n_cols
            col = i % n_cols
            prompt_text = prompt_for_cell(row, col)
            prompt_embeds, pooled_prompt_embeds = prompt_cache[prompt_text]
            prompt_changed = previous_prompt_text is not None and prompt_text != previous_prompt_text
            theta, azimuth = _sphere_probe(row, col, n_rows, n_cols, traversal, coupling)
            latents = _atlas_latent(mode, theta, azimuth, basis, radius, shape, dtype, job)
            cell_steps = steps
            cell_role = adapter_name if adapter_name != "none" else "anchor"
            cell_started = time.time()
            cache_before = dict(self.first_block_cache_stats or {})

            def on_step_end(_pipe, step, _timestep, callback_kwargs):
                if job.get("cancel_requested"):
                    raise CancelledJob("atlas job cancelled")
                job["phase"] = f"cell {i}"
                job["cell_step"] = int(step) + 1
                job["cell_total_steps"] = cell_steps
                return callback_kwargs

            def run_pipe_call():
                return self.pipe(
                    prompt=None,
                    width=size,
                    height=size,
                    guidance_scale=float(job["guidance"]),
                    num_inference_steps=cell_steps,
                    latents=latents,
                    prompt_embeds=prompt_embeds,
                    pooled_prompt_embeds=pooled_prompt_embeds,
                    callback_on_step_end=on_step_end,
                ).images[0]

            if xframe_cache_context is not None:
                if prompt_changed:
                    xframe_cache_context.clear_buffers()
                xframe_cache_context.executed_steps = 0
                xframe_cache_context.num_inference_steps = cell_steps
                xframe_cache_context.reset_incremental_names()
                with xframe_cache_utils.cache_context(xframe_cache_context):
                    image = run_pipe_call()
            else:
                image = run_pipe_call()
            job["phase"] = "saving"
            self._write_jobs()
            image.save(cell_path(i))
            job["piper_asset_ready"] = _publish_piper_asset(job["id"], cell_path(i), i, total)
            job["last_cell_seconds"] = time.time() - cell_started
            job["last_cell_role"] = cell_role
            job["last_cell_steps"] = cell_steps
            if self.first_block_cache_stats is not None:
                checks_before = int(cache_before.get("checks", 0))
                hits_before = int(cache_before.get("hits", 0))
                checks_after = int(self.first_block_cache_stats.get("checks", 0))
                hits_after = int(self.first_block_cache_stats.get("hits", 0))
                job["last_cell_cache_checks"] = checks_after - checks_before
                job["last_cell_cache_hits"] = hits_after - hits_before
                job["last_cell_cache_hit_rate"] = (
                    (hits_after - hits_before) / (checks_after - checks_before)
                    if checks_after > checks_before
                    else 0.0
                )
            previous_prompt_text = prompt_text
            done += 1
            write_progress(done, i)
        try:
            progress_path.unlink()
        except FileNotFoundError:
            pass
        manifest["rendered"] = sum(1 for i in render_order if cell_path(i).exists())
        manifest["adapter_counts"] = {"anchors": done, "adapted": 0}
        if self.first_block_cache_stats is not None:
            cache_checks = int(self.first_block_cache_stats.get("checks", 0))
            cache_hits = int(self.first_block_cache_stats.get("hits", 0))
            manifest["cache_stats"] = {
                "checks": cache_checks,
                "hits": cache_hits,
                "misses": int(self.first_block_cache_stats.get("misses", 0)),
                "hit_rate": cache_hits / cache_checks if cache_checks else 0.0,
            }
        manifest["finished"] = time.time()
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        return out_dir

    def _render_torch(self, job, backend):
        if backend == "cpu":
            self.device = "cpu"
        elif backend == "cuda":
            self.device = "cuda"
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
        ]
        model_family = str(job.get("model_family") or "dev").replace("_", "-").lower()
        if model_family in ("schnell", "flux.1-schnell", "flux1-schnell"):
            cmd.append("schnell")
        else:
            cmd.extend([str(self.model_dir), "--base-model", "dev"])
        cmd.extend([
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
        ])
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
        with self.jobs_lock:
            return {"ok": True, "jobs": [dict(job) for job in self.jobs.values()]}

    def list_profile(self):
        return {"ok": True, "profile": self.profile, "backends": backend_capabilities(self.model_dir)}

    def cancel(self, job_id):
        with self.jobs_lock:
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
        with self.jobs_lock:
            jobs = list(self.jobs.values())
        removable = [job for job in jobs if job.get("status") in statuses]
        removable.sort(key=lambda job: float(job.get("created") or job.get("finished") or 0), reverse=True)
        keep_ids = {job["id"] for job in removable[:keep] if "id" in job}
        removed = []
        for job in removable[keep:]:
            job_id = job.get("id")
            if job_id and job_id not in keep_ids:
                removed.append(job_id)
                with self.jobs_lock:
                    self.jobs.pop(job_id, None)
        if removed:
            self._write_jobs()
        with self.jobs_lock:
            jobs = [dict(job) for job in self.jobs.values()]
        return {"ok": True, "removed": removed, "jobs": jobs}


def normalize_backend(value):
    value = (value or "auto").strip().lower()
    if value not in {"auto", "cuda", "mps", "mlx", "coreml", "ane", "cpu"}:
        raise ValueError(f"unknown backend {value!r}")
    return value


def choose_torch_device(backend):
    if backend == "cpu":
        return "cpu"
    if backend == "cuda":
        return "cuda"
    if backend == "auto" and torch.cuda.is_available():
        return "cuda"
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
        "cuda": torch.cuda.is_available(),
        "cuda_device_count": torch.cuda.device_count() if torch.cuda.is_available() else 0,
        "cuda_device": torch.cuda.get_device_name(0) if torch.cuda.is_available() and torch.cuda.device_count() else "",
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
    worker = Worker(args.model_dir, args.out_dir, args.state, profile_path=args.profile, backend=args.backend, preload=args.preload, kind=args.kind)
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
            try:
                line = conn.makefile().readline()
                if not line.strip():
                    continue
                req = json.loads(line)
                if not isinstance(req, dict):
                    raise ValueError("request must be a JSON object")
                op = req.get("op")
                if op == "ping":
                    resp = worker.status()
                elif op == "warm":
                    worker._load_pipe()
                    resp = worker.status()
                elif op == "submit":
                    resp = worker.submit(req)
                elif op == "submit_img2img":
                    resp = worker.submit_img2img(req)
                elif op == "atlas_sphere":
                    resp = worker.submit_atlas(req)
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
            except Exception as exc:
                resp = {"ok": False, "error": repr(exc)}
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
    parser.add_argument("--backend", choices=["auto", "cuda", "mps", "mlx", "coreml", "ane", "cpu"], default=os.environ.get("FLUX_BACKEND", "auto"))
    parser.add_argument("--kind", choices=["flux", "img2img"], default="flux")
    parser.add_argument("--preload", action="store_true")
    serve(parser.parse_args())


if __name__ == "__main__":
    main()
