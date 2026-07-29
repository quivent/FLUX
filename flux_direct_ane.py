import argparse
import json
import os
import pathlib
import time
import statistics

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

import numpy as np
import torch
from diffusers import FluxPipeline

import flux_paths


DEFAULT_MODEL_DIR = flux_paths.default_model_dir()
DEFAULT_OUT_DIR = flux_paths.default_direct_ane_dir()
DEFAULT_DENSE_SUMMARY = os.path.join(DEFAULT_OUT_DIR, "dense_slice_1024x1024_summary.json")
DEFAULT_BLOCK_BENCHMARK = os.path.join(DEFAULT_OUT_DIR, "block_stack_1024x1024_benchmark.json")
DEFAULT_LATENT_PIPELINE_BENCHMARK = os.path.join(DEFAULT_OUT_DIR, "latent_pipeline_1024x1024_benchmark.json")
DEFAULT_COMPONENT_BENCHMARK = os.path.join(DEFAULT_OUT_DIR, "component_1024x1024_benchmark.json")
DEFAULT_ANEFORGE_PROJECTION_BENCHMARK = os.path.join(
    DEFAULT_OUT_DIR, "aneforge_projection_1024x1024_benchmark.json"
)
DEFAULT_ANEFORGE_OPTIMIZED_PROJECTION_PLAN = os.path.join(
    DEFAULT_OUT_DIR, "aneforge_optimized_projection_plan_1024x1024.json"
)
DEFAULT_ANEFORGE_ATTENTION_BENCHMARK = os.path.join(
    DEFAULT_OUT_DIR, "aneforge_attention_1024x1024_benchmark.json"
)


def parse_dtype(name):
    lowered = name.lower()
    if lowered in ("bf16", "bfloat16"):
        return torch.bfloat16
    if lowered in ("fp16", "float16", "half"):
        return torch.float16
    if lowered in ("fp32", "float32"):
        return torch.float32
    raise ValueError(f"unsupported dtype: {name}")


def classify_parameter(name):
    if name.startswith("norm1_context."):
        return "modulation_context"
    if name.startswith("norm1."):
        return "modulation_image"
    if name.startswith("attn.to_q") or name.startswith("attn.to_k") or name.startswith("attn.to_v"):
        return "attention_image_qkv"
    if name.startswith("attn.add_q_proj") or name.startswith("attn.add_k_proj") or name.startswith("attn.add_v_proj"):
        return "attention_text_qkv"
    if name.startswith("attn.to_out"):
        return "attention_image_out"
    if name.startswith("attn.to_add_out"):
        return "attention_text_out"
    if name.startswith("attn.norm"):
        return "attention_norm"
    if name.startswith("ff_context."):
        return "mlp_context"
    if name.startswith("ff."):
        return "mlp_image"
    if name.startswith("norm."):
        return "modulation_single"
    if name.startswith("proj_mlp."):
        return "mlp_single_in"
    if name.startswith("proj_out."):
        return "single_fused_out"
    return "other"


def proposed_pack(param, tile_m=128, tile_n=128):
    shape = param.get("shape", [])
    is_matrix = len(shape) == 2
    item = {
        "source_name": param["name"],
        "source_shape": shape,
        "source_dtype": param["dtype"],
        "source_bytes": param["bytes"],
        "role": classify_parameter(param["name"]),
        "pack_kind": "bias_or_vector",
        "target_dtype": param["dtype"],
        "tile": None,
        "tile_count": 1,
        "packed_bytes_estimate": param["bytes"],
        "notes": [],
    }
    if is_matrix:
        rows, cols = int(shape[0]), int(shape[1])
        tile_rows = (rows + tile_m - 1) // tile_m
        tile_cols = (cols + tile_n - 1) // tile_n
        item.update(
            {
                "pack_kind": "tiled_matrix",
                "tile": [tile_m, tile_n],
                "tile_count": tile_rows * tile_cols,
                "packed_layout": "blocked_row_major_v1",
            }
        )
        if rows % tile_m or cols % tile_n:
            padded_rows = tile_rows * tile_m
            padded_cols = tile_cols * tile_n
            element_bytes = param["bytes"] // max(1, rows * cols)
            item["packed_bytes_estimate"] = padded_rows * padded_cols * element_bytes
            item["notes"].append("requires tile padding")
    return item


def summarize_packs(packs):
    roles = {}
    total = 0
    for pack in packs:
        role = pack["role"]
        roles.setdefault(role, {"count": 0, "source_bytes": 0, "packed_bytes_estimate": 0, "matrix_count": 0})
        roles[role]["count"] += 1
        roles[role]["source_bytes"] += int(pack["source_bytes"])
        roles[role]["packed_bytes_estimate"] += int(pack["packed_bytes_estimate"])
        if pack["pack_kind"] == "tiled_matrix":
            roles[role]["matrix_count"] += 1
        total += int(pack["source_bytes"])
    for role in roles.values():
        role["source_mb"] = role["source_bytes"] / (1024 * 1024)
        role["packed_mb_estimate"] = role["packed_bytes_estimate"] / (1024 * 1024)
    return {"source_bytes": total, "source_mb": total / (1024 * 1024), "roles": roles}


def group_specs(manifest, pack_plan):
    capture = next(record for record in manifest["captures"] if record["phase"] == "pre")
    kwargs = capture["kwargs"]
    img_tokens = int(kwargs["hidden_states"]["shape"][1])
    text_tokens = int(kwargs["encoder_hidden_states"]["shape"][1])
    dim = int(kwargs["hidden_states"]["shape"][2])
    groups = []
    packs_by_name = {pack["source_name"]: pack for pack in pack_plan["packs"]}

    def add_group(name, kind, input_tokens, weights):
        present = [packs_by_name[w] for w in weights if w in packs_by_name]
        if not present:
            return
        flops = 0
        output_shapes = []
        matmuls = []
        for pack in present:
            shape = pack["source_shape"]
            if len(shape) != 2:
                continue
            out_dim, in_dim = int(shape[0]), int(shape[1])
            flops += 2 * input_tokens * in_dim * out_dim
            output_shape = [1, input_tokens, out_dim]
            output_shapes.append(output_shape)
            matmuls.append(
                {
                    "weight": pack["source_name"],
                    "m": input_tokens,
                    "n": out_dim,
                    "k": in_dim,
                    "output_shape": output_shape,
                    "counted_flops": 2 * input_tokens * in_dim * out_dim,
                    "counted_gflops": 2 * input_tokens * in_dim * out_dim / 1e9,
                    "source_bytes": int(pack["source_bytes"]),
                    "source_mb": int(pack["source_bytes"]) / (1024 * 1024),
                }
            )
        source_bytes = sum(int(pack["source_bytes"]) for pack in present)
        packed_bytes = sum(int(pack["packed_bytes_estimate"]) for pack in present)
        tile_count = sum(int(pack["tile_count"]) for pack in present)
        groups.append(
            {
                "name": name,
                "kind": kind,
                "input_shape": [1, input_tokens, dim],
                "output_shapes": output_shapes,
                "matmuls": matmuls,
                "weights": weights,
                "source_bytes": source_bytes,
                "source_mb": source_bytes / (1024 * 1024),
                "packed_bytes_estimate": packed_bytes,
                "packed_mb_estimate": packed_bytes / (1024 * 1024),
                "tile_count": tile_count,
                "counted_flops": flops,
                "counted_gflops": flops / 1e9,
            }
        )

    block_type = manifest["target"].get("block_type", "dual")
    block_index = manifest["target"].get("block_index", 0)
    prefix = f"{block_type}{block_index}"
    if block_type == "single":
        total_tokens = img_tokens + text_tokens
        add_group(
            f"{prefix}_joint_qkv",
            "attention_projection",
            total_tokens,
            ["attn.to_q.weight", "attn.to_k.weight", "attn.to_v.weight"],
        )
        add_group(f"{prefix}_joint_mlp_in", "mlp_projection", total_tokens, ["proj_mlp.weight"])
        add_group(f"{prefix}_joint_fused_out", "mlp_attention_projection", total_tokens, ["proj_out.weight"])
    else:
        add_group(
            f"{prefix}_image_qkv",
            "attention_projection",
            img_tokens,
            ["attn.to_q.weight", "attn.to_k.weight", "attn.to_v.weight"],
        )
        add_group(
            f"{prefix}_text_qkv",
            "attention_projection",
            text_tokens,
            ["attn.add_q_proj.weight", "attn.add_k_proj.weight", "attn.add_v_proj.weight"],
        )
        add_group(f"{prefix}_image_attention_out", "attention_projection", img_tokens, ["attn.to_out.0.weight"])
        add_group(f"{prefix}_text_attention_out", "attention_projection", text_tokens, ["attn.to_add_out.weight"])
        add_group(f"{prefix}_image_mlp_in", "mlp_projection", img_tokens, ["ff.net.0.proj.weight"])
        add_group(f"{prefix}_image_mlp_out", "mlp_projection", img_tokens, ["ff.net.2.weight"])
        add_group(f"{prefix}_text_mlp_in", "mlp_projection", text_tokens, ["ff_context.net.0.proj.weight"])
        add_group(f"{prefix}_text_mlp_out", "mlp_projection", text_tokens, ["ff_context.net.2.weight"])
    return groups


def tensor_info(value):
    if isinstance(value, torch.Tensor):
        return {
            "shape": list(value.shape),
            "dtype": str(value.dtype).replace("torch.", ""),
            "device": str(value.device),
            "numel": int(value.numel()),
            "bytes": int(value.numel() * value.element_size()),
        }
    if isinstance(value, (tuple, list)):
        return [tensor_info(item) for item in value]
    if isinstance(value, dict):
        return {str(key): tensor_info(item) for key, item in value.items()}
    if value is None:
        return None
    return {"type": type(value).__name__, "repr": repr(value)}


def module_param_info(module):
    params = []
    total = 0
    for name, param in module.named_parameters():
        item = tensor_info(param)
        item["name"] = name
        params.append(item)
        total += item["bytes"]
    return {
        "count": len(params),
        "bytes": total,
        "mb": total / (1024 * 1024),
        "parameters": params,
    }


def sync():
    if torch.backends.mps.is_available():
        torch.mps.synchronize()


def median_seconds(samples):
    return statistics.median(samples) if samples else 0.0


class CaptureHook:
    def __init__(self, stop_after_capture):
        self.stop_after_capture = stop_after_capture
        self.records = []

    def pre(self, module, args, kwargs):
        self.records.append(
            {
                "phase": "pre",
                "module": module.__class__.__name__,
                "args": tensor_info(args),
                "kwargs": tensor_info(kwargs),
            }
        )

    def post(self, module, args, kwargs, output):
        self.records.append(
            {
                "phase": "post",
                "module": module.__class__.__name__,
                "output": tensor_info(output),
            }
        )
        if self.stop_after_capture:
            raise StopIteration("captured target block")


def capture(args):
    model_dir = pathlib.Path(args.model_dir).expanduser()
    out_dir = pathlib.Path(args.out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    device = "mps" if torch.backends.mps.is_available() else "cpu"

    pipe = FluxPipeline.from_pretrained(
        model_dir,
        torch_dtype=torch.bfloat16,
        local_files_only=True,
    )
    pipe.to(device)

    transformer = pipe.transformer
    if args.block_type == "dual":
        blocks = transformer.transformer_blocks
    else:
        blocks = transformer.single_transformer_blocks
    if args.block_index < 0 or args.block_index >= len(blocks):
        raise ValueError(f"{args.block_type} block index out of range: {args.block_index}")
    block = blocks[args.block_index]

    hook = CaptureHook(stop_after_capture=args.stop_after_capture)
    pre_handle = block.register_forward_pre_hook(hook.pre, with_kwargs=True)
    post_handle = block.register_forward_hook(hook.post, with_kwargs=True)
    started = time.perf_counter()
    error = None
    try:
        generator = torch.Generator(device="cpu").manual_seed(args.seed)
        pipe(
            prompt=args.prompt,
            width=args.width,
            height=args.height,
            guidance_scale=args.guidance,
            num_inference_steps=args.steps,
            generator=generator,
            output_type="latent",
        )
        sync()
    except StopIteration as exc:
        error = str(exc)
        sync()
    finally:
        pre_handle.remove()
        post_handle.remove()

    elapsed = time.perf_counter() - started
    manifest = {
        "version": 1,
        "created": time.time(),
        "model_dir": str(model_dir),
        "target": {
            "kind": "mmdit_block",
            "block_type": args.block_type,
            "block_index": args.block_index,
            "module": block.__class__.__name__,
        },
        "shape_contract": {
            "width": args.width,
            "height": args.height,
            "steps": args.steps,
            "guidance": args.guidance,
            "seed": args.seed,
            "max_sequence_length": 512,
        },
        "runtime": {
            "device": device,
            "dtype": "bfloat16",
            "elapsed_seconds": elapsed,
            "stopped_after_capture": bool(args.stop_after_capture),
            "stop_reason": error,
        },
        "transformer_config": dict(transformer.config),
        "parameter_inventory": module_param_info(block),
        "captures": hook.records,
        "direct_ane_notes": [
            "This manifest defines the first direct-ANE target subgraph.",
            "Weight residency and packing remain the hardest unresolved item.",
            "No ANE execution is claimed by this manifest.",
        ],
    }

    name = args.name or f"{args.block_type}_block_{args.block_index}_{args.width}x{args.height}.json"
    path = out_dir / name
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)
    print(json.dumps({"ok": True, "manifest": str(path), "elapsed_seconds": elapsed}, sort_keys=True))


def pack_plan(args):
    manifest_path = pathlib.Path(args.manifest).expanduser()
    data = json.loads(manifest_path.read_text())
    params = data["parameter_inventory"]["parameters"]
    packs = [proposed_pack(param, tile_m=args.tile_m, tile_n=args.tile_n) for param in params]
    plan = {
        "version": 1,
        "created": time.time(),
        "source_manifest": str(manifest_path),
        "target": data["target"],
        "shape_contract": data["shape_contract"],
        "pack_format": {
            "name": "direct_ane_block_pack_v1",
            "matrix_layout": "blocked_row_major_v1",
            "tile": [args.tile_m, args.tile_n],
            "weight_residency": "hardest_unresolved_item",
            "endianness": "native",
        },
        "summary": summarize_packs(packs),
        "packs": packs,
        "open_questions": [
            "Confirm ANE-native tile sizes per chip/runtime.",
            "Choose final quantization format and scale packing.",
            "Define residency/cache lifecycle for 648 MB-class block packs.",
            "Design zero-copy boundary buffers for hidden/text states.",
        ],
    }
    out = pathlib.Path(args.out).expanduser() if args.out else manifest_path.with_name(
        manifest_path.stem + ".packplan.json"
    )
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n")
    tmp.replace(out)
    print(json.dumps({"ok": True, "pack_plan": str(out), "summary": plan["summary"]}, sort_keys=True))


def projection_plan(args):
    manifest_path = pathlib.Path(args.manifest).expanduser()
    pack_path = pathlib.Path(args.pack_plan).expanduser()
    manifest = json.loads(manifest_path.read_text())
    pack_plan_data = json.loads(pack_path.read_text())
    groups = group_specs(manifest, pack_plan_data)
    plan = {
        "version": 1,
        "created": time.time(),
        "source_manifest": str(manifest_path),
        "source_pack_plan": str(pack_path),
        "target": manifest["target"],
        "shape_contract": manifest["shape_contract"],
        "groups": groups,
        "summary": {
            "group_count": len(groups),
            "source_mb": sum(group["source_mb"] for group in groups),
            "counted_gflops": sum(group["counted_gflops"] for group in groups),
            "tile_count": sum(group["tile_count"] for group in groups),
        },
        "runtime_contract": {
            "goal": "direct_ane_dense_projection_groups",
            "boundary": "resident hidden/text tensors in block layout",
            "avoid": ["CPU bounce", "per-step weight repacking", "mixed CPU_AND_NE placement claims"],
        },
    }
    out = pathlib.Path(args.out).expanduser() if args.out else manifest_path.with_name(
        manifest_path.stem + ".projectionplan.json"
    )
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n")
    tmp.replace(out)
    print(json.dumps({"ok": True, "projection_plan": str(out), "summary": plan["summary"]}, sort_keys=True))


def attention_specs(manifest):
    capture = next(record for record in manifest["captures"] if record["phase"] == "pre")
    kwargs = capture["kwargs"]
    img_tokens = int(kwargs["hidden_states"]["shape"][1])
    text_tokens = int(kwargs["encoder_hidden_states"]["shape"][1])
    total_tokens = img_tokens + text_tokens
    heads = int(manifest["transformer_config"]["num_attention_heads"])
    head_dim = int(manifest["transformer_config"]["attention_head_dim"])
    block_type = manifest["target"].get("block_type", "dual")
    block_index = manifest["target"].get("block_index", 0)
    prefix = f"{block_type}{block_index}"

    def attn_matmul(name, q_tokens, k_tokens, n_label):
        flops = 2 * heads * q_tokens * k_tokens * head_dim
        return {
            "name": name,
            "kind": "attention_scores_or_apply",
            "heads": heads,
            "head_dim": head_dim,
            "query_tokens": q_tokens,
            "key_value_tokens": k_tokens,
            "matrix_shape_per_head": [q_tokens, k_tokens, head_dim],
            "counted_flops": flops,
            "counted_gflops": flops / 1e9,
            "semantic_n": n_label,
        }

    return [
        attn_matmul(f"{prefix}_qk_scores", total_tokens, total_tokens, "key_tokens"),
        attn_matmul(f"{prefix}_av_apply", total_tokens, total_tokens, "head_dim"),
    ]


def attention_plan(args):
    manifest_path = pathlib.Path(args.manifest).expanduser()
    manifest = json.loads(manifest_path.read_text())
    groups = attention_specs(manifest)
    plan = {
        "version": 1,
        "created": time.time(),
        "source_manifest": str(manifest_path),
        "target": manifest["target"],
        "shape_contract": manifest["shape_contract"],
        "groups": groups,
        "summary": {
            "group_count": len(groups),
            "counted_gflops": sum(group["counted_gflops"] for group in groups),
        },
        "runtime_contract": {
            "goal": "direct_ane_attention_qk_av",
            "boundary": "post-QKV head tensors with rotary already applied",
            "notes": [
                "Softmax is not included in this dense matmul plan.",
                "QK scores and AV apply are modeled as dense matmul work per head.",
            ],
        },
    }
    out = pathlib.Path(args.out).expanduser() if args.out else manifest_path.with_name(
        manifest_path.stem + ".attentionplan.json"
    )
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n")
    tmp.replace(out)
    print(json.dumps({"ok": True, "attention_plan": str(out), "summary": plan["summary"]}, sort_keys=True))


class MPSDenseBench:
    def __init__(self, dtype):
        if not torch.backends.mps.is_available():
            raise RuntimeError("MPS is not available; dense benchmark requires Apple GPU")
        self.device = torch.device("mps")
        self.dtype = dtype
        self.cache = {}

    def tensor(self, key, shape):
        cache_key = (key, tuple(int(x) for x in shape))
        if cache_key not in self.cache:
            self.cache[cache_key] = torch.empty(shape, device=self.device, dtype=self.dtype)
        return self.cache[cache_key]

    def projection_once(self, plan):
        last = None
        for group in plan["groups"]:
            for spec in group["matmuls"]:
                m, n, k = int(spec["m"]), int(spec["n"]), int(spec["k"])
                x = self.tensor(f"{group['name']}:{spec.get('weight', '')}:x", (m, k))
                w = self.tensor(f"{group['name']}:{spec.get('weight', '')}:w", (n, k))
                last = x @ w.T
        return last

    def attention_once(self, plan):
        last = None
        for group in plan["groups"]:
            heads = int(group["heads"])
            q_tokens = int(group["query_tokens"])
            kv_tokens = int(group["key_value_tokens"])
            head_dim = int(group["head_dim"])
            if group["semantic_n"] == "key_tokens":
                q = self.tensor(f"{group['name']}:q", (heads, q_tokens, head_dim))
                k = self.tensor(f"{group['name']}:k", (heads, kv_tokens, head_dim))
                last = q @ k.transpose(-2, -1)
            else:
                scores = self.tensor(f"{group['name']}:scores", (heads, q_tokens, kv_tokens))
                v = self.tensor(f"{group['name']}:v", (heads, kv_tokens, head_dim))
                last = scores @ v
        return last

    def time_callable(self, fn, warmup, iterations):
        for _ in range(warmup):
            fn()
        sync()
        samples = []
        for _ in range(iterations):
            started = time.perf_counter()
            out = fn()
            sync()
            elapsed = time.perf_counter() - started
            samples.append(elapsed)
            if out is not None:
                # Touch metadata so Python keeps the operation result live until sync.
                _ = out.shape
        return samples


def dense_benchmark(args):
    out_dir = pathlib.Path(args.out_dir).expanduser()
    dtype = parse_dtype(args.dtype)
    bench = MPSDenseBench(dtype)
    dual_projection = read_json(out_dir / "dual_block_0_1024x1024.projectionplan.json")
    single_projection = read_json(out_dir / "single_block_0_1024x1024.projectionplan.json")
    dual_attention = read_json(out_dir / "dual_block_0_1024x1024.attentionplan.json")
    single_attention = read_json(out_dir / "single_block_0_1024x1024.attentionplan.json")

    def run_one(name, kind, plan, fn):
        samples = bench.time_callable(lambda: fn(plan), args.warmup, args.iterations)
        return {
            "name": name,
            "kind": kind,
            "samples_seconds": samples,
            "median_seconds": median_seconds(samples),
            "min_seconds": min(samples),
            "max_seconds": max(samples),
            "counted_gflops": float(plan["summary"]["counted_gflops"]),
            "effective_tflops_per_second_median": float(plan["summary"]["counted_gflops"]) / 1000 / median_seconds(samples),
        }

    results = [
        run_one("dual_projection", "projection", dual_projection, bench.projection_once),
        run_one("single_projection", "projection", single_projection, bench.projection_once),
        run_one("dual_attention_qk_av", "attention", dual_attention, bench.attention_once),
        run_one("single_attention_qk_av", "attention", single_attention, bench.attention_once),
    ]
    by_name = {item["name"]: item for item in results}

    dual_blocks = int(args.dual_blocks)
    single_blocks = int(args.single_blocks)
    steps = int(args.steps)
    projection_seconds_per_step = (
        dual_blocks * by_name["dual_projection"]["median_seconds"]
        + single_blocks * by_name["single_projection"]["median_seconds"]
    )
    attention_seconds_per_step = (
        dual_blocks * by_name["dual_attention_qk_av"]["median_seconds"]
        + single_blocks * by_name["single_attention_qk_av"]["median_seconds"]
    )
    projection_gflops_per_step = (
        dual_blocks * by_name["dual_projection"]["counted_gflops"]
        + single_blocks * by_name["single_projection"]["counted_gflops"]
    )
    attention_gflops_per_step = (
        dual_blocks * by_name["dual_attention_qk_av"]["counted_gflops"]
        + single_blocks * by_name["single_attention_qk_av"]["counted_gflops"]
    )
    dense_seconds_28 = (projection_seconds_per_step + attention_seconds_per_step) * steps
    dense_gflops_per_step = projection_gflops_per_step + attention_gflops_per_step
    projection_seconds_28 = projection_seconds_per_step * steps
    attention_seconds_28 = attention_seconds_per_step * steps
    tops = float(args.ane_tops) * 1e12
    projection_floor = projection_gflops_per_step * steps * 1e9 / tops
    attention_floor = attention_gflops_per_step * steps * 1e9 / tops
    dense_floor = dense_gflops_per_step * steps * 1e9 / tops
    ideal_savings = dense_seconds_28 - dense_floor
    summary = {
        "created": time.time(),
        "shape": "1024x1024 batch1 max_text512",
        "dtype": str(dtype).replace("torch.", ""),
        "device": "mps",
        "warmup": args.warmup,
        "iterations": args.iterations,
        "results": results,
        "dual_blocks": dual_blocks,
        "single_blocks": single_blocks,
        "steps": steps,
        "gpu_only_render_seconds_reference": args.gpu_render_seconds,
        "projection_gflops_per_step": projection_gflops_per_step,
        "attention_gflops_per_step": attention_gflops_per_step,
        "dense_gflops_per_step": dense_gflops_per_step,
        "projection_seconds_28_steps_mps_estimate": projection_seconds_28,
        "attention_seconds_28_steps_mps_estimate": attention_seconds_28,
        "dense_seconds_28_steps_mps_estimate": dense_seconds_28,
        "dense_mps_share_of_render": dense_seconds_28 / args.gpu_render_seconds,
        "projection_floor_28_steps_at_38tops": projection_floor,
        "attention_floor_28_steps_at_38tops": attention_floor,
        "dense_floor_28_steps_at_38tops": dense_floor,
        "ideal_floor_savings_seconds_28_steps": ideal_savings,
        "ideal_floor_savings_share_of_render": ideal_savings / args.gpu_render_seconds,
        "notes": (
            "Measured synthetic MPS dense matmuls from captured FLUX projection and attention plans. "
            "This is measured MPS work, not measured ANE execution. Softmax, norms, activations, residuals, "
            "scheduler, and integration overhead excluded."
        ),
    }
    out = pathlib.Path(args.out).expanduser() if args.out else out_dir / "dense_slice_1024x1024_rerun.json"
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    tmp.replace(out)
    print(
        json.dumps(
            {
                "ok": True,
                "dense_benchmark": str(out),
                "dense_seconds_28_steps_mps_estimate": dense_seconds_28,
                "dense_floor_28_steps_at_38tops": dense_floor,
                "ideal_floor_savings_seconds_28_steps": ideal_savings,
                "ideal_floor_savings_share_of_render": ideal_savings / args.gpu_render_seconds,
            },
            sort_keys=True,
        )
    )


def manifest_block_inputs(manifest, device, dtype):
    capture = next(record for record in manifest["captures"] if record["phase"] == "pre")
    kwargs = capture["kwargs"]
    hidden_shape = kwargs["hidden_states"]["shape"]
    text_shape = kwargs["encoder_hidden_states"]["shape"]
    temb_shape = kwargs["temb"]["shape"]
    rotary_shapes = [item["shape"] for item in kwargs["image_rotary_emb"]]
    return {
        "hidden_states": torch.zeros(hidden_shape, device=device, dtype=dtype),
        "encoder_hidden_states": torch.zeros(text_shape, device=device, dtype=dtype),
        "temb": torch.zeros(temb_shape, device=device, dtype=dtype),
        "image_rotary_emb": tuple(torch.zeros(shape, device=device, dtype=torch.float32) for shape in rotary_shapes),
        "joint_attention_kwargs": {},
    }


def block_benchmark(args):
    if not torch.backends.mps.is_available():
        raise RuntimeError("MPS is not available; block benchmark requires Apple GPU")
    model_dir = pathlib.Path(args.model_dir).expanduser()
    out_dir = pathlib.Path(args.out_dir).expanduser()
    dtype = parse_dtype(args.dtype)
    device = torch.device("mps")

    pipe = FluxPipeline.from_pretrained(
        model_dir,
        torch_dtype=dtype,
        local_files_only=True,
    )
    pipe.to(device)
    transformer = pipe.transformer
    transformer.eval()

    dual_manifest = read_json(out_dir / "dual_block_0_1024x1024.json")
    single_manifest = read_json(out_dir / "single_block_0_1024x1024.json")
    dual_inputs = manifest_block_inputs(dual_manifest, device, dtype)
    single_inputs = manifest_block_inputs(single_manifest, device, dtype)
    dual_block = transformer.transformer_blocks[int(args.dual_index)]
    single_block = transformer.single_transformer_blocks[int(args.single_index)]

    def time_block(block, inputs):
        def run():
            with torch.no_grad():
                return block(**inputs)
        for _ in range(args.warmup):
            run()
        sync()
        samples = []
        for _ in range(args.iterations):
            started = time.perf_counter()
            out = run()
            sync()
            samples.append(time.perf_counter() - started)
            _ = out[0].shape, out[1].shape
        return samples

    dual_samples = time_block(dual_block, dual_inputs)
    single_samples = time_block(single_block, single_inputs)
    dual_median = median_seconds(dual_samples)
    single_median = median_seconds(single_samples)
    steps = int(args.steps)
    dual_blocks = int(args.dual_blocks)
    single_blocks = int(args.single_blocks)
    stack_seconds_per_step = dual_blocks * dual_median + single_blocks * single_median
    stack_seconds = stack_seconds_per_step * steps

    dense_summary_path = pathlib.Path(args.dense_summary).expanduser()
    dense = read_json(dense_summary_path) if dense_summary_path.exists() else {}
    dense_seconds = dense.get("dense_seconds_28_steps_mps_estimate")
    dense_share_of_measured_blocks = dense_seconds / stack_seconds if dense_seconds else None

    result = {
        "created": time.time(),
        "shape": "1024x1024 batch1 max_text512",
        "device": "mps",
        "dtype": str(dtype).replace("torch.", ""),
        "model_dir": str(model_dir),
        "warmup": args.warmup,
        "iterations": args.iterations,
        "dual": {
            "block_index": int(args.dual_index),
            "samples_seconds": dual_samples,
            "median_seconds": dual_median,
            "min_seconds": min(dual_samples),
            "max_seconds": max(dual_samples),
        },
        "single": {
            "block_index": int(args.single_index),
            "samples_seconds": single_samples,
            "median_seconds": single_median,
            "min_seconds": min(single_samples),
            "max_seconds": max(single_samples),
        },
        "scaled": {
            "steps": steps,
            "dual_blocks": dual_blocks,
            "single_blocks": single_blocks,
            "block_stack_seconds_per_step": stack_seconds_per_step,
            "block_stack_seconds": stack_seconds,
            "dense_seconds_from_summary": dense_seconds,
            "dense_share_of_measured_block_stack": dense_share_of_measured_blocks,
            "non_dense_or_measurement_gap_seconds": (stack_seconds - dense_seconds) if dense_seconds else None,
        },
        "notes": (
            "Measured real Diffusers FLUX transformer block modules on MPS with synthetic captured-shape tensors. "
            "This measures module execution cost, not final image correctness and not ANE execution."
        ),
    }
    out = pathlib.Path(args.out).expanduser() if args.out else out_dir / "block_stack_1024x1024_benchmark.json"
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    tmp.replace(out)
    print(
        json.dumps(
            {
                "ok": True,
                "block_benchmark": str(out),
                "dual_median_ms": dual_median * 1000,
                "single_median_ms": single_median * 1000,
                "block_stack_seconds_28_steps": stack_seconds,
                "dense_seconds_28_steps": dense_seconds,
                "dense_share_of_measured_block_stack": dense_share_of_measured_blocks,
            },
            sort_keys=True,
        )
    )


def parse_steps_list(value):
    steps = []
    for part in value.split(","):
        part = part.strip()
        if part:
            steps.append(int(part))
    if not steps:
        raise ValueError("--steps-list must include at least one step count")
    return steps


def latent_pipeline_benchmark(args):
    if not torch.backends.mps.is_available():
        raise RuntimeError("MPS is not available; latent pipeline benchmark requires Apple GPU")
    model_dir = pathlib.Path(args.model_dir).expanduser()
    out_dir = pathlib.Path(args.out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    dtype = parse_dtype(args.dtype)
    device = torch.device("mps")
    steps_list = parse_steps_list(args.steps_list)

    pipe = FluxPipeline.from_pretrained(
        model_dir,
        torch_dtype=dtype,
        local_files_only=True,
    )
    pipe.to(device)
    pipe.set_progress_bar_config(disable=True)

    results = []
    for steps in steps_list:
        samples = []
        for index in range(args.iterations):
            generator = torch.Generator(device="cpu").manual_seed(args.seed + index)
            started = time.perf_counter()
            image = pipe(
                prompt=args.prompt,
                width=args.width,
                height=args.height,
                guidance_scale=args.guidance,
                num_inference_steps=steps,
                generator=generator,
                output_type="latent",
            ).images
            sync()
            elapsed = time.perf_counter() - started
            samples.append(elapsed)
            _ = getattr(image, "shape", None)
        results.append(
            {
                "steps": steps,
                "samples_seconds": samples,
                "median_seconds": median_seconds(samples),
                "min_seconds": min(samples),
                "max_seconds": max(samples),
                "median_seconds_per_step": median_seconds(samples) / steps,
            }
        )

    slope = None
    intercept = None
    if len(results) >= 2:
        first = results[0]
        last = results[-1]
        slope = (last["median_seconds"] - first["median_seconds"]) / (last["steps"] - first["steps"])
        intercept = first["median_seconds"] - slope * first["steps"]

    block_path = pathlib.Path(args.block_benchmark).expanduser()
    block = read_json(block_path) if block_path.exists() else None
    block_seconds_per_step = None
    if block:
        block_seconds_per_step = block["scaled"]["block_stack_seconds_per_step"]

    result = {
        "created": time.time(),
        "shape": f"{args.width}x{args.height} batch1",
        "device": "mps",
        "dtype": str(dtype).replace("torch.", ""),
        "model_dir": str(model_dir),
        "prompt": args.prompt,
        "guidance": args.guidance,
        "seed": args.seed,
        "iterations": args.iterations,
        "results": results,
        "slope_seconds_per_step": slope,
        "intercept_seconds": intercept,
        "block_stack_seconds_per_step": block_seconds_per_step,
        "block_stack_share_of_pipeline_step_slope": (block_seconds_per_step / slope) if slope else None,
        "notes": (
            "Measured real FluxPipeline latent-output runs on MPS. output_type='latent' skips VAE decode. "
            "The slope across step counts approximates denoising cost per step plus pipeline overhead."
        ),
    }
    out = pathlib.Path(args.out).expanduser() if args.out else out_dir / "latent_pipeline_1024x1024_benchmark.json"
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    tmp.replace(out)
    print(
        json.dumps(
            {
                "ok": True,
                "latent_pipeline_benchmark": str(out),
                "slope_seconds_per_step": slope,
                "intercept_seconds": intercept,
                "block_stack_seconds_per_step": block_seconds_per_step,
                "block_stack_share_of_pipeline_step_slope": result["block_stack_share_of_pipeline_step_slope"],
            },
            sort_keys=True,
        )
    )


def time_samples(fn, warmup, iterations):
    for _ in range(warmup):
        out = fn()
        _ = tensor_info(out)
    sync()
    samples = []
    for _ in range(iterations):
        started = time.perf_counter()
        out = fn()
        sync()
        samples.append(time.perf_counter() - started)
        _ = tensor_info(out)
    return samples


def component_benchmark(args):
    if not torch.backends.mps.is_available():
        raise RuntimeError("MPS is not available; component benchmark requires Apple GPU")
    model_dir = pathlib.Path(args.model_dir).expanduser()
    out_dir = pathlib.Path(args.out_dir).expanduser()
    dtype = parse_dtype(args.dtype)
    device = torch.device("mps")

    pipe = FluxPipeline.from_pretrained(
        model_dir,
        torch_dtype=dtype,
        local_files_only=True,
    )
    pipe.to(device)
    transformer = pipe.transformer
    transformer.eval()

    dual_manifest = read_json(out_dir / "dual_block_0_1024x1024.json")
    single_manifest = read_json(out_dir / "single_block_0_1024x1024.json")
    dual_inputs = manifest_block_inputs(dual_manifest, device, dtype)
    single_inputs = manifest_block_inputs(single_manifest, device, dtype)
    dual = transformer.transformer_blocks[int(args.dual_index)]
    single = transformer.single_transformer_blocks[int(args.single_index)]

    with torch.no_grad():
        dual_norm_hidden, _, _, _, _ = dual.norm1(dual_inputs["hidden_states"], emb=dual_inputs["temb"])
        dual_norm_text, _, _, _, _ = dual.norm1_context(
            dual_inputs["encoder_hidden_states"], emb=dual_inputs["temb"]
        )
        single_joint = torch.cat([single_inputs["encoder_hidden_states"], single_inputs["hidden_states"]], dim=1)
        single_norm_joint, _ = single.norm(single_joint, emb=single_inputs["temb"])
        single_attn_output = single.attn(
            hidden_states=single_norm_joint,
            image_rotary_emb=single_inputs["image_rotary_emb"],
        )
        single_mlp_output = single.act_mlp(single.proj_mlp(single_norm_joint))
        single_proj_out_input = torch.cat([single_attn_output, single_mlp_output], dim=2)
        sync()

    components = []

    def add(name, block_type, fn, scale_count):
        samples = time_samples(lambda: fn(), args.warmup, args.iterations)
        components.append(
            {
                "name": name,
                "block_type": block_type,
                "samples_seconds": samples,
                "median_seconds": median_seconds(samples),
                "min_seconds": min(samples),
                "max_seconds": max(samples),
                "scaled_seconds_28_steps": median_seconds(samples) * scale_count * args.steps,
            }
        )

    with torch.no_grad():
        add(
            "dual_adaln_modulation_pair",
            "dual",
            lambda: (
                dual.norm1(dual_inputs["hidden_states"], emb=dual_inputs["temb"]),
                dual.norm1_context(dual_inputs["encoder_hidden_states"], emb=dual_inputs["temb"]),
            ),
            args.dual_blocks,
        )
        add(
            "dual_attention_module",
            "dual",
            lambda: dual.attn(
                hidden_states=dual_norm_hidden,
                encoder_hidden_states=dual_norm_text,
                image_rotary_emb=dual_inputs["image_rotary_emb"],
            ),
            args.dual_blocks,
        )
        add(
            "dual_mlp_pair",
            "dual",
            lambda: (
                dual.ff(dual.norm2(dual_inputs["hidden_states"])),
                dual.ff_context(dual.norm2_context(dual_inputs["encoder_hidden_states"])),
            ),
            args.dual_blocks,
        )
        add(
            "single_adaln_modulation",
            "single",
            lambda: single.norm(single_joint, emb=single_inputs["temb"]),
            args.single_blocks,
        )
        add(
            "single_attention_module",
            "single",
            lambda: single.attn(
                hidden_states=single_norm_joint,
                image_rotary_emb=single_inputs["image_rotary_emb"],
            ),
            args.single_blocks,
        )
        add(
            "single_mlp_in_activation",
            "single",
            lambda: single.act_mlp(single.proj_mlp(single_norm_joint)),
            args.single_blocks,
        )
        add(
            "single_fused_output_projection",
            "single",
            lambda: single.proj_out(single_proj_out_input),
            args.single_blocks,
        )

    block_path = pathlib.Path(args.block_benchmark).expanduser()
    dense_path = pathlib.Path(args.dense_summary).expanduser()
    block_bench = read_json(block_path) if block_path.exists() else None
    dense = read_json(dense_path) if dense_path.exists() else None
    total_scaled = sum(item["scaled_seconds_28_steps"] for item in components)
    result = {
        "created": time.time(),
        "shape": "1024x1024 batch1 max_text512",
        "device": "mps",
        "dtype": str(dtype).replace("torch.", ""),
        "model_dir": str(model_dir),
        "warmup": args.warmup,
        "iterations": args.iterations,
        "steps": args.steps,
        "components": components,
        "summary": {
            "component_sum_scaled_seconds_28_steps": total_scaled,
            "block_stack_seconds_28_steps": (block_bench or {}).get("scaled", {}).get("block_stack_seconds"),
            "dense_seconds_28_steps": (dense or {}).get("dense_seconds_28_steps_mps_estimate"),
            "component_sum_vs_block_stack": (
                total_scaled / block_bench["scaled"]["block_stack_seconds"] if block_bench else None
            ),
        },
        "notes": (
            "Measured real FLUX block submodules on MPS with independent synchronization per component. "
            "Component timings identify cost distribution but should not be summed as an exact replacement for full block timing."
        ),
    }
    out = pathlib.Path(args.out).expanduser() if args.out else out_dir / "component_1024x1024_benchmark.json"
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    tmp.replace(out)
    print(
        json.dumps(
            {
                "ok": True,
                "component_benchmark": str(out),
                "component_sum_scaled_seconds_28_steps": total_scaled,
                "block_stack_seconds_28_steps": result["summary"]["block_stack_seconds_28_steps"],
                "component_sum_vs_block_stack": result["summary"]["component_sum_vs_block_stack"],
            },
            sort_keys=True,
        )
    )


def require_aneforge():
    try:
        import aneforge as af
    except ImportError as exc:
        raise RuntimeError("aneforge is not installed; run `uv pip install --python .venv/bin/python aneforge`") from exc
    return af


def mps_linear_samples(x_np, w_np, iterations=5, warmup=2):
    x_t = torch.from_numpy(x_np).to("mps")
    w_t = torch.from_numpy(w_np).to("mps")
    for _ in range(warmup):
        _ = x_t @ w_t.T
    sync()
    samples = []
    for _ in range(iterations):
        started = time.perf_counter()
        _ = x_t @ w_t.T
        sync()
        samples.append(time.perf_counter() - started)
    return samples


def aneforge_conv1x1_samples(af, x_np, w_np, iterations=10, compress="int8"):
    m, k = x_np.shape
    n = w_np.shape[0]
    x_conv = x_np.T[None, :, None, :].copy()
    w_conv = w_np[:, :, None, None].copy()
    x = af.input((1, k, 1, m))
    y = af.conv(x, w_conv)
    started = time.perf_counter()
    net = af.compile(y, validate=False, compress=compress)
    compile_seconds = time.perf_counter() - started
    input_view = net.input_view()
    input_view[...] = x_conv
    net.execute()
    output_view = net.output_view()
    samples = []
    for _ in range(iterations):
        started = time.perf_counter()
        net.execute()
        _ = output_view.shape
        samples.append(time.perf_counter() - started)
    out_sum = float(output_view.astype("float32").sum())
    return samples, compile_seconds, out_sum


def aneforge_conv1x1_chunked_samples(af, x_np, w_np, chunks, iterations=10, compress="int8"):
    m, k = x_np.shape
    x_conv = x_np.T[None, :, None, :].copy()
    x = af.input((1, k, 1, m))
    outputs = []
    offset = 0
    for chunk in chunks:
        if offset == 0 and int(chunk) == k:
            x_slice = x
        else:
            x_slice = x.slice_by_size((0, offset, 0, 0), (1, int(chunk), 1, m))
        w_slice = w_np[:, offset : offset + int(chunk)][:, :, None, None].copy()
        outputs.append(af.conv(x_slice, w_slice))
        offset += int(chunk)
    if offset != k:
        raise ValueError(f"chunks sum to {offset}, expected {k}")
    y = outputs[0]
    for output in outputs[1:]:
        y = y + output
    started = time.perf_counter()
    net = af.compile(y, validate=False, compress=compress)
    compile_seconds = time.perf_counter() - started
    input_view = net.input_view()
    input_view[...] = x_conv
    net.execute()
    output_view = net.output_view()
    samples = []
    for _ in range(iterations):
        started = time.perf_counter()
        net.execute()
        _ = output_view.shape
        samples.append(time.perf_counter() - started)
    out_sum = float(output_view.astype("float32").sum())
    return samples, compile_seconds, out_sum


def mps_sdpa_samples(q_np, k_np, v_np, iterations=3, warmup=1):
    q_t = torch.from_numpy(q_np).to("mps")
    k_t = torch.from_numpy(k_np).to("mps")
    v_t = torch.from_numpy(v_np).to("mps")
    for _ in range(warmup):
        _ = torch.nn.functional.scaled_dot_product_attention(q_t, k_t, v_t)
    sync()
    samples = []
    for _ in range(iterations):
        started = time.perf_counter()
        _ = torch.nn.functional.scaled_dot_product_attention(q_t, k_t, v_t)
        sync()
        samples.append(time.perf_counter() - started)
    return samples


def aneforge_tiled_sdpa_samples(af, q_np, k_np, v_np, tiles, iterations=5, compress="int8"):
    from aneforge import graph as g

    _, heads, seq_q, head_dim = q_np.shape
    _, _, seq_kv, _ = k_np.shape
    qh = g.input((heads, seq_q, head_dim))
    kh = g.input((heads, seq_kv, head_dim))
    vh = g.input((heads, seq_kv, head_dim))
    kt = kh.transpose([0, 2, 1])
    scale = 1.0 / (head_dim**0.5)
    tile_size = -(-seq_q // int(tiles))
    parts = []
    for start in range(0, seq_q, tile_size):
        tile = min(tile_size, seq_q - start)
        qt = qh.slice_by_size([0, start, 0], [heads, tile, head_dim])
        parts.append(((qt @ kt) * scale).softmax(-1) @ vh)
    y = g.concat(parts, axis=1) if len(parts) > 1 else parts[0]
    started = time.perf_counter()
    net = af.compile(y, validate=False, compress=compress)
    compile_seconds = time.perf_counter() - started
    q_arg = np.ascontiguousarray(q_np[0])
    k_arg = np.ascontiguousarray(k_np[0])
    v_arg = np.ascontiguousarray(v_np[0])
    net(q_arg, k_arg, v_arg)
    samples = []
    out = None
    for _ in range(iterations):
        started = time.perf_counter()
        out = net(q_arg, k_arg, v_arg)
        samples.append(time.perf_counter() - started)
    out_sum = float(np.asarray(out).astype("float32").sum()) if out is not None else 0.0
    return samples, compile_seconds, out_sum


def aneforge_projection_benchmark(args):
    if not torch.backends.mps.is_available():
        raise RuntimeError("MPS is not available; benchmark needs the GPU baseline")
    af = require_aneforge()
    out_dir = pathlib.Path(args.out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    cases = [
        ("dual_image_qkv_fused", 4096, 3072, 9216, 19),
        ("dual_image_mlp_in", 4096, 3072, 12288, 19),
        ("dual_image_mlp_out", 4096, 12288, 3072, 19),
        ("single_joint_qkv_fused", 4608, 3072, 9216, 38),
        ("single_joint_mlp_in", 4608, 3072, 12288, 38),
        ("single_fused_out", 4608, 15360, 3072, 38),
        ("dual_text_qkv_fused", 512, 3072, 9216, 19),
        ("dual_text_mlp_in", 512, 3072, 12288, 19),
    ]
    results = []
    for index, (name, m, k, n, blocks_per_step) in enumerate(cases):
        rng = torch.Generator(device="cpu").manual_seed(args.seed + index)
        x_np = torch.randn((m, k), generator=rng, dtype=torch.float32).to(torch.float16).numpy()
        w_np = torch.randn((n, k), generator=rng, dtype=torch.float32).to(torch.float16).numpy()
        flops = 2 * m * n * k
        mps = mps_linear_samples(x_np, w_np, iterations=args.mps_iterations, warmup=args.warmup)
        mps_median = median_seconds(mps)
        item = {
            "name": name,
            "shape_mkn": [m, k, n],
            "blocks_per_step": blocks_per_step,
            "gflops": flops / 1e9,
            "mps_median_ms": mps_median * 1000,
            "mps_samples_ms": [round(sample * 1000, 3) for sample in mps],
            "mps_tflops": flops / mps_median / 1e12,
        }
        try:
            ane, compile_seconds, out_sum = aneforge_conv1x1_samples(
                af, x_np, w_np, iterations=args.ane_iterations, compress=args.compress
            )
            ane_median = median_seconds(ane)
            item.update(
                {
                    "ane_conv1x1_median_ms": ane_median * 1000,
                    "ane_samples_ms": [round(sample * 1000, 3) for sample in ane],
                    "ane_tflops": flops / ane_median / 1e12,
                    "speedup_ane_vs_mps": mps_median / ane_median,
                    "compile_s": compile_seconds,
                    "compress": args.compress,
                    "out_sum": out_sum,
                    "saved_ms_per_invocation": (mps_median - ane_median) * 1000,
                    "saved_seconds_28_steps": (mps_median - ane_median) * blocks_per_step * args.steps,
                }
            )
        except Exception as exc:
            item["error"] = repr(exc)
        results.append(item)

    positive = [item for item in results if item.get("saved_seconds_28_steps", 0) > 0]
    negative = [item for item in results if item.get("saved_seconds_28_steps", 0) < 0]
    data = {
        "version": 1,
        "created": time.time(),
        "shape": "1024x1024 batch1 max_text512",
        "runtime": "ANEForge direct e5rt; no CoreML; conv1x1 projection layout; zero-copy input/output views",
        "compress": args.compress,
        "steps": args.steps,
        "mps_iterations": args.mps_iterations,
        "ane_iterations": args.ane_iterations,
        "results": results,
        "positive_cases": [item["name"] for item in positive],
        "negative_cases": [item["name"] for item in negative],
        "positive_saved_seconds_28_steps": sum(item["saved_seconds_28_steps"] for item in positive),
        "net_saved_seconds_28_steps_if_all_tested_replaced": sum(
            item.get("saved_seconds_28_steps", 0) for item in results
        ),
        "notes": [
            "This is measured direct-ANE execution through ANEForge/e5rt, not CoreML.",
            "Only positive cases should be considered for a selective offload plan.",
            "High-K output projections currently regress badly and must stay on MPS unless re-lowered.",
        ],
    }
    out = pathlib.Path(args.out).expanduser() if args.out else out_dir / "aneforge_projection_1024x1024_benchmark.json"
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    tmp.replace(out)
    print(
        json.dumps(
            {
                "ok": True,
                "aneforge_projection_benchmark": str(out),
                "positive_saved_seconds_28_steps": data["positive_saved_seconds_28_steps"],
                "net_saved_seconds_28_steps_if_all_tested_replaced": data[
                    "net_saved_seconds_28_steps_if_all_tested_replaced"
                ],
                "positive_cases": data["positive_cases"],
                "negative_cases": data["negative_cases"],
            },
            sort_keys=True,
        )
    )


def aneforge_optimized_projection_plan(args):
    if not torch.backends.mps.is_available():
        raise RuntimeError("MPS is not available; benchmark needs the GPU baseline")
    af = require_aneforge()
    out_dir = pathlib.Path(args.out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    plan = [
        ("dual_image_input_fused", 4096, 3072, [9216, 3072, 12288], [3072], 19),
        ("dual_image_mlp_out_chunk4", 4096, 12288, [3072], [3072, 3072, 3072, 3072], 19),
        ("dual_text_input_fused", 512, 3072, [9216, 3072, 12288], [3072], 19),
        ("dual_text_mlp_out_chunk8", 512, 12288, [3072], [1536] * 8, 19),
        ("single_input_fused", 4608, 3072, [9216, 12288], [3072], 38),
        ("single_fused_out_chunk4", 4608, 15360, [3072], [3840] * 4, 38),
    ]
    results = []
    for index, (name, m, k, parts, chunks, blocks_per_step) in enumerate(plan):
        rng = torch.Generator(device="cpu").manual_seed(args.seed + index)
        x_np = torch.randn((m, k), generator=rng, dtype=torch.float32).to(torch.float16).numpy()
        weights = [
            torch.randn((n, k), generator=rng, dtype=torch.float32).to(torch.float16).numpy() for n in parts
        ]
        w_np = np.concatenate(weights, axis=0)
        part_mps = [median_seconds(mps_linear_samples(x_np, weight, args.mps_iterations, args.warmup)) for weight in weights]
        combined_mps = median_seconds(mps_linear_samples(x_np, w_np, args.mps_iterations, args.warmup))
        ane, compile_seconds, out_sum = aneforge_conv1x1_chunked_samples(
            af, x_np, w_np, chunks, args.ane_iterations, args.compress
        )
        ane_median = median_seconds(ane)
        mps_separate = sum(part_mps)
        flops = 2 * m * k * sum(parts)
        results.append(
            {
                "name": name,
                "shape_mkn": [m, k, sum(parts)],
                "parts": parts,
                "chunks": chunks,
                "blocks_per_step": blocks_per_step,
                "gflops": flops / 1e9,
                "mps_separate_ms": mps_separate * 1000,
                "mps_combined_ms": combined_mps * 1000,
                "ane_ms": ane_median * 1000,
                "speedup_vs_mps_separate": mps_separate / ane_median,
                "speedup_vs_mps_combined": combined_mps / ane_median,
                "saved_seconds_28_vs_mps_separate": (mps_separate - ane_median) * blocks_per_step * args.steps,
                "saved_seconds_28_vs_mps_combined": (combined_mps - ane_median) * blocks_per_step * args.steps,
                "mps_part_ms": [round(sample * 1000, 3) for sample in part_mps],
                "ane_samples_ms": [round(sample * 1000, 3) for sample in ane],
                "ane_tflops": flops / ane_median / 1e12,
                "mps_separate_tflops": flops / mps_separate / 1e12,
                "compile_s": compile_seconds,
                "out_sum": out_sum,
            }
        )

    data = {
        "version": 1,
        "created": time.time(),
        "shape": "1024x1024 batch1 max_text512",
        "runtime": "ANEForge direct e5rt optimized selective projection plan; no CoreML; fused same-input projections; chunked high-K outputs",
        "compress": args.compress,
        "steps": args.steps,
        "results": results,
        "total_saved_seconds_28_vs_mps_separate": sum(
            item["saved_seconds_28_vs_mps_separate"] for item in results
        ),
        "total_saved_seconds_28_vs_mps_combined": sum(
            item["saved_seconds_28_vs_mps_combined"] for item in results
        ),
        "all_cases_positive_vs_mps_separate": all(
            item["saved_seconds_28_vs_mps_separate"] > 0 for item in results
        ),
        "notes": [
            "This is measured direct-ANE execution through ANEForge/e5rt, not CoreML.",
            "Same-input projections are fused into larger 1x1 conv programs.",
            "High-K output projections are split along K and summed in-graph to avoid the slow single-conv lowering.",
        ],
    }
    out = pathlib.Path(args.out).expanduser() if args.out else out_dir / "aneforge_optimized_projection_plan_1024x1024.json"
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    tmp.replace(out)
    print(
        json.dumps(
            {
                "ok": True,
                "aneforge_optimized_projection_plan": str(out),
                "total_saved_seconds_28_vs_mps_separate": data["total_saved_seconds_28_vs_mps_separate"],
                "total_saved_seconds_28_vs_mps_combined": data["total_saved_seconds_28_vs_mps_combined"],
                "all_cases_positive": data["all_cases_positive_vs_mps_separate"],
            },
            sort_keys=True,
        )
    )


def aneforge_attention_benchmark(args):
    if not torch.backends.mps.is_available():
        raise RuntimeError("MPS is not available; benchmark needs the GPU baseline")
    af = require_aneforge()
    out_dir = pathlib.Path(args.out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    cases = [
        ("sdpa_tile_512", 512, 512, 24, 128, 1, 0),
        ("sdpa_tile_1024_tiled4", 1024, 1024, 24, 128, 4, 0),
        ("flux_joint_attention_core_4608_tiled8", 4608, 4608, 24, 128, 8, 57),
    ]
    results = []
    for index, (name, seq_q, seq_kv, heads, head_dim, tiles, invocations_per_step) in enumerate(cases):
        rng = torch.Generator(device="cpu").manual_seed(args.seed + index)
        shape_q = (1, heads, seq_q, head_dim)
        shape_kv = (1, heads, seq_kv, head_dim)
        q_np = torch.randn(shape_q, generator=rng, dtype=torch.float32).mul(0.1).to(torch.float16).numpy()
        k_np = torch.randn(shape_kv, generator=rng, dtype=torch.float32).mul(0.1).to(torch.float16).numpy()
        v_np = torch.randn(shape_kv, generator=rng, dtype=torch.float32).mul(0.1).to(torch.float16).numpy()
        flops = 4 * heads * seq_q * seq_kv * head_dim
        mps = mps_sdpa_samples(q_np, k_np, v_np, args.mps_iterations, args.warmup)
        mps_median = median_seconds(mps)
        item = {
            "name": name,
            "shape_bhsd": [1, heads, seq_q, head_dim],
            "kv_tokens": seq_kv,
            "tiles": tiles,
            "invocations_per_step": invocations_per_step,
            "gflops_qk_plus_av": flops / 1e9,
            "mps_median_ms": mps_median * 1000,
            "mps_samples_ms": [round(sample * 1000, 3) for sample in mps],
            "mps_tflops": flops / mps_median / 1e12,
        }
        try:
            ane, compile_seconds, out_sum = aneforge_tiled_sdpa_samples(
                af, q_np, k_np, v_np, tiles, args.ane_iterations, args.compress
            )
            ane_median = median_seconds(ane)
            item.update(
                {
                    "ane_tiled_median_ms": ane_median * 1000,
                    "ane_samples_ms": [round(sample * 1000, 3) for sample in ane],
                    "ane_tflops": flops / ane_median / 1e12,
                    "speedup_ane_vs_mps": mps_median / ane_median,
                    "compile_s": compile_seconds,
                    "out_sum": out_sum,
                    "saved_ms_per_invocation": (mps_median - ane_median) * 1000,
                    "saved_seconds_28_steps": (
                        (mps_median - ane_median) * invocations_per_step * args.steps
                    ),
                }
            )
        except Exception as exc:
            item["error"] = repr(exc)
        results.append(item)

    full_case = next((item for item in results if item["name"].startswith("flux_joint_attention_core")), None)
    data = {
        "version": 1,
        "created": time.time(),
        "shape": "1024x1024 batch1 max_text512",
        "runtime": "ANEForge direct e5rt tiled SDPA graph; no CoreML; QK softmax AV attention core only",
        "compress": args.compress,
        "steps": args.steps,
        "results": results,
        "full_flux_attention_saved_seconds_28_steps": (
            full_case.get("saved_seconds_28_steps") if full_case else None
        ),
        "notes": [
            "This is measured direct-ANE execution through ANEForge/e5rt, not CoreML.",
            "The full FLUX 4608-token case uses the tiled graph fallback, not the native small-sequence SDPA path.",
            "The benchmark measures the attention core only: QK scores, softmax, and AV apply. It excludes QKV/out projections, rotary, reshapes, and residuals.",
        ],
    }
    out = pathlib.Path(args.out).expanduser() if args.out else out_dir / "aneforge_attention_1024x1024_benchmark.json"
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    tmp.replace(out)
    print(
        json.dumps(
            {
                "ok": True,
                "aneforge_attention_benchmark": str(out),
                "full_flux_attention_saved_seconds_28_steps": data["full_flux_attention_saved_seconds_28_steps"],
                "full_flux_attention_speedup": (
                    full_case.get("speedup_ane_vs_mps") if full_case else None
                ),
            },
            sort_keys=True,
        )
    )


def read_json(path):
    return json.loads(pathlib.Path(path).expanduser().read_text())


def tensor_bytes(shape, bytes_per_element=2):
    total = 1
    for dim in shape:
        total *= int(dim)
    return total * bytes_per_element


def mib(value):
    return value / (1024 * 1024)


def fit_summary(path):
    data = read_json(path)
    return data["summary"], data.get("latency_model", {})


def runtime_contract(args):
    out_dir = pathlib.Path(args.out_dir).expanduser()
    dense_summary_path = pathlib.Path(args.dense_summary).expanduser()
    block_benchmark_path = pathlib.Path(args.block_benchmark).expanduser()
    latent_pipeline_path = pathlib.Path(args.latent_pipeline_benchmark).expanduser()
    component_benchmark_path = pathlib.Path(args.component_benchmark).expanduser()
    aneforge_projection_path = pathlib.Path(args.aneforge_projection_benchmark).expanduser()
    aneforge_optimized_projection_path = pathlib.Path(args.aneforge_optimized_projection_plan).expanduser()
    aneforge_attention_path = pathlib.Path(args.aneforge_attention_benchmark).expanduser()
    dense = read_json(dense_summary_path)
    block_bench = read_json(block_benchmark_path) if block_benchmark_path.exists() else None
    latent_bench = read_json(latent_pipeline_path) if latent_pipeline_path.exists() else None
    component_bench = read_json(component_benchmark_path) if component_benchmark_path.exists() else None
    aneforge_projection_bench = read_json(aneforge_projection_path) if aneforge_projection_path.exists() else None
    aneforge_optimized_projection_plan = (
        read_json(aneforge_optimized_projection_path) if aneforge_optimized_projection_path.exists() else None
    )
    aneforge_attention_bench = read_json(aneforge_attention_path) if aneforge_attention_path.exists() else None
    dual_manifest = read_json(out_dir / "dual_block_0_1024x1024.json")
    single_manifest = read_json(out_dir / "single_block_0_1024x1024.json")

    dual_projection_summary, dual_projection_latency = fit_summary(
        out_dir / "dual_block_0_1024x1024.projectionplan.anefit.json"
    )
    single_projection_summary, single_projection_latency = fit_summary(
        out_dir / "single_block_0_1024x1024.projectionplan.anefit.json"
    )
    dual_attention_summary, dual_attention_latency = fit_summary(
        out_dir / "dual_block_0_1024x1024.attentionplan.anefit.json"
    )
    single_attention_summary, single_attention_latency = fit_summary(
        out_dir / "single_block_0_1024x1024.attentionplan.anefit.json"
    )

    capture = next(record for record in dual_manifest["captures"] if record["phase"] == "pre")
    hidden_shape = capture["kwargs"]["hidden_states"]["shape"]
    text_shape = capture["kwargs"]["encoder_hidden_states"]["shape"]
    hidden_bytes = tensor_bytes(hidden_shape)
    text_bytes = tensor_bytes(text_shape)
    block_boundary_bytes = 2 * (hidden_bytes + text_bytes)

    steps = int(args.steps)
    dual_blocks = int(args.dual_blocks)
    single_blocks = int(args.single_blocks)
    blocks_per_step = dual_blocks + single_blocks
    block_invocations = steps * blocks_per_step
    dual_groups_per_block = 8 + 2
    single_groups_per_block = 3 + 2
    group_invocations = steps * (dual_blocks * dual_groups_per_block + single_blocks * single_groups_per_block)

    total_tiles_per_dual = dual_projection_summary["total_chosen_tiles"] + dual_attention_summary["total_chosen_tiles"]
    total_tiles_per_single = single_projection_summary["total_chosen_tiles"] + single_attention_summary["total_chosen_tiles"]
    tile_invocations = steps * (dual_blocks * total_tiles_per_dual + single_blocks * total_tiles_per_single)

    dense_tflops = dense["dense_gflops_per_step"] * steps / 1000
    mps_dense_seconds = dense["dense_seconds_28_steps_mps_estimate"]
    dense_floor_seconds = dense["dense_floor_28_steps_at_38tops"]
    ideal_savings = dense["ideal_floor_savings_seconds_28_steps"]
    gpu_reference = dense["gpu_only_render_seconds_reference"]
    effective_mps_tflops = dense_tflops / mps_dense_seconds
    advertised_ane_tops = 38.0

    contract = {
        "version": 1,
        "created": time.time(),
        "shape": dense["shape"],
        "sources": {
            "dense_summary": str(dense_summary_path),
            "block_benchmark": str(block_benchmark_path) if block_bench else "",
            "latent_pipeline_benchmark": str(latent_pipeline_path) if latent_bench else "",
            "component_benchmark": str(component_benchmark_path) if component_bench else "",
            "aneforge_projection_benchmark": str(aneforge_projection_path) if aneforge_projection_bench else "",
            "aneforge_optimized_projection_plan": (
                str(aneforge_optimized_projection_path) if aneforge_optimized_projection_plan else ""
            ),
            "aneforge_attention_benchmark": str(aneforge_attention_path) if aneforge_attention_bench else "",
            "dual_manifest": str(out_dir / "dual_block_0_1024x1024.json"),
            "single_manifest": str(out_dir / "single_block_0_1024x1024.json"),
            "dual_projection_fit": str(out_dir / "dual_block_0_1024x1024.projectionplan.anefit.json"),
            "single_projection_fit": str(out_dir / "single_block_0_1024x1024.projectionplan.anefit.json"),
            "dual_attention_fit": str(out_dir / "dual_block_0_1024x1024.attentionplan.anefit.json"),
            "single_attention_fit": str(out_dir / "single_block_0_1024x1024.attentionplan.anefit.json"),
        },
        "mps_benchmark_definition": {
            "what_it_is": "isolated synthetic bf16 PyTorch/MPS timing for FLUX-shaped dense matmuls",
            "what_it_is_not": "not a full image render and not a proof of ANE execution",
            "purpose": "estimate how much current GPU dense-matmul time can be removed if the same work runs on a resident direct-ANE path",
            "included": ["linear projections", "MLP projections", "attention QK matmuls", "attention AV matmuls"],
            "excluded": ["softmax", "norms", "activations", "residual adds", "scheduler", "VAE", "text encoders", "layout/sync integration overhead"],
        },
        "baseline": {
            "gpu_only_render_seconds_reference": gpu_reference,
            "mps_dense_seconds": mps_dense_seconds,
            "mps_dense_share_of_render": mps_dense_seconds / gpu_reference,
            "dense_tflops": dense_tflops,
            "effective_mps_dense_tflops_per_second": effective_mps_tflops,
            "ane_advertised_tops": advertised_ane_tops,
            "ane_arithmetic_floor_seconds": dense_floor_seconds,
            "ane_floor_dense_speedup_vs_mps": mps_dense_seconds / dense_floor_seconds,
            "ideal_savings_seconds": ideal_savings,
            "ideal_savings_share_of_render": ideal_savings / gpu_reference,
            "best_case_render_seconds_if_only_dense_floor_changes": gpu_reference - ideal_savings,
        },
        "measured_block_stack": None,
        "measured_latent_pipeline": None,
        "measured_components": None,
        "measured_direct_ane_projection_evidence": None,
        "measured_direct_ane_optimized_projection_plan": None,
        "measured_direct_ane_attention_evidence": None,
        "schedule": {
            "steps": steps,
            "dual_blocks_per_step": dual_blocks,
            "single_blocks_per_step": single_blocks,
            "block_invocations": block_invocations,
            "dense_group_invocations": group_invocations,
            "chosen_tile_invocations": tile_invocations,
            "dual_chosen_tiles_per_block": total_tiles_per_dual,
            "single_chosen_tiles_per_block": total_tiles_per_single,
        },
        "boundary_buffers": {
            "dtype": "fp16_or_bf16",
            "image_hidden_shape": hidden_shape,
            "text_hidden_shape": text_shape,
            "image_hidden_mib": mib(hidden_bytes),
            "text_hidden_mib": mib(text_bytes),
            "read_plus_write_per_block_mib": mib(block_boundary_bytes),
            "read_plus_write_if_boundary_per_block_gib_per_step": block_boundary_bytes * blocks_per_step / (1024**3),
            "read_plus_write_if_boundary_per_block_gib_per_render": block_boundary_bytes * block_invocations / (1024**3),
        },
        "overhead_break_even": {
            "budget_seconds_before_ideal_dense_savings_are_lost": ideal_savings,
            "budget_ms_per_step": ideal_savings / steps * 1000,
            "budget_ms_per_block_boundary": ideal_savings / block_invocations * 1000,
            "budget_ms_per_dense_group_boundary": ideal_savings / group_invocations * 1000,
            "budget_microseconds_per_chosen_tile_if_host_dispatched": ideal_savings / tile_invocations * 1_000_000,
            "interpretation": "Per-tile host dispatch is ruled out; per-block or larger persistent programs are required.",
        },
        "runtime_requirements": [
            "packed weights remain resident across denoise steps",
            "one persistent or resumable program loops over tiles internally",
            "boundary tensors use shared memory/layouts that the GPU-side residual ops can consume without CPU copies",
            "program timing is measured independently from Core ML CPU_AND_NE",
            "fallback is explicit when a shape or block variant has no packed program",
        ],
    }
    if block_bench:
        scaled = block_bench["scaled"]
        block_stack_seconds = scaled["block_stack_seconds"]
        contract["measured_block_stack"] = {
            "source": str(block_benchmark_path),
            "dual_block_median_ms": block_bench["dual"]["median_seconds"] * 1000,
            "single_block_median_ms": block_bench["single"]["median_seconds"] * 1000,
            "block_stack_seconds": block_stack_seconds,
            "block_stack_seconds_per_step": scaled["block_stack_seconds_per_step"],
            "dense_share_of_measured_block_stack": dense["dense_seconds_28_steps_mps_estimate"] / block_stack_seconds,
            "non_dense_or_measurement_gap_seconds": block_stack_seconds - dense["dense_seconds_28_steps_mps_estimate"],
            "best_case_block_stack_seconds_if_only_dense_floor_changes": block_stack_seconds - ideal_savings,
        }
    if latent_bench:
        contract["measured_latent_pipeline"] = {
            "source": str(latent_pipeline_path),
            "slope_seconds_per_step": latent_bench["slope_seconds_per_step"],
            "intercept_seconds": latent_bench["intercept_seconds"],
            "step_results": latent_bench["results"],
            "projected_28_step_latent_seconds_from_slope": latent_bench["intercept_seconds"]
            + latent_bench["slope_seconds_per_step"] * steps,
            "dense_share_of_pipeline_step_slope": (
                dense["dense_seconds_28_steps_mps_estimate"] / steps / latent_bench["slope_seconds_per_step"]
            ),
            "block_stack_share_of_pipeline_step_slope": latent_bench.get("block_stack_share_of_pipeline_step_slope"),
        }
    if component_bench:
        component_sum = component_bench["summary"]["component_sum_scaled_seconds_28_steps"]
        contract["measured_components"] = {
            "source": str(component_benchmark_path),
            "component_sum_scaled_seconds_28_steps": component_sum,
            "component_sum_vs_block_stack": component_bench["summary"]["component_sum_vs_block_stack"],
            "components": [
                {
                    "name": item["name"],
                    "block_type": item["block_type"],
                    "median_ms": item["median_seconds"] * 1000,
                    "scaled_seconds_28_steps": item["scaled_seconds_28_steps"],
                    "share_of_component_sum": item["scaled_seconds_28_steps"] / component_sum,
                }
                for item in component_bench["components"]
            ],
        }
    if aneforge_projection_bench:
        contract["measured_direct_ane_projection_evidence"] = {
            "source": str(aneforge_projection_path),
            "runtime": aneforge_projection_bench["runtime"],
            "positive_saved_seconds_28_steps": aneforge_projection_bench["positive_saved_seconds_28_steps"],
            "net_saved_seconds_28_steps_if_all_tested_replaced": aneforge_projection_bench[
                "net_saved_seconds_28_steps_if_all_tested_replaced"
            ],
            "positive_cases": aneforge_projection_bench["positive_cases"],
            "negative_cases": aneforge_projection_bench["negative_cases"],
            "results": aneforge_projection_bench["results"],
        }
    if aneforge_optimized_projection_plan:
        saved_separate = aneforge_optimized_projection_plan["total_saved_seconds_28_vs_mps_separate"]
        saved_combined = aneforge_optimized_projection_plan["total_saved_seconds_28_vs_mps_combined"]
        contract["measured_direct_ane_optimized_projection_plan"] = {
            "source": str(aneforge_optimized_projection_path),
            "runtime": aneforge_optimized_projection_plan["runtime"],
            "total_saved_seconds_28_vs_mps_separate": saved_separate,
            "total_saved_seconds_28_vs_mps_combined": saved_combined,
            "share_of_gpu_render_reference_vs_mps_separate": saved_separate / gpu_reference,
            "share_of_latent_pipeline_28_step_projection_vs_mps_separate": (
                saved_separate
                / (
                    contract["measured_latent_pipeline"]["projected_28_step_latent_seconds_from_slope"]
                    if contract["measured_latent_pipeline"]
                    else gpu_reference
                )
            ),
            "share_of_measured_block_stack_vs_mps_separate": (
                saved_separate / contract["measured_block_stack"]["block_stack_seconds"]
                if contract["measured_block_stack"]
                else None
            ),
            "share_of_modeled_ideal_dense_savings_vs_mps_separate": saved_separate / ideal_savings,
            "all_cases_positive_vs_mps_separate": aneforge_optimized_projection_plan[
                "all_cases_positive_vs_mps_separate"
            ],
            "results": aneforge_optimized_projection_plan["results"],
        }
    if aneforge_attention_bench:
        contract["measured_direct_ane_attention_evidence"] = {
            "source": str(aneforge_attention_path),
            "runtime": aneforge_attention_bench["runtime"],
            "full_flux_attention_saved_seconds_28_steps": aneforge_attention_bench[
                "full_flux_attention_saved_seconds_28_steps"
            ],
            "results": aneforge_attention_bench["results"],
        }

    out = pathlib.Path(args.out).expanduser() if args.out else out_dir / "direct_runtime_contract_1024x1024.json"
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(contract, indent=2, sort_keys=True) + "\n")
    tmp.replace(out)
    print(
        json.dumps(
            {
                "ok": True,
                "runtime_contract": str(out),
                "mps_dense_seconds": mps_dense_seconds,
                "ane_floor_seconds": dense_floor_seconds,
                "ideal_savings_seconds": ideal_savings,
                "budget_ms_per_block_boundary": contract["overhead_break_even"]["budget_ms_per_block_boundary"],
                "budget_microseconds_per_tile_if_host_dispatched": contract["overhead_break_even"][
                    "budget_microseconds_per_chosen_tile_if_host_dispatched"
                ],
                "block_stack_seconds": (contract["measured_block_stack"] or {}).get("block_stack_seconds"),
                "dense_share_of_measured_block_stack": (contract["measured_block_stack"] or {}).get(
                    "dense_share_of_measured_block_stack"
                ),
                "latent_pipeline_slope_seconds_per_step": (contract["measured_latent_pipeline"] or {}).get(
                    "slope_seconds_per_step"
                ),
                "component_sum_scaled_seconds_28_steps": (contract["measured_components"] or {}).get(
                    "component_sum_scaled_seconds_28_steps"
                ),
                "direct_ane_positive_saved_seconds_28_steps": (
                    contract["measured_direct_ane_projection_evidence"] or {}
                ).get("positive_saved_seconds_28_steps"),
                "direct_ane_optimized_saved_seconds_28_steps": (
                    contract["measured_direct_ane_optimized_projection_plan"] or {}
                ).get("total_saved_seconds_28_vs_mps_separate"),
                "direct_ane_full_attention_saved_seconds_28_steps": (
                    contract["measured_direct_ane_attention_evidence"] or {}
                ).get("full_flux_attention_saved_seconds_28_steps"),
            },
            sort_keys=True,
        )
    )


def runtime_report(args):
    contract = read_json(args.contract)
    baseline = contract["baseline"]
    boundary = contract["boundary_buffers"]
    budget = contract["overhead_break_even"]
    bench = contract["mps_benchmark_definition"]

    def seconds(value):
        return f"{value:.1f}s"

    def pct(value):
        return f"{value * 100:.1f}%"

    def ms(value):
        return f"{value:.2f}ms"

    print("Direct ANE Dense Offload Report")
    print(f"shape: {contract['shape']}")
    print()
    print("MPS benchmark:")
    print(f"- {bench['what_it_is']}")
    print(f"- {bench['what_it_is_not']}")
    print(f"- purpose: {bench['purpose']}")
    print()
    print("Opportunity:")
    print(f"- GPU-only render reference: {seconds(baseline['gpu_only_render_seconds_reference'])}")
    print(f"- MPS dense slice: {seconds(baseline['mps_dense_seconds'])} ({pct(baseline['mps_dense_share_of_render'])} of render)")
    print(f"- dense compute: {baseline['dense_tflops']:.0f} TFLOP over 28 steps")
    print(f"- effective MPS dense throughput: {baseline['effective_mps_dense_tflops_per_second']:.2f} TFLOP/s")
    print(f"- ANE arithmetic floor at 38 TOPS: {seconds(baseline['ane_arithmetic_floor_seconds'])}")
    print(f"- ideal dense speedup vs MPS: {baseline['ane_floor_dense_speedup_vs_mps']:.2f}x")
    print(f"- ideal render saving: {seconds(baseline['ideal_savings_seconds'])} ({pct(baseline['ideal_savings_share_of_render'])})")
    print(f"- best-case render if only dense changes: {seconds(baseline['best_case_render_seconds_if_only_dense_floor_changes'])}")
    if contract.get("measured_latent_pipeline"):
        latent = contract["measured_latent_pipeline"]
        print()
        print("Measured latent pipeline:")
        print(f"- denoise slope without VAE: {latent['slope_seconds_per_step']:.2f}s/step")
        print(f"- fixed overhead/intercept: {seconds(latent['intercept_seconds'])}")
        print(f"- projected 28-step latent run: {seconds(latent['projected_28_step_latent_seconds_from_slope'])}")
        print(f"- dense share of pipeline step slope: {pct(latent['dense_share_of_pipeline_step_slope'])}")
        if latent.get("block_stack_share_of_pipeline_step_slope") is not None:
            print(f"- block stack share of pipeline step slope: {pct(latent['block_stack_share_of_pipeline_step_slope'])}")
    if contract.get("measured_block_stack"):
        block = contract["measured_block_stack"]
        print()
        print("Measured block stack:")
        print(f"- dual block median: {block['dual_block_median_ms']:.2f}ms")
        print(f"- single block median: {block['single_block_median_ms']:.2f}ms")
        print(f"- block stack over 28 steps: {seconds(block['block_stack_seconds'])}")
        print(f"- dense share of measured block stack: {pct(block['dense_share_of_measured_block_stack'])}")
        print(f"- non-dense or measurement gap: {seconds(block['non_dense_or_measurement_gap_seconds'])}")
        print(
            f"- best-case block stack if only dense changes: "
            f"{seconds(block['best_case_block_stack_seconds_if_only_dense_floor_changes'])}"
        )
    if contract.get("measured_components"):
        components = contract["measured_components"]
        print()
        print("Measured block components:")
        print(f"- component sum over 28 steps: {seconds(components['component_sum_scaled_seconds_28_steps'])}")
        print(f"- component sum vs block stack: {pct(components['component_sum_vs_block_stack'])}")
        for item in sorted(components["components"], key=lambda x: x["scaled_seconds_28_steps"], reverse=True):
            print(
                f"- {item['name']}: {item['median_ms']:.2f}ms/block, "
                f"{seconds(item['scaled_seconds_28_steps'])} scaled, {pct(item['share_of_component_sum'])}"
            )
    if contract.get("measured_direct_ane_projection_evidence"):
        evidence = contract["measured_direct_ane_projection_evidence"]
        print()
        print("Measured Direct ANE Projection Evidence:")
        print(f"- runtime: {evidence['runtime']}")
        print(f"- selective positive saving: {seconds(evidence['positive_saved_seconds_28_steps'])}")
        print(
            f"- net result if all tested projections are offloaded: "
            f"{seconds(evidence['net_saved_seconds_28_steps_if_all_tested_replaced'])}"
        )
        for item in evidence["results"]:
            if "speedup_ane_vs_mps" not in item:
                continue
            print(
                f"- {item['name']}: MPS {item['mps_median_ms']:.2f}ms, "
                f"ANE {item['ane_conv1x1_median_ms']:.2f}ms, "
                f"{item['speedup_ane_vs_mps']:.2f}x, "
                f"{seconds(item['saved_seconds_28_steps'])} over 28 steps"
            )
    if contract.get("measured_direct_ane_optimized_projection_plan"):
        plan = contract["measured_direct_ane_optimized_projection_plan"]
        print()
        print("Measured Direct ANE Optimized Projection Plan:")
        print(f"- runtime: {plan['runtime']}")
        print(
            f"- total saving vs current MPS separate projection calls: "
            f"{seconds(plan['total_saved_seconds_28_vs_mps_separate'])} "
            f"({pct(plan['share_of_gpu_render_reference_vs_mps_separate'])} of 180s render reference)"
        )
        print(
            f"- total saving vs fused MPS lower-bound projection calls: "
            f"{seconds(plan['total_saved_seconds_28_vs_mps_combined'])}"
        )
        if plan.get("share_of_measured_block_stack_vs_mps_separate") is not None:
            print(f"- share of measured block stack removed: {pct(plan['share_of_measured_block_stack_vs_mps_separate'])}")
        print(f"- share of modeled ideal dense saving reached: {pct(plan['share_of_modeled_ideal_dense_savings_vs_mps_separate'])}")
        print(f"- all tested optimized cases positive: {plan['all_cases_positive_vs_mps_separate']}")
        for item in plan["results"]:
            print(
                f"- {item['name']}: MPS separate {item['mps_separate_ms']:.2f}ms, "
                f"MPS fused {item['mps_combined_ms']:.2f}ms, "
                f"ANE {item['ane_ms']:.2f}ms, "
                f"{item['speedup_vs_mps_separate']:.2f}x vs separate, "
                f"{seconds(item['saved_seconds_28_vs_mps_separate'])} over 28 steps"
            )
    if contract.get("measured_direct_ane_attention_evidence"):
        evidence = contract["measured_direct_ane_attention_evidence"]
        print()
        print("Measured Direct ANE Attention Evidence:")
        print(f"- runtime: {evidence['runtime']}")
        print(f"- full FLUX attention-core impact: {seconds(evidence['full_flux_attention_saved_seconds_28_steps'])}")
        for item in evidence["results"]:
            if "speedup_ane_vs_mps" not in item:
                print(f"- {item['name']}: {item.get('error', 'no result')}")
                continue
            impact = item["saved_seconds_28_steps"]
            impact_text = "diagnostic tile" if item["invocations_per_step"] == 0 else f"{seconds(impact)} over 28 steps"
            print(
                f"- {item['name']}: MPS {item['mps_median_ms']:.2f}ms, "
                f"ANE {item['ane_tiled_median_ms']:.2f}ms, "
                f"{item['speedup_ane_vs_mps']:.2f}x, {impact_text}"
            )
    print()
    print("Boundary pressure:")
    print(f"- image hidden tensor: {boundary['image_hidden_mib']:.1f} MiB")
    print(f"- text hidden tensor: {boundary['text_hidden_mib']:.1f} MiB")
    print(f"- read+write per block boundary: {boundary['read_plus_write_per_block_mib']:.1f} MiB")
    print(
        f"- naive per-block boundary traffic: "
        f"{boundary['read_plus_write_if_boundary_per_block_gib_per_render']:.1f} GiB/render"
    )
    print()
    print("Break-even overhead budget before the ideal saving is erased:")
    print(f"- per denoise step: {ms(budget['budget_ms_per_step'])}")
    print(f"- per block boundary: {ms(budget['budget_ms_per_block_boundary'])}")
    print(f"- per dense group boundary: {ms(budget['budget_ms_per_dense_group_boundary'])}")
    print(f"- per chosen tile if host-dispatched: {budget['budget_microseconds_per_chosen_tile_if_host_dispatched']:.2f}us")
    print()
    print("Conclusion:")
    print(
        "The offload target is substantial, but only a resident packed program has a credible path. "
        "Per-tile host dispatch and CPU tensor bounce are outside the break-even budget."
    )


def main():
    parser = argparse.ArgumentParser(description="Direct ANE FLUX denoiser target probes")
    sub = parser.add_subparsers(dest="cmd", required=True)

    cap = sub.add_parser("capture-block", help="capture one FLUX transformer block shape/weight manifest")
    cap.add_argument("--model-dir", default=os.environ.get("MODEL_DIR", DEFAULT_MODEL_DIR))
    cap.add_argument("--out-dir", default=os.environ.get("FLUX_DIRECT_ANE_DIR", DEFAULT_OUT_DIR))
    cap.add_argument("--prompt", default="a clean product photo of a translucent glass cube on a matte table")
    cap.add_argument("--width", type=int, default=1024)
    cap.add_argument("--height", type=int, default=1024)
    cap.add_argument("--steps", type=int, default=1)
    cap.add_argument("--guidance", type=float, default=3.5)
    cap.add_argument("--seed", type=int, default=12345)
    cap.add_argument("--block-type", choices=["dual", "single"], default="dual")
    cap.add_argument("--block-index", type=int, default=0)
    cap.add_argument("--name", default="")
    cap.add_argument("--stop-after-capture", action=argparse.BooleanOptionalAction, default=True)

    pack = sub.add_parser("pack-plan", help="create a direct-ANE block weight packing plan")
    pack.add_argument("--manifest", required=True)
    pack.add_argument("--out", default="")
    pack.add_argument("--tile-m", type=int, default=128)
    pack.add_argument("--tile-n", type=int, default=128)

    proj = sub.add_parser("projection-plan", help="create direct-ANE dense projection group plan")
    proj.add_argument("--manifest", required=True)
    proj.add_argument("--pack-plan", required=True)
    proj.add_argument("--out", default="")

    attn = sub.add_parser("attention-plan", help="create direct-ANE attention QK/AV matmul plan")
    attn.add_argument("--manifest", required=True)
    attn.add_argument("--out", default="")

    contract = sub.add_parser("runtime-contract", help="create direct-ANE dense runtime contract and break-even budget")
    contract.add_argument("--out-dir", default=os.environ.get("FLUX_DIRECT_ANE_DIR", DEFAULT_OUT_DIR))
    contract.add_argument("--dense-summary", default=DEFAULT_DENSE_SUMMARY)
    contract.add_argument("--block-benchmark", default=DEFAULT_BLOCK_BENCHMARK)
    contract.add_argument("--latent-pipeline-benchmark", default=DEFAULT_LATENT_PIPELINE_BENCHMARK)
    contract.add_argument("--component-benchmark", default=DEFAULT_COMPONENT_BENCHMARK)
    contract.add_argument("--aneforge-projection-benchmark", default=DEFAULT_ANEFORGE_PROJECTION_BENCHMARK)
    contract.add_argument("--aneforge-optimized-projection-plan", default=DEFAULT_ANEFORGE_OPTIMIZED_PROJECTION_PLAN)
    contract.add_argument("--aneforge-attention-benchmark", default=DEFAULT_ANEFORGE_ATTENTION_BENCHMARK)
    contract.add_argument("--out", default="")
    contract.add_argument("--steps", type=int, default=28)
    contract.add_argument("--dual-blocks", type=int, default=19)
    contract.add_argument("--single-blocks", type=int, default=38)

    report = sub.add_parser("runtime-report", help="print direct-ANE dense runtime report")
    report.add_argument("--contract", default=os.path.join(DEFAULT_OUT_DIR, "direct_runtime_contract_1024x1024.json"))

    bench = sub.add_parser("dense-benchmark", help="measure synthetic MPS dense matmuls from captured plans")
    bench.add_argument("--out-dir", default=os.environ.get("FLUX_DIRECT_ANE_DIR", DEFAULT_OUT_DIR))
    bench.add_argument("--out", default="")
    bench.add_argument("--dtype", default="bf16")
    bench.add_argument("--warmup", type=int, default=2)
    bench.add_argument("--iterations", type=int, default=5)
    bench.add_argument("--steps", type=int, default=28)
    bench.add_argument("--dual-blocks", type=int, default=19)
    bench.add_argument("--single-blocks", type=int, default=38)
    bench.add_argument("--ane-tops", type=float, default=38.0)
    bench.add_argument("--gpu-render-seconds", type=float, default=180.0)

    block_bench = sub.add_parser("block-benchmark", help="measure real MPS Flux transformer block forwards")
    block_bench.add_argument("--model-dir", default=os.environ.get("MODEL_DIR", DEFAULT_MODEL_DIR))
    block_bench.add_argument("--out-dir", default=os.environ.get("FLUX_DIRECT_ANE_DIR", DEFAULT_OUT_DIR))
    block_bench.add_argument("--dense-summary", default=DEFAULT_DENSE_SUMMARY)
    block_bench.add_argument("--out", default="")
    block_bench.add_argument("--dtype", default="bf16")
    block_bench.add_argument("--warmup", type=int, default=1)
    block_bench.add_argument("--iterations", type=int, default=3)
    block_bench.add_argument("--steps", type=int, default=28)
    block_bench.add_argument("--dual-blocks", type=int, default=19)
    block_bench.add_argument("--single-blocks", type=int, default=38)
    block_bench.add_argument("--dual-index", type=int, default=0)
    block_bench.add_argument("--single-index", type=int, default=0)

    pipe_bench = sub.add_parser("latent-pipeline-benchmark", help="measure real MPS FluxPipeline latent-output step slope")
    pipe_bench.add_argument("--model-dir", default=os.environ.get("MODEL_DIR", DEFAULT_MODEL_DIR))
    pipe_bench.add_argument("--out-dir", default=os.environ.get("FLUX_DIRECT_ANE_DIR", DEFAULT_OUT_DIR))
    pipe_bench.add_argument("--block-benchmark", default=DEFAULT_BLOCK_BENCHMARK)
    pipe_bench.add_argument("--out", default="")
    pipe_bench.add_argument("--prompt", default="a clean product photo of a translucent glass cube on a matte table")
    pipe_bench.add_argument("--width", type=int, default=1024)
    pipe_bench.add_argument("--height", type=int, default=1024)
    pipe_bench.add_argument("--guidance", type=float, default=3.5)
    pipe_bench.add_argument("--seed", type=int, default=12345)
    pipe_bench.add_argument("--dtype", default="bf16")
    pipe_bench.add_argument("--steps-list", default="1,2,4")
    pipe_bench.add_argument("--iterations", type=int, default=1)

    comp_bench = sub.add_parser("component-benchmark", help="measure real MPS FLUX block submodule components")
    comp_bench.add_argument("--model-dir", default=os.environ.get("MODEL_DIR", DEFAULT_MODEL_DIR))
    comp_bench.add_argument("--out-dir", default=os.environ.get("FLUX_DIRECT_ANE_DIR", DEFAULT_OUT_DIR))
    comp_bench.add_argument("--dense-summary", default=DEFAULT_DENSE_SUMMARY)
    comp_bench.add_argument("--block-benchmark", default=DEFAULT_BLOCK_BENCHMARK)
    comp_bench.add_argument("--out", default="")
    comp_bench.add_argument("--dtype", default="bf16")
    comp_bench.add_argument("--warmup", type=int, default=1)
    comp_bench.add_argument("--iterations", type=int, default=3)
    comp_bench.add_argument("--steps", type=int, default=28)
    comp_bench.add_argument("--dual-blocks", type=int, default=19)
    comp_bench.add_argument("--single-blocks", type=int, default=38)
    comp_bench.add_argument("--dual-index", type=int, default=0)
    comp_bench.add_argument("--single-index", type=int, default=0)

    aneforge_bench = sub.add_parser("aneforge-projection-benchmark", help="measure direct-ANE ANEForge FLUX projection kernels")
    aneforge_bench.add_argument("--out-dir", default=os.environ.get("FLUX_DIRECT_ANE_DIR", DEFAULT_OUT_DIR))
    aneforge_bench.add_argument("--out", default="")
    aneforge_bench.add_argument("--seed", type=int, default=6000)
    aneforge_bench.add_argument("--steps", type=int, default=28)
    aneforge_bench.add_argument("--warmup", type=int, default=2)
    aneforge_bench.add_argument("--mps-iterations", type=int, default=5)
    aneforge_bench.add_argument("--ane-iterations", type=int, default=10)
    aneforge_bench.add_argument("--compress", default="int8")

    optimized_aneforge = sub.add_parser(
        "aneforge-optimized-projection-plan",
        help="measure optimized direct-ANE ANEForge projection plan",
    )
    optimized_aneforge.add_argument("--out-dir", default=os.environ.get("FLUX_DIRECT_ANE_DIR", DEFAULT_OUT_DIR))
    optimized_aneforge.add_argument("--out", default="")
    optimized_aneforge.add_argument("--seed", type=int, default=7000)
    optimized_aneforge.add_argument("--steps", type=int, default=28)
    optimized_aneforge.add_argument("--warmup", type=int, default=2)
    optimized_aneforge.add_argument("--mps-iterations", type=int, default=5)
    optimized_aneforge.add_argument("--ane-iterations", type=int, default=10)
    optimized_aneforge.add_argument("--compress", default="int8")

    aneforge_attention = sub.add_parser(
        "aneforge-attention-benchmark",
        help="measure direct-ANE ANEForge tiled SDPA attention core",
    )
    aneforge_attention.add_argument("--out-dir", default=os.environ.get("FLUX_DIRECT_ANE_DIR", DEFAULT_OUT_DIR))
    aneforge_attention.add_argument("--out", default="")
    aneforge_attention.add_argument("--seed", type=int, default=8000)
    aneforge_attention.add_argument("--steps", type=int, default=28)
    aneforge_attention.add_argument("--warmup", type=int, default=1)
    aneforge_attention.add_argument("--mps-iterations", type=int, default=3)
    aneforge_attention.add_argument("--ane-iterations", type=int, default=5)
    aneforge_attention.add_argument("--compress", default="int8")

    args = parser.parse_args()
    if args.cmd == "capture-block":
        capture(args)
    elif args.cmd == "pack-plan":
        pack_plan(args)
    elif args.cmd == "projection-plan":
        projection_plan(args)
    elif args.cmd == "attention-plan":
        attention_plan(args)
    elif args.cmd == "runtime-contract":
        runtime_contract(args)
    elif args.cmd == "runtime-report":
        runtime_report(args)
    elif args.cmd == "dense-benchmark":
        dense_benchmark(args)
    elif args.cmd == "block-benchmark":
        block_benchmark(args)
    elif args.cmd == "latent-pipeline-benchmark":
        latent_pipeline_benchmark(args)
    elif args.cmd == "component-benchmark":
        component_benchmark(args)
    elif args.cmd == "aneforge-projection-benchmark":
        aneforge_projection_benchmark(args)
    elif args.cmd == "aneforge-optimized-projection-plan":
        aneforge_optimized_projection_plan(args)
    elif args.cmd == "aneforge-attention-benchmark":
        aneforge_attention_benchmark(args)


if __name__ == "__main__":
    main()
