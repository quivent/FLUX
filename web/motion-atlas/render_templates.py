#!/usr/bin/env python3
"""Build-time renderer: Jinja2 templates -> static HTML, concatenated CSS/JS for the Go server."""
import os
import subprocess
import sys
import time
from pathlib import Path
from jinja2 import Environment, FileSystemLoader

ROOT = Path(__file__).parent
TEMPLATE_DIR = ROOT / "templates"
OUTPUT_DIR = ROOT
CSS_DIR = ROOT / "css"
JS_DIR = ROOT / "js"

PAGES = {
    "atlas.html": "index.html",
    "optics.html": "optics.html",
    "queue.html": "queue.html",
    "registry.html": "registry.html",
    "governor.html": "governor.html",
    "visionary.html": "visionary.html",
    "processing.html": "processing.html",
}

# Order matters for CSS cascade
CSS_ORDER = [
    "variables.css",
    "status.css",
    "navigator.css",
    "stage.css",
    "inspector.css",
    "layout.css",
    "spine.css",
    "presets.css",
    "gauges.css",
    "controls.css",
    "viewport.css",
    "model.css",
    "dialogs.css",
    "media.css",
    "extra.css",
]

# Order matters for JS dependencies
JS_ORDER = [
    "core.js",
    "studies.js",
    "assets.js",
    "map.js",
    "submit.js",
    "gpu.js",
    "jobs.js",
    "stage.js",
    "fuel.js",
    "prefill.js",
    "presets.js",
    "init.js",
]


def concat_files(directory, order, output_path):
    """Concatenate files in order from directory into output_path."""
    parts = []
    for name in order:
        path = directory / name
        if path.exists():
            parts.append(path.read_text())
    # Also include any files not in the order list
    existing = {f.name for f in directory.iterdir() if f.suffix in ('.css', '.js')}
    for name in sorted(existing - set(order)):
        parts.append((directory / name).read_text())
    output_path.write_text('\n'.join(parts))
    return len(parts)


def main():
    stamp = str(int(time.time()))
    try:
        git_rev = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(ROOT), stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception:
        git_rev = "unknown"

    # Concatenate CSS
    if CSS_DIR.exists():
        n = concat_files(CSS_DIR, CSS_ORDER, OUTPUT_DIR / "app.css")
        print(f"  css: {n} files -> app.css")

    # Concatenate JS
    if JS_DIR.exists():
        n = concat_files(JS_DIR, JS_ORDER, OUTPUT_DIR / "app.js")
        print(f"  js:  {n} files -> app.js")

    # Render Jinja templates
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        keep_trailing_newline=True,
    )
    env.globals["build_stamp"] = stamp
    env.globals["gpu_count"] = int(os.environ.get("FLUX_GPU_COUNT", "4"))
    env.globals["git_rev"] = git_rev

    for template_name, output_name in PAGES.items():
        template = env.get_template(template_name)
        html = template.render()
        out_path = OUTPUT_DIR / output_name
        out_path.write_text(html)
        print(f"  rendered {template_name} -> {output_name}")

    print(f"\n  {len(PAGES)} pages · stamp={stamp} · rev={git_rev}")


if __name__ == "__main__":
    main()
