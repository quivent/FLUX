"""Shared FLUX pipeline loader. Weights stay resident on the H100; no ComfyUI."""
import json
import os
import pathlib
import time

import torch
from diffusers import FluxPipeline

MODEL_ID = os.environ.get("FLUX_MODEL", "black-forest-labs/FLUX.1-dev")
KONTEXT_ID = os.environ.get("FLUX_KONTEXT", "black-forest-labs/FLUX.1-Kontext-dev")
OUT_DIR = pathlib.Path(os.environ.get("FLUX_OUT", "/home/dev/out"))

# The original-layout checkpoints: ~24 GiB duplicating the diffusers shards, so
# download.py skips them. Anything resolving the cache must skip them too --
# otherwise the hub calls the snapshot incomplete and won't hand back the path.
SKIP_PATTERNS = ["flux1-*.safetensors", "ae.safetensors", "*.gguf", "*.onnx"]

# FLUX.1-dev is guidance-distilled; schnell is timestep-distilled and wants
# very few steps with no CFG. Defaults follow whichever model is loaded.
DEFAULTS = {
    "black-forest-labs/FLUX.1-dev": {"steps": 28, "guidance": 3.5},
    "black-forest-labs/FLUX.1-schnell": {"steps": 4, "guidance": 0.0},
}

_pipe = None
_kontext = None


def defaults(model_id=MODEL_ID):
    return DEFAULTS.get(model_id, {"steps": 28, "guidance": 3.5})


def resolve(model_id=MODEL_ID):
    """Repo id -> the local snapshot directory, when it is already cached.

    diffusers' own offline fallback still tries to reach the Hub for repo
    metadata and gives up when it can't, so resolve the path ourselves and hand
    from_pretrained a plain directory. Uncached ids pass through unchanged.
    """
    if os.path.isdir(model_id):
        return model_id
    try:
        from huggingface_hub import snapshot_download

        return snapshot_download(
            model_id, local_files_only=True, ignore_patterns=SKIP_PATTERNS
        )
    except Exception:
        return model_id


def load(model_id=MODEL_ID):
    """Load the pipeline once, fully on GPU. 80 GB fits bf16 FLUX with room to spare."""
    global _pipe
    if _pipe is not None:
        return _pipe
    t0 = time.time()
    pipe = FluxPipeline.from_pretrained(resolve(model_id), torch_dtype=torch.bfloat16)
    pipe.to("cuda")
    pipe.set_progress_bar_config(disable=True)
    _pipe = pipe
    print(
        f"[fluxlib] loaded {model_id} in {time.time() - t0:.1f}s; "
        f"{torch.cuda.memory_allocated() / 2**30:.1f} GiB resident",
        flush=True,
    )
    return _pipe


def generate(
    prompt,
    steps=None,
    guidance=None,
    width=1024,
    height=1024,
    seed=None,
    num=1,
    negative_prompt=None,
    max_sequence_length=512,
    model_id=MODEL_ID,
    out_dir=None,
    stem=None,
):
    """Render `num` images and write them as PNGs. Returns a list of result dicts."""
    pipe = load(model_id)
    d = defaults(model_id)
    steps = d["steps"] if steps is None else steps
    guidance = d["guidance"] if guidance is None else guidance
    out_dir = pathlib.Path(out_dir or OUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    if seed is None:
        seed = int.from_bytes(os.urandom(4), "big")
    stem = stem or f"flux-{int(time.time())}-{seed}"

    results = []
    for i in range(num):
        s = seed + i
        gen = torch.Generator("cuda").manual_seed(s)
        t0 = time.time()
        kwargs = dict(
            prompt=prompt,
            num_inference_steps=steps,
            guidance_scale=guidance,
            width=width,
            height=height,
            generator=gen,
            max_sequence_length=max_sequence_length,
        )
        # Plain FluxPipeline is guidance-distilled and has no true-CFG path, so
        # only pass a negative prompt to a pipeline that actually accepts one.
        if negative_prompt and "negative_prompt" in pipe.__call__.__code__.co_varnames:
            kwargs["negative_prompt"] = negative_prompt
        image = pipe(**kwargs).images[0]
        elapsed = time.time() - t0

        path = out_dir / (f"{stem}.png" if num == 1 else f"{stem}-{i:02d}.png")
        meta = {
            "model": model_id,
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "steps": steps,
            "guidance": guidance,
            "width": width,
            "height": height,
            "seed": s,
            "seconds": round(elapsed, 2),
        }
        image.save(path, pnginfo=_pnginfo(meta))
        path.with_suffix(".json").write_text(json.dumps(meta, indent=2))
        print(f"[fluxlib] {path}  {elapsed:.1f}s  seed={s}", flush=True)
        results.append({"path": str(path), **meta})
    release()
    return results


def generate_batch(
    items,
    steps=None,
    guidance=None,
    width=1024,
    height=1024,
    max_sequence_length=512,
    model_id=MODEL_ID,
    out_dir=None,
):
    """Render many prompts in ONE forward pass.

    `items` is a list of {prompt, seed?, stem?}. Every image in a batch shares
    step count, guidance and dimensions -- that is what makes them batchable --
    but each keeps its own seed, because diffusers accepts one generator per
    batch element. Returns result dicts in the order given.
    """
    pipe = load(model_id)
    d = defaults(model_id)
    steps = d["steps"] if steps is None else steps
    guidance = d["guidance"] if guidance is None else guidance
    out_dir = pathlib.Path(out_dir or OUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    prompts, gens, seeds = [], [], []
    for i, it in enumerate(items):
        seed = it.get("seed")
        if seed is None:
            seed = int.from_bytes(os.urandom(4), "big")
        seeds.append(seed)
        prompts.append(it["prompt"])
        gens.append(torch.Generator("cuda").manual_seed(seed))

    t0 = time.time()
    images = pipe(
        prompt=prompts,
        num_inference_steps=steps,
        guidance_scale=guidance,
        width=width,
        height=height,
        generator=gens,
        max_sequence_length=max_sequence_length,
    ).images
    elapsed = time.time() - t0
    per = elapsed / max(1, len(images))
    print(
        f"[fluxlib] batch of {len(images)} in {elapsed:.1f}s ({per:.1f}s/image); "
        f"peak {torch.cuda.max_memory_allocated() / 2**30:.1f} GiB",
        flush=True,
    )

    results = []
    for i, (image, it) in enumerate(zip(images, items)):
        stem = it.get("stem") or f"flux-{int(t0)}-{seeds[i]}"
        path = out_dir / f"{stem}.png"
        meta = {
            "model": model_id,
            "prompt": it["prompt"],
            "steps": steps,
            "guidance": guidance,
            "width": width,
            "height": height,
            "seed": seeds[i],
            "seconds": round(per, 2),
            "batch_size": len(images),
            "batch_seconds": round(elapsed, 2),
        }
        image.save(path, pnginfo=_pnginfo(meta))
        path.with_suffix(".json").write_text(json.dumps(meta, indent=2))
        results.append({"path": str(path), **meta})
    release()
    return results


def release():
    """Hand the allocator's cached blocks back to the driver.

    Resident weights are untouched; this only drops the free pool PyTorch keeps
    after a peak. Without it a single large batch permanently reserves its
    high-water mark and nothing else fits on the card.
    """
    torch.cuda.empty_cache()


def load_kontext():
    """FLUX.1-Kontext-dev, reusing the base pipeline's encoders and VAE.

    Kontext edits an existing image against an instruction instead of sampling
    from scratch -- the difference between "roll again" and "keep this, change
    that". Only its transformer is loaded; every other component is shared with
    the already-resident FLUX.1-dev pipeline.
    """
    global _kontext
    if _kontext is not None:
        return _kontext
    from diffusers import FluxKontextPipeline, FluxTransformer2DModel

    base = load()
    t0 = time.time()
    path = resolve(KONTEXT_ID)
    transformer = FluxTransformer2DModel.from_pretrained(
        path, subfolder="transformer", torch_dtype=torch.bfloat16
    ).to("cuda")
    pipe = FluxKontextPipeline(
        scheduler=base.scheduler,
        vae=base.vae,
        text_encoder=base.text_encoder,
        tokenizer=base.tokenizer,
        text_encoder_2=base.text_encoder_2,
        tokenizer_2=base.tokenizer_2,
        transformer=transformer,
        image_encoder=None,
        feature_extractor=None,
    )
    pipe.set_progress_bar_config(disable=True)
    _kontext = pipe
    print(
        f"[fluxlib] loaded Kontext in {time.time() - t0:.1f}s; "
        f"{torch.cuda.memory_allocated() / 2**30:.1f} GiB resident total",
        flush=True,
    )
    return _kontext


def edit_geometry(w, h, megapixels=1.0):
    """The size to run an edit at: the source aspect, at the model's happy area.

    Kontext left to itself picks from a fixed menu of preferred resolutions, and
    a 2:3 card comes back square with the bottom cropped away. Preserving aspect
    explicitly is the whole difference between an edit and a re-crop.
    """
    import math

    target = megapixels * 1024 * 1024
    scale = math.sqrt(target / (w * h))
    return max(256, int(round(w * scale / 16)) * 16), max(256, int(round(h * scale / 16)) * 16)


def edit(
    image_path,
    instruction,
    steps=28,
    guidance=2.5,
    seed=None,
    out_dir=None,
    stem=None,
    width=None,
    height=None,
):
    """Apply one targeted instruction to one image. Everything else is preserved.

    Feed this ART, not a composited card -- text that survives a diffusion pass
    intact is luck, and we already set type deterministically.
    """
    from PIL import Image as PILImage

    pipe = load_kontext()
    out_dir = pathlib.Path(out_dir or OUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    src = PILImage.open(image_path).convert("RGB")

    if width is None or height is None:
        width, height = edit_geometry(src.width, src.height)

    if seed is None:
        seed = int.from_bytes(os.urandom(4), "big")
    gen = torch.Generator("cuda").manual_seed(seed)

    t0 = time.time()
    image = pipe(
        image=src,
        prompt=instruction,
        num_inference_steps=steps,
        guidance_scale=guidance,
        width=width,
        height=height,
        generator=gen,
    ).images[0]
    elapsed = time.time() - t0

    stem = stem or f"edit-{int(t0)}-{seed}"
    path = out_dir / f"{stem}.png"
    meta = {
        "model": KONTEXT_ID,
        "source": str(image_path),
        "instruction": instruction,
        "steps": steps,
        "guidance": guidance,
        "seed": seed,
        "seconds": round(elapsed, 2),
        "width": image.width,
        "height": image.height,
    }
    image.save(path, pnginfo=_pnginfo(meta))
    path.with_suffix(".json").write_text(json.dumps(meta, indent=2))
    print(f"[fluxlib] edit {path}  {elapsed:.1f}s  seed={seed}", flush=True)
    release()
    return {"path": str(path), **meta}


def _pnginfo(meta):
    from PIL.PngImagePlugin import PngInfo

    info = PngInfo()
    info.add_text("flux", json.dumps(meta))
    return info
