#!/usr/bin/env python3
import argparse
import hashlib
import json
import pathlib
import re
import subprocess
import sys
import time


IMAGE_RE = re.compile(r"(p\d{2})/(p\d{2})-s(\d{2})-seed-(\d+)\.png$")


def expand(path):
    return pathlib.Path(path).expanduser()


def rel_output_name(output_dir, pass_id, source_path):
    match = IMAGE_RE.search(source_path.as_posix())
    if not match:
        stem = source_path.stem
        return f"{output_dir}/{pass_id}/{stem}-{pass_id}.png"
    persona, _, variant, seed = match.groups()
    return f"{output_dir}/{pass_id}/{persona}/{persona}-s{variant}-seed-{seed}-{pass_id}.png"


def stable_seed(seed_base, pass_id, source_path):
    h = hashlib.sha256(f"{seed_base}:{pass_id}:{source_path}".encode()).hexdigest()
    return str(100000 + (int(h[:10], 16) % 899900000))


def format_prompt(template, manifest, source_path, index):
    match = IMAGE_RE.search(source_path.as_posix())
    persona_id = match.group(1) if match else "p01"
    personas = manifest.get("personas") or {}
    beats = manifest.get("beats") or ["subtle motion"]
    worlds = manifest.get("worlds") or ["cinematic animated environment"]
    return template.format(
        persona=personas.get(persona_id, "anime cast member"),
        beat=beats[index % len(beats)],
        world=worlds[index % len(worlds)],
        persona_id=persona_id,
        source_name=source_path.name,
    )


def collect_sources(manifest, limit):
    root = expand(manifest["source_root"])
    sources = sorted(root.glob(manifest.get("source_glob", "**/*.png")))
    if limit and limit > 0:
        sources = sources[:limit]
    return root, sources


def run(args):
    manifest_path = expand(args.manifest)
    manifest = json.loads(manifest_path.read_text())
    root, sources = collect_sources(manifest, args.limit)
    if not sources:
        raise SystemExit(f"no source images matched under {root}")

    passes = manifest["passes"]
    if args.passes:
        wanted = {p.strip() for p in args.passes.split(",") if p.strip()}
        passes = [p for p in passes if p["id"] in wanted]

    output_root = expand(args.out_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    ledger = output_root / manifest["output_dir"] / "creative-run.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    progress_path = ledger.parent / "progress.json"
    total_steps = len(sources) * len(passes)
    completed_steps = 0

    def write_progress(state, source_index=0, pass_id=""):
        if args.dry_run:
            return
        progress = {
            "id": manifest["id"],
            "label": manifest.get("label", manifest["id"]),
            "state": state,
            "current": completed_steps,
            "total": total_steps,
            "source_index": source_index,
            "source_total": len(sources),
            "pass": pass_id,
            "updated": int(time.time()),
        }
        progress_path.write_text(json.dumps(progress, sort_keys=True, indent=2) + "\n")

    write_progress("running")

    rows = []
    for source_index, original in enumerate(sources):
        previous = original
        for step in passes:
            output_rel = rel_output_name(manifest["output_dir"], step["id"], original)
            output_abs = output_root / output_rel
            output_abs.parent.mkdir(parents=True, exist_ok=True)
            prompt = format_prompt(step["prompt"], manifest, original, source_index)
            seed = stable_seed(manifest.get("seed_base", 1), step["id"], original)
            row = {
                "manifest": manifest["id"],
                "source": str(original),
                "input": str(previous),
                "output": str(output_abs),
                "output_name": output_rel,
                "pass": step["id"],
                "seed": seed,
                "strength": step["strength"],
                "steps": step["steps"],
                "guidance": step["guidance"],
                "prompt": prompt,
            }
            rows.append(row)
            if args.dry_run:
                print(json.dumps(row, sort_keys=True))
            elif output_abs.exists() and not args.restart:
                print(f"skip existing {output_rel}", flush=True)
                completed_steps += 1
                write_progress("running", source_index + 1, step["id"])
            else:
                cmd = [
                    args.flux,
                    "img2img",
                    "--image", str(previous),
                    "--backend", manifest.get("backend", "auto"),
                    "--strength", str(step["strength"]),
                    "--steps", str(step["steps"]),
                    "--guidance", str(step["guidance"]),
                    "--width", str(manifest.get("width", 384)),
                    "--height", str(manifest.get("height", 384)),
                    "--seed", seed,
                    "--name", output_rel,
                    prompt,
                ]
                print(f"run {step['id']} {original.name} -> {output_rel}", flush=True)
                subprocess.run(cmd, check=True)
                completed_steps += 1
                write_progress("running", source_index + 1, step["id"])
            previous = output_abs

    if not args.dry_run:
        with ledger.open("a", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, sort_keys=True) + "\n")
        write_progress("complete")
        print(f"ledger={ledger}")


def main():
    parser = argparse.ArgumentParser(description="Run a creative multi-pass FLUX img2img manifest.")
    parser.add_argument("manifest")
    parser.add_argument("--flux", default="flux", help="flux CLI path")
    parser.add_argument("--out-dir", default="~/Models/flux-output", help="FLUX output root")
    parser.add_argument("--limit", type=int, default=0, help="limit source images; 0 means all")
    parser.add_argument("--passes", default="", help="comma-separated pass ids")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--restart", action="store_true", help="rerun existing outputs")
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        sys.exit(exc.returncode)
