import json
import os
import pathlib
import struct
import sys


MODEL_DIR = pathlib.Path(os.environ.get("MODEL_DIR", "/Users/joshkornreich/Models/flux1"))


def safetensors_dtypes(path: pathlib.Path) -> set[str]:
    with path.open("rb") as f:
        header_len = struct.unpack("<Q", f.read(8))[0]
        header = json.loads(f.read(header_len))
    return {v.get("dtype") for k, v in header.items() if k != "__metadata__"}


def main() -> int:
    print(f"model_dir={MODEL_DIR}")
    if not MODEL_DIR.exists():
        print("missing model directory", file=sys.stderr)
        return 1

    required = [
        "model_index.json",
        "scheduler/scheduler_config.json",
        "text_encoder/model.safetensors",
        "text_encoder_2/model-00001-of-00002.safetensors",
        "text_encoder_2/model-00002-of-00002.safetensors",
        "transformer/diffusion_pytorch_model-00001-of-00003.safetensors",
        "transformer/diffusion_pytorch_model-00002-of-00003.safetensors",
        "transformer/diffusion_pytorch_model-00003-of-00003.safetensors",
        "vae/diffusion_pytorch_model.safetensors",
    ]
    missing = [p for p in required if not (MODEL_DIR / p).exists()]
    if missing:
        print("missing files:", file=sys.stderr)
        for p in missing:
            print(f"  {p}", file=sys.stderr)
        return 1

    try:
        import torch
        import diffusers
        import transformers
    except Exception as exc:
        print(f"import check failed: {exc}", file=sys.stderr)
        return 1

    print(f"torch={torch.__version__}")
    print(f"diffusers={diffusers.__version__}")
    print(f"transformers={transformers.__version__}")
    print(f"mps_available={torch.backends.mps.is_available()}")

    checks = [
        MODEL_DIR / "transformer/diffusion_pytorch_model-00001-of-00003.safetensors",
        MODEL_DIR / "text_encoder_2/model-00001-of-00002.safetensors",
    ]
    for path in checks:
        print(f"{path.name}: {sorted(safetensors_dtypes(path))}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
