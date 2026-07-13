import argparse
import json
import os
import pathlib
import time


DEFAULT_MODEL_DIR = "/Users/joshkornreich/Models/flux1"
REGISTRY_NAME = "registry.json"


def registry_path(model_dir):
    env_path = os.environ.get("FLUX_ANE_REGISTRY", "")
    if env_path:
        return pathlib.Path(env_path).expanduser()
    return pathlib.Path(model_dir).expanduser() / "ane" / REGISTRY_NAME


def default_registry():
    return {
        "version": 1,
        "created": time.time(),
        "packages": [],
    }


def load_registry(model_dir):
    path = registry_path(model_dir)
    if not path.exists():
        return default_registry()
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return default_registry()
    if not isinstance(data, dict):
        return default_registry()
    data.setdefault("version", 1)
    data.setdefault("packages", [])
    if not isinstance(data["packages"], list):
        data["packages"] = []
    return data


def save_registry(model_dir, registry):
    path = registry_path(model_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(registry, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)
    return path


def resolve_package_path(registry_file, value):
    if not value:
        return pathlib.Path()
    path = pathlib.Path(value).expanduser()
    if path.is_absolute():
        return path
    return registry_file.parent / path


def package_exists(registry_file, package):
    for key in ("compiled_path", "package_path"):
        path = resolve_package_path(registry_file, package.get(key, ""))
        if path.exists():
            return True
    return False


def package_matches_size(package, width, height):
    if width and int(package.get("width", 0) or 0) not in (0, int(width)):
        return False
    if height and int(package.get("height", 0) or 0) not in (0, int(height)):
        return False
    return True


def select_render_package(model_dir, width=0, height=0):
    registry_file = registry_path(model_dir)
    registry = load_registry(model_dir)
    for package in registry.get("packages", []):
        if package.get("component") != "pipeline":
            continue
        if not bool(package.get("ane_validated")):
            continue
        if not package_matches_size(package, width, height):
            continue
        if package_exists(registry_file, package):
            return package
    return None


def capabilities(model_dir):
    registry_file = registry_path(model_dir)
    registry = load_registry(model_dir)
    packages = [p for p in registry.get("packages", []) if isinstance(p, dict)]
    existing = [p for p in packages if package_exists(registry_file, p)]
    validated = [p for p in existing if bool(p.get("ane_validated"))]
    renderable = [p for p in validated if p.get("component") == "pipeline"]
    components = sorted({str(p.get("component", "unknown")) for p in existing})
    return {
        "ane_registry": str(registry_file),
        "ane_registry_exists": registry_file.exists(),
        "ane_packages": len(existing),
        "ane_validated": len(validated) > 0,
        "ane_renderable": len(renderable) > 0,
        "ane_components": components,
    }


def upsert_package(model_dir, record):
    registry = load_registry(model_dir)
    packages = [p for p in registry.get("packages", []) if isinstance(p, dict)]
    name = record["name"]
    replaced = False
    for index, package in enumerate(packages):
        if package.get("name") == name:
            merged = dict(package)
            merged.update(record)
            packages[index] = merged
            replaced = True
            break
    if not replaced:
        packages.append(record)
    registry["packages"] = packages
    registry["updated"] = time.time()
    return save_registry(model_dir, registry)


def convert_compute_units(value):
    import coremltools as ct

    normalized = value.strip().lower().replace("-", "_")
    mapping = {
        "all": ct.ComputeUnit.ALL,
        "cpu_only": ct.ComputeUnit.CPU_ONLY,
        "cpu_and_gpu": ct.ComputeUnit.CPU_AND_GPU,
        "cpu_and_ne": ct.ComputeUnit.CPU_AND_NE,
        "cpuandneuralengine": ct.ComputeUnit.CPU_AND_NE,
        "cpu_and_neural_engine": ct.ComputeUnit.CPU_AND_NE,
    }
    if normalized not in mapping:
        raise ValueError(f"unknown compute units {value!r}")
    return mapping[normalized]


def convert_vae_decoder(args):
    import numpy as np
    import torch
    import coremltools as ct
    from diffusers import AutoencoderKL

    model_dir = pathlib.Path(args.model_dir).expanduser()
    out_dir = pathlib.Path(args.out_dir).expanduser() if args.out_dir else model_dir / "ane"
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.width % 8 != 0 or args.height % 8 != 0:
        raise ValueError("width and height must be divisible by 8 for FLUX VAE latents")

    latent_h = args.height // 8
    latent_w = args.width // 8
    dtype = torch.float16 if args.precision == "fp16" else torch.float32
    np_dtype = np.float16 if args.precision == "fp16" else np.float32

    print(f"phase=load_vae model={model_dir}", flush=True)
    vae = AutoencoderKL.from_pretrained(
        model_dir,
        subfolder="vae",
        torch_dtype=dtype,
        local_files_only=True,
    ).eval()
    vae.to("cpu")

    class FluxVAEDecoder(torch.nn.Module):
        def __init__(self, inner):
            super().__init__()
            self.inner = inner
            self.scaling_factor = float(inner.config.scaling_factor)
            self.shift_factor = float(getattr(inner.config, "shift_factor", 0.0))

        def forward(self, latents):
            latents = (latents / self.scaling_factor) + self.shift_factor
            image = self.inner.decode(latents, return_dict=False)[0]
            return image

    wrapper = FluxVAEDecoder(vae).eval()
    sample = torch.zeros((1, 16, latent_h, latent_w), dtype=dtype)
    print(f"phase={args.exporter} latent_shape={list(sample.shape)}", flush=True)
    if args.exporter == "torch-export":
        traced = torch.export.export(wrapper, (sample,)).run_decompositions({})
    else:
        traced = torch.jit.trace(wrapper, sample, check_trace=False)

    name = args.name or f"vae_decoder_{args.width}x{args.height}_{args.precision}"
    package_path = out_dir / f"{name}.mlpackage"
    compute_units = convert_compute_units(args.compute_units)
    print(f"phase=convert_coreml package={package_path}", flush=True)
    mlmodel = ct.convert(
        traced,
        convert_to="mlprogram",
        compute_units=compute_units,
        minimum_deployment_target=ct.target.macOS14,
        inputs=[ct.TensorType(name="latents", shape=sample.shape, dtype=np_dtype)],
    )
    print(f"phase=save package={package_path}", flush=True)
    mlmodel.save(str(package_path))

    record = {
        "name": name,
        "component": "vae_decoder",
        "width": args.width,
        "height": args.height,
        "latent_shape": list(sample.shape),
        "precision": args.precision,
        "compute_units": args.compute_units,
        "package_path": str(package_path),
        "ane_validated": False,
        "created": time.time(),
        "notes": "Converted FLUX VAE decoder only; not a complete render backend.",
    }
    path = upsert_package(model_dir, record)
    print(json.dumps({"ok": True, "registry": str(path), "package": str(package_path), "record": record}, sort_keys=True))


def mark_validated(args):
    registry = load_registry(args.model_dir)
    found = False
    for package in registry.get("packages", []):
        if package.get("name") != args.name:
            continue
        package["ane_validated"] = bool(args.ane_validated)
        package["validated_at"] = time.time()
        if args.notes:
            package["validation_notes"] = args.notes
        found = True
    if not found:
        raise ValueError(f"package {args.name!r} not found")
    path = save_registry(args.model_dir, registry)
    print(json.dumps({"ok": True, "registry": str(path), "name": args.name}, sort_keys=True))


def main():
    parser = argparse.ArgumentParser(description="FLUX ANE package registry and conversion tools")
    sub = parser.add_subparsers(dest="cmd", required=True)

    probe = sub.add_parser("probe", help="print ANE registry capabilities")
    probe.add_argument("--model-dir", default=os.environ.get("MODEL_DIR", DEFAULT_MODEL_DIR))

    init = sub.add_parser("init", help="create an empty ANE registry")
    init.add_argument("--model-dir", default=os.environ.get("MODEL_DIR", DEFAULT_MODEL_DIR))

    convert = sub.add_parser("convert-vae-decoder", help="convert the fixed-shape FLUX VAE decoder to Core ML")
    convert.add_argument("--model-dir", default=os.environ.get("MODEL_DIR", DEFAULT_MODEL_DIR))
    convert.add_argument("--out-dir", default="")
    convert.add_argument("--width", type=int, default=1024)
    convert.add_argument("--height", type=int, default=1024)
    convert.add_argument("--precision", choices=["fp16", "fp32"], default="fp32")
    convert.add_argument("--compute-units", default="cpu_and_ne")
    convert.add_argument("--exporter", choices=["torchscript", "torch-export"], default="torch-export")
    convert.add_argument("--name", default="")

    validate = sub.add_parser("mark-validated", help="record external Instruments validation for a package")
    validate.add_argument("--model-dir", default=os.environ.get("MODEL_DIR", DEFAULT_MODEL_DIR))
    validate.add_argument("--name", required=True)
    validate.add_argument("--ane-validated", action="store_true")
    validate.add_argument("--notes", default="")

    args = parser.parse_args()
    if args.cmd == "probe":
        print(json.dumps(capabilities(args.model_dir), sort_keys=True))
    elif args.cmd == "init":
        path = save_registry(args.model_dir, load_registry(args.model_dir))
        print(json.dumps({"ok": True, "registry": str(path)}, sort_keys=True))
    elif args.cmd == "convert-vae-decoder":
        convert_vae_decoder(args)
    elif args.cmd == "mark-validated":
        mark_validated(args)


if __name__ == "__main__":
    main()
