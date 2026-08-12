#!/usr/bin/env python3
"""Directional FLUX tournament: four children, one stateful decision.

The prompt stays fixed. Each generation renders four literal tangent-space
directions around the retained parent. A multimodal council scores the parent
and children from four named perspectives; a deterministic Director advances
only when one child beats the parent. Every coordinate and decision is durable.
"""
import argparse
import base64
import gc
import json
import math
import os
import pathlib
import random
import signal
import socket
import time
import urllib.error
import urllib.request

import torch
from diffusers import FluxPipeline
from PIL import Image, ImageDraw

import governor

GOVERNOR = "https://governor.influx.vision/v1/chat/completions"
DIRECTIONS = ("north", "south", "east", "west")
PERSPECTIVES = ("continuity", "composition", "material_light", "meaningful_change")


def atomic_json(path, value):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def append_json(path, value):
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, sort_keys=True) + "\n")


def normalize(vector):
    norm = math.sqrt(sum(float(x) ** 2 for x in vector))
    if norm <= 1e-12:
        raise ValueError("zero direction")
    return [float(x) / norm for x in vector]


def project_tangent(vector, parent):
    dot = sum(float(a) * float(b) for a, b in zip(vector, parent))
    return normalize([float(a) - dot * float(b) for a, b in zip(vector, parent)])


def orthogonal_axis(parent, heading, generation):
    # A deterministic rotating sequence prevents one chosen axis from becoming
    # the only axis the lineage ever sees.
    candidates = (
        [1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1],
        [1, 1, -1, 0], [0, 1, 1, -1],
    )
    for offset in range(len(candidates)):
        raw = candidates[(generation + offset) % len(candidates)]
        dot_parent = sum(a * b for a, b in zip(raw, parent))
        dot_heading = sum(a * b for a, b in zip(raw, heading))
        vector = [a - dot_parent * b - dot_heading * h for a, b, h in zip(raw, parent, heading)]
        if sum(x * x for x in vector) > 1e-8:
            return normalize(vector)
    raise ValueError("could not construct tangent axis")


def candidates(parent, heading, generation, angle):
    heading = project_tangent(heading, parent)
    cross = orthogonal_axis(parent, heading, generation)
    axes = (heading, [-x for x in heading], cross, [-x for x in cross])
    children = []
    for axis in axes:
        child = normalize([
            math.cos(angle) * p + math.sin(angle) * d
            for p, d in zip(parent, axis)
        ])
        children.append(child)
    return children, axes


def compose_latent(coefficients, basis, radius, shape, dtype):
    flat = sum(float(c) * b for c, b in zip(coefficients, basis)) * radius
    return flat.reshape(shape).to(dtype)


def make_sheet(parent, children, path):
    thumb = 384
    sheet = Image.new("RGB", (thumb * 3, thumb * 2), "#eeeae3")
    draw = ImageDraw.Draw(sheet)
    entries = [("0 PARENT", parent)] + [
        (f"{i + 1} {DIRECTIONS[i].upper()}", image) for i, image in enumerate(children)
    ]
    for i, (label, image) in enumerate(entries):
        x, y = (i % 3) * thumb, (i // 3) * thumb
        tile = image.copy().convert("RGB")
        tile.thumbnail((thumb, thumb))
        sheet.paste(tile, (x, y))
        draw.rectangle((x, y, x + 126, y + 22), fill=(247, 245, 240))
        draw.text((x + 6, y + 5), label, fill=(38, 34, 29))
    sheet.save(path, quality=82, optimize=True)
    return sheet


def image_reference(sheet):
    reduced = sheet.copy()
    reduced.thumbnail((900, 600))
    import io
    buf = io.BytesIO()
    reduced.save(buf, format="JPEG", quality=68, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def parse_json_object(text):
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("council returned no JSON object")
    return json.loads(text[start:end + 1])


def consult(sheet, north_star, timeout):
    prompt = f"""You are a four-seat visual council supplying evidence to a separate Director.
The asymptotic north star is: {north_star}

Image 0 is the retained parent. Images 1-4 are four literal latent directions
north, south, east, west. Judge all five independently from each perspective:
continuity, composition, material_light, meaningful_change. Scores are 0-100.
Meaningful change must reward visible development and penalize near-duplicates.
Continuity must preserve the work's established identity. Do not cut a coherent
image merely because it is not novel. Mark catastrophic only for broken images.

Reply ONLY as JSON:
{{"perspectives":{{
 "continuity":{{"scores":[0,0,0,0,0],"reason":"<12 words>"}},
 "composition":{{"scores":[0,0,0,0,0],"reason":"<12 words>"}},
 "material_light":{{"scores":[0,0,0,0,0],"reason":"<12 words>"}},
 "meaningful_change":{{"scores":[0,0,0,0,0],"reason":"<12 words>"}}}},
 "north_star_progress":[0,0,0,0,0],"catastrophic":[]}}"""
    body = {
        "model": "governor", "max_tokens": 700,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": image_reference(sheet)}},
        ]}],
    }
    engines = [e for e in (os.environ.get("CHORUS_SECOND_ENGINE", ""), GOVERNOR) if e]
    errors = []
    for engine in engines:
        request = urllib.request.Request(
            engine, json.dumps(body).encode(), governor.headers("flux-directional/1"), method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                content = json.load(response)["choices"][0]["message"]["content"]
            return parse_json_object(content), engine
        except (urllib.error.URLError, OSError, ValueError, KeyError, IndexError, json.JSONDecodeError) as exc:
            errors.append(f"{engine}: {exc}")
    raise RuntimeError("; ".join(errors) or "no council engine configured")


def five_scores(value, name):
    if not isinstance(value, list) or len(value) != 5:
        raise ValueError(f"{name} must contain five scores")
    return [max(0.0, min(100.0, float(x))) for x in value]


def direct(verdict, minimum_gain):
    perspectives = verdict.get("perspectives") or {}
    totals = [0.0] * 5
    # The Director is an explicit algorithm, not a fifth aesthetic opinion.
    weights = {"continuity": 0.28, "composition": 0.20,
               "material_light": 0.20, "meaningful_change": 0.32}
    evidence = {}
    for seat, weight in weights.items():
        scores = five_scores((perspectives.get(seat) or {}).get("scores"), seat)
        evidence[seat] = scores
        totals = [total + weight * score for total, score in zip(totals, scores)]
    progress = five_scores(verdict.get("north_star_progress"), "north_star_progress")
    totals = [0.78 * total + 0.22 * score for total, score in zip(totals, progress)]
    catastrophic = {int(x) for x in (verdict.get("catastrophic") or []) if str(x).isdigit()}
    eligible = [(totals[i], i) for i in range(1, 5) if i not in catastrophic]
    best_score, best = max(eligible, default=(float("-inf"), 0))
    advance = best > 0 and best_score >= totals[0] + minimum_gain
    return {
        "action": "advance" if advance else "hold",
        "selected": best if advance else 0,
        "direction": DIRECTIONS[best - 1] if advance else "hold",
        "scores": [round(x, 3) for x in totals],
        "parent_score": round(totals[0], 3),
        "best_child_score": round(best_score, 3),
        "minimum_gain": minimum_gain,
        "evidence": evidence,
    }


def publish(job_id, path, index, total, out_dir):
    socket_path = os.environ.get("PIPER_SOCKET", "/tmp/piper.sock")
    relative = path.resolve().relative_to(out_dir.resolve()).as_posix()
    payload = {"type": "asset.publish", "job_id": job_id, "asset": {
        "id": f"{job_id}:{index}", "name": path.name, "path": str(path),
        "media_type": "image/png", "index": index, "cell_index": index,
        "total": total, "access_url": "/outputs/" + relative,
    }}
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(3); conn.connect(socket_path)
            conn.sendall((json.dumps(payload) + "\n").encode()); conn.shutdown(socket.SHUT_WR)
            return bool(json.loads(conn.recv(4096)).get("ok"))
    except (OSError, ValueError, json.JSONDecodeError):
        return False


def configure_cache(pipe, adapter, threshold):
    if adapter in ("", "none", "off"):
        return
    if adapter != "first-block-cache":
        raise ValueError("adapter must be none or first-block-cache")
    from para_attn.first_block_cache.diffusers_adapters import apply_cache_on_pipe
    apply_cache_on_pipe(pipe, residual_diff_threshold=threshold, downsample_factor=1, warmup_steps=1)


def main():
    ap = argparse.ArgumentParser(description="Four-direction, council-directed FLUX lineage")
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--north-star", required=True)
    ap.add_argument("--id", required=True)
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--generations", type=int, default=1024)
    ap.add_argument("--size", type=int, default=512)
    ap.add_argument("--steps", type=int, default=28)
    ap.add_argument("--guidance", type=float, default=3.6)
    ap.add_argument("--seed", type=int, default=1935692473)
    ap.add_argument("--angle", type=float, default=0.12)
    ap.add_argument("--minimum-gain", type=float, default=2.0)
    ap.add_argument("--adapter", choices=("none", "first-block-cache"), default="none")
    ap.add_argument("--cache-threshold", type=float, default=0.08)
    ap.add_argument("--council-timeout", type=float, default=220)
    args = ap.parse_args()
    if args.size != 512:
        raise SystemExit("directional protocol is locked to 512x512 for this run")
    if args.generations < 1 or args.generations > 65536:
        raise SystemExit("generations must be in [1,65536]")

    out_dir = pathlib.Path(args.out_dir).expanduser()
    sphere = out_dir / "atlas" / f"{args.id}.sphere"
    work = sphere / "_work"
    sphere.mkdir(parents=True, exist_ok=True); work.mkdir(parents=True, exist_ok=True)
    state_path, manifest_path = sphere / "director-state.json", sphere / "manifest.json"
    decisions_path, control_path = sphere / "decisions.jsonl", sphere / "control.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {
        "generation": 0, "accepted": 0, "parent": [1.0, 0.0, 0.0, 0.0],
        "heading": [0.0, 1.0, 0.0, 0.0], "angle": args.angle,
    }
    if not control_path.exists():
        atomic_json(control_path, {"paused": False, "stop": False, "angle": args.angle,
                                   "steps": args.steps, "minimum_gain": args.minimum_gain,
                                   "north_star": args.north_star})
    manifest = {
        "kind": "directional_tournament", "study_type": "movement", "id": args.id,
        "prompt": args.prompt, "north_star": args.north_star, "size": args.size,
        "steps": args.steps, "precision": "bf16", "batch_size": 4,
        "directions": list(DIRECTIONS), "adapter": args.adapter,
        "cache_threshold": args.cache_threshold, "generation_target": args.generations,
        "out_dir": str(sphere), "status": "loading", "started": time.time(),
    }
    atomic_json(manifest_path, manifest)
    stopping = {"value": False}
    signal.signal(signal.SIGTERM, lambda *_: stopping.__setitem__("value", True))
    signal.signal(signal.SIGINT, lambda *_: stopping.__setitem__("value", True))

    print(f"loading BF16 FLUX from {args.model_dir}", flush=True)
    pipe = FluxPipeline.from_pretrained(args.model_dir, torch_dtype=torch.bfloat16, local_files_only=True).to("cuda")
    pipe.set_progress_bar_config(disable=True)
    prompt_embeds, pooled_prompt_embeds = pipe.encode_prompt(
        prompt=args.prompt, device="cuda", num_images_per_prompt=1, max_sequence_length=512)[:2]
    configure_cache(pipe, args.adapter, args.cache_threshold)
    generators = [torch.Generator("cpu").manual_seed(args.seed + offset)
                  for offset in (0, 104729, 209759, 314159)]
    raw = [pipe.prepare_latents(1, pipe.transformer.config.in_channels // 4, args.size, args.size,
                                torch.bfloat16, "cuda", generator)[0].detach().cpu().float()
           for generator in generators]
    flat = [item.flatten() for item in raw]
    radius = float(flat[0].norm())
    basis_cpu = []
    for vector in flat:
        for prior in basis_cpu:
            vector = vector - torch.dot(vector, prior) * prior
        basis_cpu.append(vector / vector.norm())
    basis = [vector.to("cuda") for vector in basis_cpu]
    shape = raw[0].shape
    del raw, flat, basis_cpu; gc.collect(); torch.cuda.empty_cache()

    parent_coeff = normalize(state["parent"]); heading = project_tangent(state["heading"], parent_coeff)
    parent_path = work / "parent.png"
    if not parent_path.exists():
        latent = compose_latent(parent_coeff, basis, radius, shape, torch.bfloat16)
        parent_image = pipe(prompt=None, prompt_embeds=prompt_embeds,
                            pooled_prompt_embeds=pooled_prompt_embeds,
                            width=args.size, height=args.size, num_inference_steps=args.steps,
                            guidance_scale=args.guidance, latents=latent).images[0]
        parent_image.save(parent_path)
    else:
        parent_image = Image.open(parent_path).convert("RGB")

    manifest["status"] = "running"; atomic_json(manifest_path, manifest)
    while state["generation"] < args.generations and not stopping["value"]:
        control = json.loads(control_path.read_text())
        if control.get("stop"):
            break
        if control.get("paused"):
            time.sleep(2); continue
        generation = state["generation"] + 1
        angle = max(0.01, min(1.2, float(control.get("angle", state.get("angle", args.angle)))))
        steps = max(1, min(120, int(control.get("steps", args.steps))))
        north_star = str(control.get("north_star") or args.north_star)
        child_coeffs, axes = candidates(parent_coeff, heading, generation, angle)
        latents = torch.cat([compose_latent(c, basis, radius, shape, torch.bfloat16) for c in child_coeffs])
        began = time.time()
        child_images = pipe(prompt=None, prompt_embeds=prompt_embeds,
                            pooled_prompt_embeds=pooled_prompt_embeds,
                            num_images_per_prompt=4, width=args.size, height=args.size,
                            num_inference_steps=steps, guidance_scale=args.guidance,
                            latents=latents).images
        gen_dir = work / f"generation-{generation:05d}"; gen_dir.mkdir(exist_ok=True)
        for i, image in enumerate(child_images):
            image.save(gen_dir / f"candidate-{i + 1}-{DIRECTIONS[i]}.png")
        sheet_path = gen_dir / "_council.jpg"
        sheet = make_sheet(parent_image, child_images, sheet_path)
        verdict, engine = consult(sheet, north_star, args.council_timeout)
        decision = direct(verdict, float(control.get("minimum_gain", args.minimum_gain)))
        selected = int(decision["selected"])
        if selected:
            parent_coeff = child_coeffs[selected - 1]
            heading = axes[selected - 1]
            parent_image = child_images[selected - 1]
            parent_image.save(parent_path)
            accepted = state["accepted"] + 1
            public = sphere / f"cell_{accepted - 1:05d}.png"
            parent_image.save(public)
            publish(args.id, public, accepted - 1, args.generations, out_dir)
            state["accepted"] = accepted
        # If change remains too small, the path widens; if a child advances,
        # approach the asymptote more carefully on the next decision.
        state["angle"] = max(0.02, min(0.9, angle * (0.96 if selected else 1.16)))
        state.update({"generation": generation, "parent": parent_coeff, "heading": heading,
                      "last_direction": decision["direction"], "updated": time.time()})
        row = {"generation": generation, "angle": angle, "steps": steps,
               "render_seconds": round(time.time() - began, 3), "engine": engine,
               "north_star": north_star, "decision": decision, "verdict": verdict,
               "parent": parent_coeff, "heading": heading, "ts": time.time()}
        append_json(decisions_path, row); atomic_json(state_path, state)
        manifest.update({"generation": generation, "accepted": state["accepted"],
                         "angle": state["angle"], "last_decision": decision,
                         "updated": time.time()})
        atomic_json(manifest_path, manifest)
        print(json.dumps({"generation": generation, "accepted": state["accepted"],
                          "decision": decision["direction"], "scores": decision["scores"],
                          "seconds": row["render_seconds"]}), flush=True)

    manifest["status"] = "stopped" if stopping["value"] else "done"
    manifest["finished"] = time.time(); atomic_json(manifest_path, manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
