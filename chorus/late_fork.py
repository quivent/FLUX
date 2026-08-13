#!/usr/bin/env python3
"""Late geometry fork experiment for FLUX.1.

Run one shared denoising trunk, fork the latent into four literal directions at
declared late boundaries, finish each branch through the unchanged transformer
and VAE, and record both trajectory reuse and observed render cost.
"""
import argparse
import gc
import hashlib
import json
import math
import pathlib
import time

import torch
from diffusers import FluxPipeline
from diffusers.pipelines.flux.pipeline_flux import calculate_shift, retrieve_timesteps
from PIL import Image, ImageDraw
from safetensors.torch import load_file, save_file

DIRECTIONS = ("north", "south", "east", "west")


def atomic_json(path, value):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


@torch.inference_mode()
def transformer_step(pipe, latents, timestep, prompt_embeds, pooled, text_ids, image_ids, guidance):
    t = timestep.expand(latents.shape[0]).to(latents.dtype)
    with pipe.transformer.cache_context("cond"):
        flow = pipe.transformer(
            hidden_states=latents,
            timestep=t / 1000,
            guidance=guidance.expand(latents.shape[0]),
            pooled_projections=pooled,
            encoder_hidden_states=prompt_embeds,
            txt_ids=text_ids,
            img_ids=image_ids,
            joint_attention_kwargs={},
            return_dict=False,
        )[0]
    return pipe.scheduler.step(flow, timestep, latents, return_dict=False)[0]


def tangent_pair(parent, seed):
    generator = torch.Generator("cpu").manual_seed(seed)
    # Build and orthogonalize the basis on CPU, then move only the two finished
    # axes. This keeps the seeded basis portable and avoids mixed-device dots.
    flat_parent = parent.detach().cpu().flatten().float()
    unit_parent = flat_parent / flat_parent.norm()
    axes = []
    for _ in range(2):
        vector = torch.randn(flat_parent.shape, generator=generator)
        vector -= torch.dot(vector, unit_parent) * unit_parent
        for prior in axes:
            vector -= torch.dot(vector, prior) * prior
        axes.append(vector / vector.norm())
    return [axis.to(parent.device).reshape(parent.shape) for axis in axes]


def fork_latents(parent, strength, seed):
    u, v = tangent_pair(parent, seed)
    radius = parent.float().norm()
    unit = parent.float() / radius
    axes = (u, -u, v, -v)
    return torch.cat([
        (radius * (math.cos(strength) * unit + math.sin(strength) * axis.float())).to(parent.dtype)
        for axis in axes
    ])


@torch.inference_mode()
def decode(pipe, latents, size):
    unpacked = pipe._unpack_latents(latents, size, size, pipe.vae_scale_factor)
    unpacked = (unpacked / pipe.vae.config.scaling_factor) + pipe.vae.config.shift_factor
    pixels = pipe.vae.decode(unpacked, return_dict=False)[0]
    return pipe.image_processor.postprocess(pixels, output_type="pil")


def finish_suffix(pipe, branches_cpu, fork_after, steps, mu, prompt_embeds, pooled,
                  text_ids, image_ids, guidance, requested_microbatch):
    """Finish identical suffixes with the largest microbatch that fits.

    Scheduling changes only memory occupancy: every branch begins at the same
    fork latent and receives the same timestep sequence it would in batch four.
    """
    finished = []
    used = []
    start = 0
    microbatch = min(requested_microbatch, len(branches_cpu))
    while start < len(branches_cpu):
        take = min(microbatch, len(branches_cpu) - start)
        try:
            timesteps, _ = retrieve_timesteps(pipe.scheduler, steps, "cuda", mu=mu)
            pipe.scheduler.set_begin_index(fork_after)
            chunk = branches_cpu[start:start + take].to("cuda")
            chunk_prompt = prompt_embeds.repeat(take, 1, 1)
            chunk_pooled = pooled.repeat(take, 1)
            chunk_guidance = torch.full((take,), guidance, device="cuda", dtype=torch.float32)
            for timestep in timesteps[fork_after:]:
                chunk = transformer_step(pipe, chunk, timestep, chunk_prompt, chunk_pooled,
                                         text_ids, image_ids, chunk_guidance)
            finished.append(chunk.detach().cpu())
            used.append(take)
            start += take
            del chunk, chunk_prompt, chunk_pooled, chunk_guidance
            gc.collect(); torch.cuda.empty_cache()
        except torch.OutOfMemoryError:
            gc.collect(); torch.cuda.empty_cache()
            if take == 1:
                raise
            microbatch = max(1, take // 2)
            print(json.dumps({"memory_adaptation": "reduce_suffix_microbatch",
                              "from": take, "to": microbatch, "fork_after": fork_after}), flush=True)
    return torch.cat(finished), used


def contact(images, fork_after, shared, path):
    tile = 512
    sheet = Image.new("RGB", (tile * 2, tile * 2), "#f5f1eb")
    draw = ImageDraw.Draw(sheet)
    for i, image in enumerate(images):
        x, y = (i % 2) * tile, (i // 2) * tile
        sheet.paste(image.resize((tile, tile)), (x, y))
        draw.rectangle((x, y, x + 175, y + 24), fill=(247, 245, 240))
        draw.text((x + 7, y + 6), f"{DIRECTIONS[i]} · fork {fork_after} · {shared:.0%}", fill=(38, 34, 29))
    sheet.save(path, quality=86, optimize=True)


def configure_cache(pipe, adapter, threshold):
    if adapter == "none":
        return
    from para_attn.first_block_cache.diffusers_adapters import apply_cache_on_pipe
    apply_cache_on_pipe(pipe, residual_diff_threshold=threshold, downsample_factor=1, warmup_steps=1)


def main():
    ap = argparse.ArgumentParser(description="late-schedule FLUX geometry fork sweep")
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--id", required=True)
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--size", type=int, default=512)
    ap.add_argument("--steps", type=int, default=28)
    ap.add_argument("--fork-steps", default="18,22,25,26")
    ap.add_argument("--strength", type=float, default=0.06)
    ap.add_argument("--guidance", type=float, default=3.6)
    ap.add_argument("--seed", type=int, default=1935692473)
    ap.add_argument("--adapter", choices=("none", "first-block-cache"), default="none")
    ap.add_argument("--cache-threshold", type=float, default=0.08)
    ap.add_argument("--branch-microbatch", type=int, default=2)
    args = ap.parse_args()
    if args.size != 512:
        raise SystemExit("late-fork study is locked to 512x512")
    forks = sorted({int(x.strip()) for x in args.fork_steps.split(",") if x.strip()})
    if not forks or any(x < 1 or x >= args.steps for x in forks):
        raise SystemExit("every fork step must be in [1, steps-1]")
    if not 0.001 <= args.strength <= 0.8:
        raise SystemExit("strength must be in [0.001,0.8] radians")
    if args.branch_microbatch < 1 or args.branch_microbatch > 4:
        raise SystemExit("branch microbatch must be in [1,4]")

    out = pathlib.Path(args.out_dir).expanduser()
    sphere = out / "atlas" / f"{args.id}.sphere"
    sphere.mkdir(parents=True, exist_ok=True)
    manifest_path = sphere / "manifest.json"
    manifest = {
        "kind": "late_geometry_fork", "study_type": "movement", "id": args.id,
        "prompt": args.prompt, "precision": "bf16", "size": args.size,
        "steps": args.steps, "fork_steps": forks, "strength": args.strength,
        "adapter": args.adapter, "cache_threshold": args.cache_threshold,
        "branch_microbatch": args.branch_microbatch,
        "directions": list(DIRECTIONS), "status": "loading", "started": time.time(),
        "hypothesis": "late directional forks move geometry while the high-impact early trajectory remains shared",
    }
    atomic_json(manifest_path, manifest)
    pipe = FluxPipeline.from_pretrained(args.model_dir, torch_dtype=torch.bfloat16, local_files_only=True).to("cuda")
    pipe.set_progress_bar_config(disable=True)
    prompt_embeds, pooled, text_ids = pipe.encode_prompt(
        prompt=args.prompt, device="cuda", num_images_per_prompt=1, max_sequence_length=512)
    # The late-fork loop consumes only the encoded conditioning. Keep the
    # denoiser/VAE on CUDA and park both prompt encoders before the four-way
    # suffix so batch memory measures the motion experiment, not idle language
    # weights.
    if pipe.text_encoder is not None:
        pipe.text_encoder.to("cpu")
    if pipe.text_encoder_2 is not None:
        pipe.text_encoder_2.to("cpu")
    gc.collect(); torch.cuda.empty_cache()
    configure_cache(pipe, args.adapter, args.cache_threshold)
    generator = torch.Generator("cpu").manual_seed(args.seed)
    base, image_ids = pipe.prepare_latents(
        1, pipe.transformer.config.in_channels // 4, args.size, args.size,
        torch.bfloat16, "cuda", generator)
    base = base.detach()
    guidance_one = torch.full((1,), args.guidance, device="cuda", dtype=torch.float32)
    results = []
    manifest["status"] = "running"; atomic_json(manifest_path, manifest)
    image_seq_len = base.shape[1]
    mu = calculate_shift(image_seq_len, pipe.scheduler.config.get("base_image_seq_len", 256),
                         pipe.scheduler.config.get("max_image_seq_len", 4096),
                         pipe.scheduler.config.get("base_shift", 0.5),
                         pipe.scheduler.config.get("max_shift", 1.15))
    cache_dir = sphere / "_cache"; cache_dir.mkdir(exist_ok=True)
    cache_facts = {"prompt": args.prompt, "seed": args.seed, "steps": args.steps,
                   "size": args.size, "guidance": args.guidance,
                   "model_dir": str(pathlib.Path(args.model_dir).resolve())}
    cache_key = hashlib.sha256(json.dumps(cache_facts, sort_keys=True).encode()).hexdigest()
    cache_manifest_path = cache_dir / "manifest.json"
    checkpoint_paths = {step: cache_dir / f"latent-step-{step:03d}.safetensors" for step in forks}
    prior_cache = json.loads(cache_manifest_path.read_text()) if cache_manifest_path.exists() else {}
    cache_hit = prior_cache.get("key") == cache_key and all(path.exists() for path in checkpoint_paths.values())
    checkpoints = {}
    trunk_started = time.perf_counter()
    if cache_hit:
        for step, path in checkpoint_paths.items():
            checkpoints[step] = load_file(path)["latent"]
    else:
        timesteps, _ = retrieve_timesteps(pipe.scheduler, args.steps, "cuda", mu=mu)
        pipe.scheduler.set_begin_index(0)
        latent = base.clone()
        for index, timestep in enumerate(timesteps[:max(forks)], start=1):
            latent = transformer_step(pipe, latent, timestep, prompt_embeds, pooled,
                                      text_ids, image_ids, guidance_one)
            if index in checkpoint_paths:
                checkpoint = latent.detach().cpu().contiguous()
                checkpoints[index] = checkpoint
                save_file({"latent": checkpoint}, checkpoint_paths[index])
        atomic_json(cache_manifest_path, {"key": cache_key, "facts": cache_facts,
                                          "checkpoints": [path.name for path in checkpoint_paths.values()],
                                          "created": time.time(), "schema": "flux.exact-trunk-cache.v1"})
        del latent
    trunk_seconds = time.perf_counter() - trunk_started
    manifest["exact_trunk_cache"] = {"key": cache_key, "hit": cache_hit,
                                     "build_or_load_seconds": trunk_seconds,
                                     "checkpoints": forks}
    atomic_json(manifest_path, manifest)
    gc.collect(); torch.cuda.empty_cache()

    for fork_after in forks:
        branches_cpu = fork_latents(checkpoints[fork_after].to("cuda"), args.strength,
                                    args.seed + fork_after).detach().cpu()
        gc.collect(); torch.cuda.empty_cache()
        branch_started = time.perf_counter()
        branches_cpu, used_microbatches = finish_suffix(
            pipe, branches_cpu, fork_after, args.steps, mu, prompt_embeds, pooled,
            text_ids, image_ids, args.guidance, args.branch_microbatch)
        branch_seconds = time.perf_counter() - branch_started
        decode_started = time.perf_counter()
        images = []
        for branch in branches_cpu:
            images.extend(decode(pipe, branch.unsqueeze(0).to("cuda"), args.size))
        decode_seconds = time.perf_counter() - decode_started
        target = sphere / f"fork-{fork_after:02d}"
        target.mkdir(exist_ok=True)
        for i, image in enumerate(images):
            image.save(target / f"{i + 1}-{DIRECTIONS[i]}.png")
        shared = fork_after / args.steps
        contact(images, fork_after, shared, target / "contact.jpg")
        # Four independent paths cost 4*steps. One trunk plus four suffixes
        # avoids three copies of every shared step before block-level caching.
        transformer_equivalent = fork_after + 4 * (args.steps - fork_after)
        compute_saved = 1 - transformer_equivalent / (4 * args.steps)
        row = {
            "fork_after": fork_after, "remaining_steps": args.steps - fork_after,
            "trajectory_shared": shared, "independent_compute_saved": compute_saved,
            "suffix_microbatches": used_microbatches,
            "exact_trunk_cache_hit": cache_hit, "branch_seconds": branch_seconds,
            "decode_seconds": decode_seconds, "total_seconds": branch_seconds + decode_seconds,
        }
        results.append(row)
        manifest["results"] = results; manifest["updated"] = time.time()
        atomic_json(manifest_path, manifest)
        print(json.dumps(row, sort_keys=True), flush=True)
        del branches_cpu, images; gc.collect(); torch.cuda.empty_cache()

    manifest["status"] = "done"; manifest["finished"] = time.time()
    atomic_json(manifest_path, manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
