#!/usr/bin/env python3
"""Late geometry fork experiment for FLUX.1.

Run one shared denoising trunk, fork the latent into four literal directions at
declared late boundaries, finish each branch through the unchanged transformer
and VAE, and record both trajectory reuse and observed render cost.
"""
import argparse
import gc
import json
import math
import pathlib
import time

import torch
from diffusers import FluxPipeline
from diffusers.pipelines.flux.pipeline_flux import calculate_shift, retrieve_timesteps
from PIL import Image, ImageDraw

DIRECTIONS = ("north", "south", "east", "west")


def atomic_json(path, value):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


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


def decode(pipe, latents, size):
    unpacked = pipe._unpack_latents(latents, size, size, pipe.vae_scale_factor)
    unpacked = (unpacked / pipe.vae.config.scaling_factor) + pipe.vae.config.shift_factor
    pixels = pipe.vae.decode(unpacked, return_dict=False)[0]
    return pipe.image_processor.postprocess(pixels, output_type="pil")


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
    args = ap.parse_args()
    if args.size != 512:
        raise SystemExit("late-fork study is locked to 512x512")
    forks = sorted({int(x.strip()) for x in args.fork_steps.split(",") if x.strip()})
    if not forks or any(x < 1 or x >= args.steps for x in forks):
        raise SystemExit("every fork step must be in [1, steps-1]")
    if not 0.001 <= args.strength <= 0.8:
        raise SystemExit("strength must be in [0.001,0.8] radians")

    out = pathlib.Path(args.out_dir).expanduser()
    sphere = out / "atlas" / f"{args.id}.sphere"
    sphere.mkdir(parents=True, exist_ok=True)
    manifest_path = sphere / "manifest.json"
    manifest = {
        "kind": "late_geometry_fork", "study_type": "movement", "id": args.id,
        "prompt": args.prompt, "precision": "bf16", "size": args.size,
        "steps": args.steps, "fork_steps": forks, "strength": args.strength,
        "adapter": args.adapter, "cache_threshold": args.cache_threshold,
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
    branch_prompt = prompt_embeds.repeat(4, 1, 1)
    branch_pooled = pooled.repeat(4, 1)
    guidance_one = torch.full((1,), args.guidance, device="cuda", dtype=torch.float32)
    guidance_four = torch.full((4,), args.guidance, device="cuda", dtype=torch.float32)
    results = []
    manifest["status"] = "running"; atomic_json(manifest_path, manifest)

    for fork_after in forks:
        image_seq_len = base.shape[1]
        mu = calculate_shift(image_seq_len, pipe.scheduler.config.get("base_image_seq_len", 256),
                             pipe.scheduler.config.get("max_image_seq_len", 4096),
                             pipe.scheduler.config.get("base_shift", 0.5),
                             pipe.scheduler.config.get("max_shift", 1.15))
        timesteps, _ = retrieve_timesteps(pipe.scheduler, args.steps, "cuda", mu=mu)
        pipe.scheduler.set_begin_index(0)
        latent = base.clone()
        trunk_started = time.perf_counter()
        for timestep in timesteps[:fork_after]:
            latent = transformer_step(pipe, latent, timestep, prompt_embeds, pooled,
                                      text_ids, image_ids, guidance_one)
        trunk_seconds = time.perf_counter() - trunk_started
        branches = fork_latents(latent, args.strength, args.seed + fork_after)
        branch_started = time.perf_counter()
        for timestep in timesteps[fork_after:]:
            branches = transformer_step(pipe, branches, timestep, branch_prompt, branch_pooled,
                                        text_ids, image_ids, guidance_four)
        branch_seconds = time.perf_counter() - branch_started
        decode_started = time.perf_counter()
        images = decode(pipe, branches, args.size)
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
            "trunk_seconds": trunk_seconds, "branch_seconds": branch_seconds,
            "decode_seconds": decode_seconds, "total_seconds": trunk_seconds + branch_seconds + decode_seconds,
        }
        results.append(row)
        manifest["results"] = results; manifest["updated"] = time.time()
        atomic_json(manifest_path, manifest)
        print(json.dumps(row, sort_keys=True), flush=True)
        del latent, branches, images; gc.collect(); torch.cuda.empty_cache()

    manifest["status"] = "done"; manifest["finished"] = time.time()
    atomic_json(manifest_path, manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
