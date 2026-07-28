#!/usr/bin/env python3
"""Build-time renderer: Jinja2 templates -> static HTML for the Go server."""
import sys
import time
from pathlib import Path
from jinja2 import Environment, FileSystemLoader

ROOT = Path(__file__).parent
TEMPLATE_DIR = ROOT / "templates"
OUTPUT_DIR = ROOT

PAGES = {
    "atlas.html": "index.html",
    "optics.html": "optics.html",
    "queue.html": "queue.html",
    "registry.html": "registry.html",
    "governor.html": "governor.html",
    "visionary.html": "visionary.html",
}

def main():
    stamp = str(int(time.time()))
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        keep_trailing_newline=True,
    )
    env.globals["build_stamp"] = stamp
    for template_name, output_name in PAGES.items():
        template = env.get_template(template_name)
        html = template.render()
        out_path = OUTPUT_DIR / output_name
        out_path.write_text(html)
        print(f"  rendered {template_name} -> {output_name}")
    print(f"\n  {len(PAGES)} pages rendered. stamp={stamp}")

if __name__ == "__main__":
    main()
