import argparse
import os
import pathlib
import time

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

import torch
from diffusers import FluxPipeline

import flux_paths


DEFAULT_MODEL_DIR = flux_paths.default_model_dir()
DEFAULT_OUT_DIR = flux_paths.default_out_dir()


def choose_device(requested: str) -> str:
    if requested != "auto":
        return requested
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Lean local FLUX.1-dev BF16 runner")
    parser.add_argument("--model-dir", default=os.environ.get("MODEL_DIR", DEFAULT_MODEL_DIR))
    parser.add_argument("--out-dir", default=os.environ.get("OUT_DIR", DEFAULT_OUT_DIR))
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--width", type=int, default=1024)
    parser.add_argument("--height", type=int, default=1024)
    parser.add_argument("--steps", type=int, default=28)
    parser.add_argument("--guidance", type=float, default=3.5)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--device", choices=["auto", "mps", "cuda", "cpu"], default="auto")
    parser.add_argument("--dtype", choices=["bf16", "fp16", "fp32"], default="bf16")
    parser.add_argument("--filename", default=None)
    parser.add_argument("--save-intermediates", action="store_true", help="Save intermediate latents and step previews")
    parser.add_argument("--intermediates-dir", default=None, help="Directory to save step previews")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    model_dir = pathlib.Path(args.model_dir).expanduser()
    out_dir = pathlib.Path(args.out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    dtype = {
        "bf16": torch.bfloat16,
        "fp16": torch.float16,
        "fp32": torch.float32,
    }[args.dtype]
    device = choose_device(args.device)

    print(f"model={model_dir}")
    print(f"device={device} dtype={args.dtype}")

    pipe = FluxPipeline.from_pretrained(
        str(model_dir),
        torch_dtype=dtype,
        local_files_only=True,
    )
    pipe.to(device)

    generator = None
    if args.seed is not None:
        generator = torch.Generator(device="cpu").manual_seed(args.seed)

    intermediates = []
    def step_callback(pipe_ref, step_index, timestep, callback_kwargs):
        if args.save_intermediates:
            latents = callback_kwargs.get("latents")
            if latents is not None:
                inter_dir = pathlib.Path(args.intermediates_dir or (out_dir / "intermediates"))
                inter_dir.mkdir(parents=True, exist_ok=True)
                step_path = inter_dir / f"latent_step_{step_index:03d}.pt"
                torch.save(latents.cpu(), step_path)
                intermediates.append(str(step_path))
        return callback_kwargs

    started = time.time()
    call_kwargs = {
        "prompt": args.prompt,
        "width": args.width,
        "height": args.height,
        "guidance_scale": args.guidance,
        "num_inference_steps": args.steps,
        "generator": generator,
    }
    if args.save_intermediates:
        call_kwargs["callback_on_step_end"] = step_callback

    image = pipe(**call_kwargs).images[0]

    filename = args.filename
    if filename is None:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        seed_part = "random" if args.seed is None else str(args.seed)
        filename = f"flux-{stamp}-seed-{seed_part}.png"
    output_path = out_dir / filename
    image.save(output_path)

    elapsed = time.time() - started
    print(f"saved={output_path}")
    if intermediates:
        print(f"intermediates={len(intermediates)} steps saved to {out_dir / 'intermediates'}")
    print(f"seconds={elapsed:.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
