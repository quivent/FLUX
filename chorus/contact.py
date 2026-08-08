#!/usr/bin/env python3
"""contact — one montage of the run, so looking costs a single glance.

The failure this exists to prevent: shipping a change to the language, pulling
the newest one or two frames, and declaring it better. Two frames chosen by
recency are not evidence about a wall, and the newest is the one most likely
to flatter the change. Every iteration is judged against a representative
sample, and it has to be cheap or it will be skipped under pressure.

Samples EVENLY across the run rather than taking the tail, so an improvement
has to hold across the run to show up here.

The sheet writes into _sheets/, never beside the art. It is an instrument, and
a grid tile appearing in the gallery is the operator being shown my ruler
instead of the work -- which is exactly what happened.

    python3 chorus/contact.py --n 16
"""
import argparse
import pathlib

from PIL import Image, ImageDraw

DEFAULT_OUT = pathlib.Path.home() / "models" / "flux-output" / "_sheets" / "contact.jpg"


def sample(paths, n):
    """Even spread, oldest to newest. Recency bias is the thing to avoid."""
    if len(paths) <= n:
        return paths
    step = (len(paths) - 1) / (n - 1)
    return [paths[round(i * step)] for i in range(n)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(pathlib.Path.home() / "models" / "flux-output"))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--n", type=int, default=16)
    ap.add_argument("--cell", type=int, default=320)
    ap.add_argument("--cols", type=int, default=4)
    ap.add_argument("--recent", type=int, default=0,
                    help="sample only the newest N frames (0 = the whole run)")
    args = ap.parse_args()

    root = pathlib.Path(args.dir)
    frames = sorted(root.glob("drift-*.png"), key=lambda p: p.stat().st_mtime)
    if args.recent:
        frames = frames[-args.recent:]
    if not frames:
        print("no frames yet")
        return 1
    picked = sample(frames, args.n)

    cols = args.cols
    rows = (len(picked) + cols - 1) // cols
    cell = args.cell
    sheet = Image.new("RGB", (cols * cell, rows * cell), (12, 12, 14))
    draw = ImageDraw.Draw(sheet)

    for i, path in enumerate(picked):
        try:
            im = Image.open(path).convert("RGB")
        except OSError:
            continue
        im.thumbnail((cell - 4, cell - 4))
        x = (i % cols) * cell + (cell - im.width) // 2
        y = (i // cols) * cell + (cell - im.height) // 2
        sheet.paste(im, (x, y))
        # The index is the point of a contact sheet: it lets a critique name a
        # frame instead of gesturing at "some of them".
        draw.text((x + 4, y + 4), str(i + 1), fill=(255, 220, 120))

    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, quality=88)
    print(f"{len(picked)} of {len(frames)} frames -> {out}")
    for i, p in enumerate(picked, 1):
        print(f"  {i:2d} {p.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
