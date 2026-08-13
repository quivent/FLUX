# Stallion study: capacity and next work

This note records the evidence gathered on 2026-08-12. No service, worker, or
render job was changed during the inspection.

## What the work actually is

The exhibition derives from the P3 latent-sphere study
`spheremap_atlas_parametergridatl_1781801154422_0`:

- 1,024 × 64 latent cells (65,536 possible states)
- 384 px, 40 steps, Omega mode, seed 7, batch size 24
- 65,536 completed cells
- full-shell scale 1.0 with spherical-outward traversal

The checked-in pause record is historical and stale: it captured the run at
7,584 cells, before completion. Tea's current 7,584-image grid is an authored
presentation snapshot from the completed corpus, not the corpus total. The
FLUX repository also has later Equine Lateral Motion and three 1,024-cell Volga
gallop path drafts; those are related experiments, not the source of the
current white-Stallion exhibition.

Two CPU continuity-graph specifications were recovered from the deployed
Atelier release. They treat the atlas as an image graph rather than pretending
that adjacent cell numbers are automatically temporal frames:

- Graph I: 12 candidate offsets, top-k 6, six planned CPU shards
- Graph II: 24 candidate offsets, stricter contour/color/histogram checks,
  top-k 3

## Live H100 capacity

The relevant running `anime-productions` H100 has a cgroup allocation of:

- 14 CPU cores (`cpu.max = 1400000 / 100000`)
- 115.1 GiB RAM, with 67.8 GiB in use and about 47.3 GiB headroom
- 246 GiB persistent home disk, with 143 GiB free
- 98 GiB scratch, with 93 GiB free (scratch is destroyed when the node stops)

A two-second cgroup-local sample used 0.374 CPU cores (2.67% of the allocation).
CPU pressure was effectively zero and the cgroup has recorded no memory high,
max, OOM, or OOM-kill events. Host `loadavg` is not a capacity signal here; it
describes the shared host, while the cgroup limits above are the real budget.

The H100 was GPU-idle during the sample, although about 34 GiB of its 80 GiB
VRAM was resident. CPU is not the immediate bottleneck for continuity mining,
compositing, scoring, or video encoding.

## Corpus location mismatch

The completed corpus is not mounted or indexed on the current
`anime-productions` H100. Both its node-local Sphere Library API and the live
`atelier.influx.vision` API currently return zero jobs, and direct Stallion cell
requests return 404. The current node has the historical record, experiment
code, contact sheet, grid image, and exhibition videos, but not the individual
cell directory.

The original motion host, `moest.influx.vision`, still resolves and accepts SSH
connections, but the SSH identity available in this workspace is rejected. Its
web ports are closed. Completion is therefore treated as authoritative while
the current visibility problem is treated as a storage/routing issue—not as an
incomplete render.

The canonical path expected on that host is:

`/home/ubuntu/render/data/motion/renders/spheremap_atlas_parametergridatl_1781801154422_0.sphere/`

## Safe execution shape

Once the completed corpus is made visible to the H100 or continuity workers:

1. Validate the manifest and count exactly 65,536 `cell_*.png` files.
2. Benchmark Graph II over 128–256 existing cells with one process.
3. Run six CPU shards as originally designed, leaving eight cores available to
   the server, inference orchestration, decoding, and the OS page cache.
4. Set `OMP_NUM_THREADS=1`, `OPENBLAS_NUM_THREADS=1`, and `MKL_NUM_THREADS=1`
   for each shard so six Python processes do not each attempt to claim all 14
   cores.
5. Merge candidate paths and inspect them before encoding public motion.

Six workers are deliberately conservative. The machine could likely sustain
10–12 small workers, but the current graph implementation repeatedly decodes
neighbor PNGs; increasing processes before measuring the page-cache and disk
behavior would add contention rather than useful throughput.

## Recommended next step

Do not render more Stallion cells. Reconnect or copy the completed corpus into
a path visible to the current Sphere Library, verify the first and last cell by
bytes, then benchmark the CPU continuity graph. The 14-core H100 allocation is
already sufficient for the original six-shard design.
