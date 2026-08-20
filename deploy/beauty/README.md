# Beauty Studies Stack

The FLUX Tea surface, the governor gateway, and the aesthetic jury models as
one container.

## Why this directory exists

The stack shipped as a 10.5 GiB tarball at
`containers/h200-beauty-studies-latest.tar.zst` in the `governor` R2 bucket,
built `2026-08-20T14:51:54Z`. Nothing in any repo referenced it — no pull path,
no run script, no make target. Its build context (`Dockerfile`, `entrypoint.sh`,
`supervisord.conf`) sat loose beside it in R2 rather than in version control.

This directory is that build, in the repo, with the defects fixed.

## Provenance of the archived container

`.gemstone/governor.json` is the source of truth for the governor's posture.
The archive predates it and encodes a third, unrelated posture:

| | archive (14:51Z) | `governor configure` (16:44Z) | `governor.json` now |
|---|---|---|---|
| model | `redhatai/gemma-4-31b-it-fp8-dynamic` | `RedHatAI/gemma-4-31B-it-FP8-dynamic` | `google/gemma-4-31B-it-qat-w4a16-ct` |
| gpu util | 0.40 | 0.75 | 0.75 |
| context | 65536 | 131072 | 131072 |
| spec tokens | 5 | 8 | 3 |

Nothing was removed from the container. It was built before the governor ever
ran `configure`, and the governor has since re-cut twice — once to FP8-dynamic,
then again when the `a100` card profile landed and moved it to W4A16.

## Defects in the archived container

1. **GPU fractions are not additive shares.** vLLM reads
   `--gpu-memory-utilization` as an absolute fraction of the *whole card*, then
   subtracts memory other processes already hold. The shipped `0.40/0.22/0.20`
   are three per-process absolutes, so the second and third servers compute a
   negative KV budget and die with "No available memory for the cache blocks".
   `render-supervisord.sh` now emits **cumulative** fractions.
2. **Every vLLM program autostarts at once**, so each profiles free memory while
   the others are still allocating and all of them mis-size. Aux servers now
   wait on the governor's `/health` before starting.
3. **No weights baked in, no cache mount.** All services stampeded HuggingFace
   for >100 GiB on every cold container. Use `--target warm`, or `flux beauty up`,
   which mounts a named volume.
4. **`--unsafe-no-auth` on `0.0.0.0:7861`.** Now binds `127.0.0.1` by default via
   `BEAUTY_TEA_ADDR`.
5. **Not a defect.** An earlier revision of this file claimed the lowercased
   `redhatai/gemma-4-31b-it-fp8-dynamic` 404s and that `Qwen/Qwen3.8-27B-FP8`
   was not a real repo. Both claims were wrong, verified against the Hub on
   2026-08-20: the Hub 307-redirects lowercased ids to canonical casing, and
   the Qwen repo resolves with ~1.5M downloads.

## Format: prefer building over pulling

A `.tar.zst` is a single zstd stream, so it has no random access. Reading the
1 KB `supervisord.conf` out of it costs the full 10.5 GiB download, and changing
that file costs a full 10.5 GiB re-upload. An OCI image is already
content-addressed per layer — storing it blob-per-object (a registry, or an OCI
layout in R2) lets a client fetch only the blobs it lacks, in parallel.

R2 supports both axes of parallelism today: concurrent GETs across objects, and
HTTP `Range` within one object. But parallel is not selective — range-reading the
archive still transfers all 10.5 GiB. Note also that `gemstone r2`'s multipart
path is upload-only and sequential, and `pull` is a single-stream GET.

## Usage

```sh
make beauty-doctor           # posture vs governor and the card in this box
make beauty-build            # weights stay outside the image
HF_TOKEN=... make beauty-warm   # weights baked in, instant boot
make beauty-up               # run with a persistent model cache
```

`beauty-stage` copies the `flux` and `gemstone` binaries into `bin/` as build
context. They are gitignored; build them with `make go-build` and the gemstone
repo respectively.

## Tuning

Every model id and GPU fraction is an environment variable, so the same image
runs any posture — no rebuild to fix a typo.

| variable | default | meaning |
|---|---|---|
| `BEAUTY_GOVERNOR_MODEL` | from `governor.json` | governor brain |
| `BEAUTY_GOVERNOR_UTIL` | `0.55` | governor VRAM fraction |
| `BEAUTY_CODER_MODEL` | *(empty)* | coder; empty disables the service |
| `BEAUTY_VISION_MODEL` | `mistralai/Pixtral-12B-2409` | visual witness |
| `BEAUTY_CODER_UTIL` / `BEAUTY_VISION_UTIL` | `0.0` | aux fractions; `0` disables |
| `BEAUTY_FLUX_RESERVE_GIB` | `35` | VRAM the preflight holds back for FLUX |
| `BEAUTY_TEA_ADDR` | `127.0.0.1:7861` | Tea bind address |
| `BEAUTY_SKIP_PREFLIGHT` | unset | set `1` to start over-subscribed anyway |
