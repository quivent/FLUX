#!/home/dev/venv/bin/python
"""FLUX inference daemon: keeps the pipeline resident so a render costs only its
own steps, not a fresh ~60 s model load.

  POST /generate  {"prompt": "...", "steps": 28, "num": 1, ...}
                  -> {"images": [{path, seed, seconds, url}, ...]}
  POST /generate?return=png  -> the PNG bytes of the first image
  POST /interp    {"prompt_a": "...", "seed_a": 1, "seed_b": 2, "n": 5, ...}
                  -> {"images": [{path, t, seconds, url}, ...]}
  GET  /image/<name>.png     -> a rendered PNG
  GET  /health               -> {"ready": true, "model": ..., "vram_gib": ...}

One GPU, so generation is serialized behind a lock; requests queue rather than
thrashing VRAM.
"""
import asyncio
import json
import math
import pathlib
import time
from typing import Literal

import torch
from diffusers.utils.torch_utils import randn_tensor
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

import fluxlib

app = FastAPI(title="fluxd")
_lock = asyncio.Lock()

# Interpolation paths are rendered in sub-batches: n points share step count,
# guidance and geometry, so they batch exactly like /batch does -- but the card
# already holds ~56 GiB of resident weights, so cap the width of a pass.
INTERP_CHUNK = 5


class EditReq(BaseModel):
    image: str                       # path on the node, or gen/key
    instruction: str
    steps: int = 28
    guidance: float = 2.5
    seed: int | None = None
    stem: str | None = None
    width: int | None = None
    height: int | None = None


class BatchItem(BaseModel):
    prompt: str
    seed: int | None = None
    stem: str | None = None


class BatchReq(BaseModel):
    items: list[BatchItem] = Field(min_length=1, max_length=16)
    steps: int | None = None
    guidance: float | None = None
    width: int = 1024
    height: int = 1024
    max_sequence_length: int = 512


class Req(BaseModel):
    prompt: str
    steps: int | None = None
    guidance: float | None = None
    width: int = 1024
    height: int = 1024
    seed: int | None = None
    num: int = Field(default=1, ge=1, le=16)
    negative: str | None = None
    stem: str | None = None
    max_sequence_length: int = 512


class InterpReq(BaseModel):
    prompt_a: str
    prompt_b: str | None = None      # None -> prompt_a at both ends
    seed_a: int
    seed_b: int
    steps: int = 28
    guidance: float | None = None
    width: int = 704
    height: int = 1056
    n: int = Field(default=5, ge=1, le=16)
    mode: Literal["latent", "prompt", "both"] = "latent"
    endpoints: bool = True           # include t=0 and t=1
    stem: str | None = None
    max_sequence_length: int = 512
    # "lerp" exists to demonstrate why it is wrong; the path you want is slerp.
    noise_interp: Literal["slerp", "lerp"] = "slerp"


# --------------------------------------------------------------------------
# interpolation
# --------------------------------------------------------------------------

# Near-parallel guard. Two independent Gaussians in ~185k dimensions have a
# cosine of 0 +- 1/sqrt(d) ~ 0.002, so this only fires when the caller asks for
# a path between a seed and itself (or a near-duplicate) -- exactly the case
# where sin(theta0) underflows and the slerp coefficients blow up to inf/NaN.
SLERP_DOT_THRESHOLD = 0.9995


def _slerp(t, a, b, threshold=SLERP_DOT_THRESHOLD):
    """Spherical linear interpolation between two noise tensors.

    Gaussian latents concentrate on a hypersphere shell of radius ~sqrt(d); a
    straight line between two of them cuts through the inside of the ball and
    at t=0.5 has norm ~1/sqrt(2) of the endpoints'. The denoiser reads that
    shrunken norm as a lower-variance start and returns washed-out, low-contrast
    mush. Slerp walks the great circle instead, so every point on the path has
    the endpoints' norm.

    Returns (interpolated, cos) -- cos is reported so a caller can see whether
    the parallel guard fired.
    """
    # bf16 has 8 mantissa bits; acos/sin of a near-zero cosine needs more.
    a32, b32 = a.float(), b.float()
    fa, fb = a32.reshape(-1), b32.reshape(-1)
    cos = torch.dot(fa, fb) / (fa.norm() * fb.norm() + 1e-12)
    cos = float(cos.clamp(-1.0, 1.0))

    if abs(cos) > threshold:
        # The great circle is degenerate here and the coefficients divide by a
        # vanishing sin(theta0). The chord and the arc agree to within the
        # guard's tolerance anyway, so lerp is both safe and accurate.
        out = a32 + (b32 - a32) * t
    else:
        theta0 = math.acos(cos)
        sin0 = math.sin(theta0)
        theta = theta0 * t
        out = (math.sin(theta0 - theta) / sin0) * a32 + (math.sin(theta) / sin0) * b32
    return out.to(a.dtype), cos


def _lerp(t, a, b):
    """Straight-line interpolation -- correct for TEXT embeddings.

    The noise tensor and the text embedding want different interpolations for
    the same reason: only one of them is a sample from an isotropic Gaussian.
    Noise is, so its norm is the whole signal and must be preserved (slerp).
    T5/CLIP embeddings are not samples from anything spherical -- they are
    learned features the transformer consumes additively, where the meaningful
    path between two conditionings is the straight one and the magnitude
    carries conditioning strength. Slerping them would renormalise every
    midpoint onto the endpoints' norm and distort exactly that.
    """
    return (a.float() + (b.float() - a.float()) * t).to(a.dtype)


def _flux_noise(pipe, seed, width, height, dtype, device):
    """One endpoint's initial noise, packed into FLUX's transformer layout.

    FLUX does not take a plain 4-channel image latent: prepare_latents samples
    (B, 16, h, w) and then _pack_latents folds each 2x2 spatial block into the
    channel axis, giving (B, h/2 * w/2, 64) -- a sequence of patch tokens. When
    `latents=` is passed to the pipeline, prepare_latents returns it UNTOUCHED
    (it only packs what it sampled itself), so what we hand over must already be
    packed. Packing is a reshape + permute, i.e. a coordinate permutation, so
    slerp commutes with it -- interpolating packed and unpacked give the same
    tensor. We pack first and interpolate in the layout the pipeline wants.
    """
    vsf = pipe.vae_scale_factor
    lh = 2 * (int(height) // (vsf * 2))
    lw = 2 * (int(width) // (vsf * 2))
    ch = pipe.transformer.config.in_channels // 4
    # Seeded per endpoint, so the same (seed_a, seed_b) always walks the same path.
    gen = torch.Generator(device=device).manual_seed(int(seed))
    lat = randn_tensor((1, ch, lh, lw), generator=gen, device=torch.device(device), dtype=dtype)
    return pipe._pack_latents(lat, 1, ch, lh, lw)


def _t_values(n, endpoints):
    if endpoints:
        return [0.0] if n == 1 else [i / (n - 1) for i in range(n)]
    # Interior only: n points strictly between the two peaks.
    return [(i + 1) / (n + 1) for i in range(n)]


def interpolate(p: dict):
    """Render a path of `n` points between two seeds (and optionally two prompts).

    Returns /batch-shaped result dicts, each carrying its `t`.
    """
    pipe = fluxlib.load()
    d = fluxlib.defaults()
    steps = d["steps"] if p.get("steps") is None else p["steps"]
    guidance = d["guidance"] if p.get("guidance") is None else p["guidance"]
    width, height = p["width"], p["height"]
    mode, msl = p["mode"], p["max_sequence_length"]
    device = pipe._execution_device
    out_dir = pathlib.Path(fluxlib.OUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    prompt_a = p["prompt_a"]
    prompt_b = p.get("prompt_b") or prompt_a

    # Encode once, reuse for every point on the path -- this is exactly what the
    # pipeline does internally, just hoisted so we can interpolate the result.
    pe_a, ppe_a, _ = pipe.encode_prompt(
        prompt=prompt_a, prompt_2=None, device=device,
        num_images_per_prompt=1, max_sequence_length=msl,
    )
    if prompt_b == prompt_a:
        pe_b, ppe_b = pe_a, ppe_a
    else:
        pe_b, ppe_b, _ = pipe.encode_prompt(
            prompt=prompt_b, prompt_2=None, device=device,
            num_images_per_prompt=1, max_sequence_length=msl,
        )

    dtype = pe_a.dtype
    noise_a = _flux_noise(pipe, p["seed_a"], width, height, dtype, device)
    noise_b = _flux_noise(pipe, p["seed_b"], width, height, dtype, device)

    ts = _t_values(p["n"], p["endpoints"])
    do_latent = mode in ("latent", "both")
    do_prompt = mode in ("prompt", "both")
    noise_interp = p.get("noise_interp", "slerp")

    lats, pes, ppes, cos = [], [], [], None
    for t in ts:
        if do_latent:
            if noise_interp == "lerp":
                lat = _lerp(t, noise_a, noise_b)
            else:
                lat, cos = _slerp(t, noise_a, noise_b)
        else:
            lat = noise_a
        lats.append(lat)
        if do_prompt:
            pes.append(_lerp(t, pe_a, pe_b))
            ppes.append(_lerp(t, ppe_a, ppe_b))
        else:
            pes.append(pe_a)
            ppes.append(ppe_a)

    guard_fired = cos is not None and abs(cos) > SLERP_DOT_THRESHOLD
    stem = p.get("stem") or f"interp-{int(time.time())}-{p['seed_a']}-{p['seed_b']}"

    results = []
    for start in range(0, len(ts), INTERP_CHUNK):
        sl = slice(start, start + INTERP_CHUNK)
        lat_c = torch.cat(lats[sl], dim=0)
        pe_c = torch.cat(pes[sl], dim=0)
        ppe_c = torch.cat(ppes[sl], dim=0)
        t0 = time.time()
        images = pipe(
            prompt_embeds=pe_c,
            pooled_prompt_embeds=ppe_c,
            latents=lat_c,
            num_inference_steps=steps,
            guidance_scale=guidance,
            width=width,
            height=height,
            max_sequence_length=msl,
        ).images
        elapsed = time.time() - t0
        per = elapsed / max(1, len(images))
        print(
            f"[fluxd] interp chunk of {len(images)} in {elapsed:.1f}s "
            f"({per:.1f}s/image); peak {torch.cuda.max_memory_allocated() / 2**30:.1f} GiB",
            flush=True,
        )
        for k, image in enumerate(images):
            i = start + k
            t = ts[i]
            path = out_dir / f"{stem}-{i:02d}.png"
            meta = {
                "model": fluxlib.MODEL_ID,
                "prompt": prompt_a if not do_prompt else f"{prompt_a} || {prompt_b}",
                "prompt_a": prompt_a,
                "prompt_b": prompt_b,
                "t": round(t, 6),
                "mode": mode,
                "noise_interp": noise_interp if do_latent else None,
                "seed_a": p["seed_a"],
                "seed_b": p["seed_b"],
                # Keep the /batch key so existing consumers still find a seed.
                "seed": p["seed_a"] if t < 0.5 else p["seed_b"],
                "steps": steps,
                "guidance": guidance,
                "width": width,
                "height": height,
                "seconds": round(per, 2),
                "batch_size": len(images),
                "batch_seconds": round(elapsed, 2),
                "endpoint_cos": None if cos is None else round(cos, 6),
                "slerp_guard_fired": guard_fired,
            }
            image.save(path, pnginfo=fluxlib._pnginfo(meta))
            path.with_suffix(".json").write_text(json.dumps(meta, indent=2))
            results.append({"path": str(path), **meta})
    fluxlib.release()
    return results


@app.on_event("startup")
def _warm():
    fluxlib.load()


@app.get("/health")
def health():
    return {
        "ready": fluxlib._pipe is not None,
        "model": fluxlib.MODEL_ID,
        "defaults": fluxlib.defaults(),
        "kontext_ready": fluxlib._kontext is not None,
        "vram_gib": round(torch.cuda.memory_allocated() / 2**30, 2),
        "vram_reserved_gib": round(torch.cuda.memory_reserved() / 2**30, 2),
        "vram_free_gib": round(torch.cuda.mem_get_info()[0] / 2**30, 2),
        "out_dir": str(fluxlib.OUT_DIR),
    }


@app.post("/generate")
async def generate(req: Req, return_: str = Query("json", alias="return")):
    async with _lock:
        results = await asyncio.to_thread(
            fluxlib.generate,
            req.prompt,
            steps=req.steps,
            guidance=req.guidance,
            width=req.width,
            height=req.height,
            seed=req.seed,
            num=req.num,
            negative_prompt=req.negative,
            max_sequence_length=req.max_sequence_length,
            stem=req.stem,
        )
    if return_ == "png":
        return FileResponse(results[0]["path"], media_type="image/png")
    for r in results:
        r["url"] = f"/image/{pathlib.Path(r['path']).name}"
    return JSONResponse({"images": results})


@app.post("/batch")
async def batch(req: BatchReq):
    """One forward pass for every item -- the whole collection in a single call."""
    async with _lock:
        results = await asyncio.to_thread(
            fluxlib.generate_batch,
            [i.model_dump() for i in req.items],
            steps=req.steps,
            guidance=req.guidance,
            width=req.width,
            height=req.height,
            max_sequence_length=req.max_sequence_length,
        )
    for r in results:
        r["url"] = f"/image/{pathlib.Path(r['path']).name}"
    return JSONResponse({"images": results})


@app.post("/interp")
async def interp(req: InterpReq):
    """Walk the latent (and/or prompt) space between two rolls.

    A seed is a point, not a knob: two good seeds have a whole path between them
    that no prompt can address directly. This renders `n` points along it.
    """
    async with _lock:
        results = await asyncio.to_thread(interpolate, req.model_dump())
    for r in results:
        r["url"] = f"/image/{pathlib.Path(r['path']).name}"
    return JSONResponse({"images": results})


@app.post("/edit")
async def edit(req: EditReq):
    """Kontext: change one thing about an existing image, keep the rest."""
    src = pathlib.Path(req.image)
    if not src.is_file():
        raise HTTPException(404, f"no such image: {req.image}")
    async with _lock:
        result = await asyncio.to_thread(
            fluxlib.edit,
            str(src),
            req.instruction,
            steps=req.steps,
            guidance=req.guidance,
            seed=req.seed,
            stem=req.stem,
            width=req.width,
            height=req.height,
        )
    result["url"] = f"/image/{pathlib.Path(result['path']).name}"
    return JSONResponse(result)


@app.get("/image/{name}")
def image(name: str):
    # Resolve inside OUT_DIR so a crafted name cannot walk out of it.
    path = (fluxlib.OUT_DIR / name).resolve()
    if fluxlib.OUT_DIR.resolve() not in path.parents or not path.is_file():
        raise HTTPException(404, "no such image")
    return FileResponse(path, media_type="image/png")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="info")
