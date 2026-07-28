import argparse
import json
import math
import pathlib
import time

import flux_paths


DEFAULT_PROJECTION_PLAN = str(
    pathlib.Path(flux_paths.default_direct_ane_dir()) / "dual_block_0_1024x1024.projectionplan.json"
)


PROFILES = {
    "m4max_h16g_estimate": {
        "source": "reverse-engineered H16/H16G generation notes plus local M4 Max hardware profile",
        "status": "estimated; not Apple public ISA documentation",
        "ne_marketed_cores": 16,
        "tops_advertised": 38e12,
        "dtype": "fp16",
        "bytes_per_element": 2,
        "working_set_bytes": 2 * 1024 * 1024,
        "bank_count": 64,
        "bank_granule_bytes": 16,
        "k_alignment": 4,
        "channel_alignment": 64,
        "preferred_tile_multiples": [64, 128, 256, 512, 1024],
        "preferred_tile_n_multiples": [64, 128, 256, 512, 1024],
        "preferred_tile_k_multiples": [64, 128, 256, 512, 1024],
    }
}


def round_down(value, multiple):
    return max(multiple, (value // multiple) * multiple)


def ceil_div(a, b):
    return (a + b - 1) // b


def matmul_flops(m, n, k):
    return 2 * m * n * k


def tile_bytes(m, n, k, bpe):
    return {
        "a_bytes": m * k * bpe,
        "b_bytes": n * k * bpe,
        "c_bytes": m * n * bpe,
        "sum_bytes": (m * k + n * k + m * n) * bpe,
        "max_operand_bytes": max(m * k * bpe, n * k * bpe, m * n * bpe),
    }


def candidate_tiles(m, n, k, profile):
    bpe = profile["bytes_per_element"]
    cap = profile["working_set_bytes"]
    best = []
    for mt in profile["preferred_tile_multiples"]:
        if mt > m:
            continue
        for nt in profile["preferred_tile_n_multiples"]:
            if nt > n:
                continue
            if nt % profile["channel_alignment"] != 0:
                continue
            for kt in profile["preferred_tile_k_multiples"]:
                if kt > k:
                    continue
                if kt % profile["k_alignment"] != 0:
                    continue
                sizes = tile_bytes(mt, nt, kt, bpe)
                if sizes["max_operand_bytes"] > cap:
                    continue
                tiles = ceil_div(m, mt) * ceil_div(n, nt) * ceil_div(k, kt)
                reuse_score = matmul_flops(mt, nt, kt) / max(1, sizes["sum_bytes"])
                dispatch_floor_work = matmul_flops(mt, nt, kt) / profile["tops_advertised"]
                best.append(
                    {
                        "tile": [mt, nt, kt],
                        "tile_count": tiles,
                        "tile_flops": matmul_flops(mt, nt, kt),
                        "tile_gflops": matmul_flops(mt, nt, kt) / 1e9,
                        "tile_bytes": sizes,
                        "arithmetic_intensity_flop_per_byte": reuse_score,
                        "ideal_tile_compute_seconds_at_advertised_tops": dispatch_floor_work,
                    }
                )
    best.sort(key=lambda x: (x["tile_count"], -x["arithmetic_intensity_flop_per_byte"]))
    return best


def fit_group(group, profile):
    legacy_output_shapes = group.get("output_shapes", [])
    matmul_specs = group.get("matmuls")
    if not matmul_specs:
        m = int(group["input_shape"][1])
        k = int(group["input_shape"][2])
        matmul_specs = [{"m": m, "n": int(out_shape[-1]), "k": k} for out_shape in legacy_output_shapes]
    fits = []
    for spec in matmul_specs:
        m = int(spec["m"])
        n = int(spec["n"])
        k = int(spec["k"])
        candidates = candidate_tiles(m, n, k, profile)
        chosen = candidates[0] if candidates else None
        fits.append(
            {
                "weight": spec.get("weight", ""),
                "m": m,
                "n": n,
                "k": k,
                "full_flops": matmul_flops(m, n, k),
                "full_gflops": matmul_flops(m, n, k) / 1e9,
                "full_bytes": tile_bytes(m, n, k, profile["bytes_per_element"]),
                "fits_without_tiling": tile_bytes(m, n, k, profile["bytes_per_element"])["max_operand_bytes"]
                <= profile["working_set_bytes"],
                "chosen_tile": chosen,
                "top_candidates": candidates[:8],
            }
        )
    return {
        "name": group["name"],
        "kind": group["kind"],
        "weights_mb": group["source_mb"],
        "group_gflops": group["counted_gflops"],
        "matmuls": fits,
    }


def fit_attention_group(group, profile):
    heads = int(group["heads"])
    q_tokens = int(group["query_tokens"])
    kv_tokens = int(group["key_value_tokens"])
    head_dim = int(group["head_dim"])
    if group["semantic_n"] == "key_tokens":
        m, n, k = q_tokens, kv_tokens, head_dim
    else:
        m, n, k = q_tokens, head_dim, kv_tokens
    candidates = candidate_tiles(m, n, k, profile)
    chosen = candidates[0] if candidates else None
    return {
        "name": group["name"],
        "kind": group["kind"],
        "heads": heads,
        "m": m,
        "n": n,
        "k": k,
        "full_flops": group["counted_flops"],
        "full_gflops": group["counted_gflops"],
        "per_head_flops": matmul_flops(m, n, k),
        "full_bytes_per_head": tile_bytes(m, n, k, profile["bytes_per_element"]),
        "fits_without_tiling": tile_bytes(m, n, k, profile["bytes_per_element"])["max_operand_bytes"]
        <= profile["working_set_bytes"],
        "chosen_tile": chosen,
        "total_tiles_all_heads": (chosen or {}).get("tile_count", 0) * heads,
        "top_candidates": candidates[:8],
    }


def main():
    parser = argparse.ArgumentParser(description="Fit FLUX projection groups into candidate ANE tiles")
    parser.add_argument("--projection-plan", default=DEFAULT_PROJECTION_PLAN)
    parser.add_argument("--profile", choices=sorted(PROFILES), default="m4max_h16g_estimate")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    plan_path = pathlib.Path(args.projection_plan).expanduser()
    plan = json.loads(plan_path.read_text())
    profile = PROFILES[args.profile]
    is_attention = "attention" in plan_path.name or plan.get("runtime_contract", {}).get("goal") == "direct_ane_attention_qk_av"
    fits = [fit_attention_group(group, profile) if is_attention else fit_group(group, profile) for group in plan["groups"]]
    result = {
        "version": 1,
        "created": time.time(),
        "source_projection_plan": str(plan_path),
        "profile_name": args.profile,
        "profile": profile,
        "fits": fits,
        "summary": {
            "groups": len(fits),
            "matmuls": len(fits) if is_attention else sum(len(group["matmuls"]) for group in fits),
            "all_fit_without_tiling": all(
                group["fits_without_tiling"] for group in fits
            )
            if is_attention
            else all(matmul["fits_without_tiling"] for group in fits for matmul in group["matmuls"]),
            "total_chosen_tiles": sum(group["total_tiles_all_heads"] for group in fits)
            if is_attention
            else sum((matmul["chosen_tile"] or {}).get("tile_count", 0) for group in fits for matmul in group["matmuls"]),
            "total_gflops": sum(group["counted_gflops"] for group in plan["groups"]),
        },
    }
    total_flops = result["summary"]["total_gflops"] * 1e9
    total_tiles = result["summary"]["total_chosen_tiles"]
    dispatch_floor_seconds = 0.00023
    result["latency_model"] = {
        "advertised_tops": profile["tops_advertised"],
        "arithmetic_floor_seconds": total_flops / profile["tops_advertised"],
        "arithmetic_floor_ms": total_flops / profile["tops_advertised"] * 1000,
        "naive_host_dispatches": total_tiles,
        "naive_dispatch_floor_seconds": total_tiles * dispatch_floor_seconds,
        "naive_dispatch_floor_ms": total_tiles * dispatch_floor_seconds * 1000,
        "persistent_program_dispatches": 1,
        "persistent_program_note": "Target path: one resident/fused program with an internal tile loop; host dispatch is not paid per tile.",
    }
    out = pathlib.Path(args.out).expanduser() if args.out else plan_path.with_name(plan_path.stem + ".anefit.json")
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    tmp.replace(out)
    print(json.dumps({"ok": True, "ane_fit": str(out), "summary": result["summary"]}, sort_keys=True))


if __name__ == "__main__":
    main()
