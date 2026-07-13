# FLUX Acceleration Architecture

This project should treat acceleration as a scheduler problem, not as a single
library choice. The socket and HTTP APIs stay stable while worker backends can
change underneath.

## Current Default Backend

The current default backend is:

```text
flux CLI / HTTP
  -> Unix socket
    -> worker.py
      -> Diffusers FluxPipeline
        -> PyTorch
          -> MPS / Metal GPU
```

Properties:

- Uses the BF16 Diffusers checkpoint at `/Users/joshkornreich/Models/flux1`.
- Uses `torch.bfloat16`.
- Uses `mps` when PyTorch reports it available.
- Keeps one resident worker process so weights are loaded once.
- Supports per-job backend routing through `backend=auto|mps|mlx|coreml|ane|cpu`.
- Has MLX and Core ML tooling installed in the local venv.
- Records completed socket render timings in `.fluxd/profile.json`.
- Does not use ANE unless a future Core ML converted model can actually run
  through a Neural Engine capable graph.

## Target Topology

```text
clients
  -> flux CLI / Atelier / HTTP callers
    -> HTTP server
      -> local scheduler
        -> worker backend=mps       PyTorch Diffusers, MPS baseline
        -> worker backend=mlx       mflux / MLX native Apple Silicon path
        -> worker backend=coreml    Core ML compiled packages, fixed shapes
        -> worker backend=ane       strict ANE adapter, registry proof required
        -> worker backend=cpu       CPU fallback / diagnostics
```

The API contract should remain:

```json
{"op":"submit","backend":"mlx","prompt":"...","width":1024,"height":1024,"steps":28,"guidance":3.5,"seed":"123"}
```

Every backend must respond with the same job fields:

```json
{"id":"...","backend":"mps","status":"running","phase":"sampling","step":12,"total_steps":28}
```

## Backend Roles

### PyTorch MPS

Role: compatibility baseline and default production path.

Use it for:

- first implementation of every feature
- maximum Diffusers compatibility
- BF16 local checkpoint reuse
- debugging correctness

Limitations:

- no Neural Engine use
- some operations may fall back to CPU if MPS lacks coverage
- not the most Apple-native execution model

### MLX

Role: primary Apple Silicon optimization target.

Use it for:

- native Apple unified-memory execution
- long-running resident worker
- Metal GPU path with lower framework overhead
- future quantized variants if quality is acceptable

Requirements:

- `mlx`
- `mflux`
- checkpoint conversion or compatible loader where needed
- parity tests against the MPS backend

### Core ML

Role: fixed-shape compiled inference backend.

Use it for:

- repeated production sizes such as `896x1344`, `1024x1024`, `1344x768`
- compiled model caching
- possible CPU/GPU/Neural Engine scheduling through Core ML compute units

Limitations:

- conversion is the hard part
- FLUX graph size and operator support can make this expensive
- dynamic prompts and sizes need careful model partitioning
- best treated as a specialized backend, not the default path
- Core ML runtime availability does not prove Neural Engine use

Why this path is required:

- Apple does not expose a supported public ANE kernel API comparable to Metal.
- Core ML is the supported compiler/runtime path that can schedule model
  programs on CPU, GPU, ANE, or a hybrid plan.
- Private/reverse-engineered ANE APIs can exist, but they are version-fragile
  and are not the default target for this CLI.

### ANE Adapter

Role: experimental dedicated adapter for proven Apple Neural Engine execution.

This is distinct from the generic `coreml` backend:

- `coreml` means "run a compiled Core ML package." Core ML may schedule work on
  CPU, GPU, ANE, or a hybrid plan.
- `ane` means "this job has a fixed-shape compiled path and profiling confirms
  meaningful Neural Engine activity."

The adapter should not be marked complete just because `coremltools` is
installed or a model package exists. It needs measurement.

Likely architecture:

```text
FLUX checkpoint
  -> component partitioning
  -> fixed-shape graph specialization
  -> Core ML / MIL conversion
  -> compiled mlpackage / mlmodelc registry at model/ane/registry.json
  -> socket worker backend=ane
  -> Instruments validation of ANE counters
```

Candidate partition order:

1. VAE decoder for fixed latent/image sizes.
2. Text encoder subgraphs where conversion is stable.
3. Denoiser/MMDiT blocks in fixed-shape slices.
4. Full denoising path only after partial components prove faster or lower
   power than MPS/MLX.

Current implementation:

- `flux_ane.py` owns the ANE package registry and conversion commands.
- `flux_direct_ane.py` owns direct-ANE denoiser target probes.
- `flux ane init` creates `model/ane/registry.json`.
- `flux ane convert-vae --width 1024 --height 1024` converts the fixed-shape
  FLUX VAE decoder to a Core ML `mlpackage`.
- `flux ane direct-capture --width 1024 --height 1024 --block-type dual
  --block-index 0` captures the first MMDiT block manifest under
  `model/ane/direct/`.
- `flux ane direct-pack --manifest <manifest>` creates a first direct-ANE
  weight-packing plan next to the manifest.
- `flux ane direct-benchmark` measures the synthetic MPS dense matmul slice
  from the captured plans.
- `flux ane direct-block-benchmark` measures real Diffusers transformer block
  forward passes on MPS with synthetic captured-shape tensors.
- `flux ane direct-latent-benchmark` measures real `FluxPipeline` latent-output
  runs at multiple step counts to estimate denoise seconds per step with VAE
  decode excluded.
- `flux ane direct-component-benchmark` measures real FLUX block submodules on
  MPS with independent synchronization per component.
- `flux ane direct-aneforge-projections` measures direct-ANE projection kernels
  through ANEForge/e5rt with zero-copy buffers and no Core ML.
- `flux ane direct-aneforge-optimized` measures the optimized direct-ANE
  projection plan: fused same-input projections plus chunked high-K output
  projections.
- `flux ane direct-contract` creates a dense runtime contract and break-even
  budget from the direct-ANE fit artifacts.
- `flux ane direct-report` prints the dense offload report in human-readable
  form.
- The VAE conversion uses `torch.export` plus decompositions. The naive
  TorchScript path emitted Core ML frontend failures for FLUX VAE dtype/cast
  behavior.
- `worker.py` accepts `backend=ane`, but fails closed unless the registry has
  a validated full-pipeline package for the requested size.
- `backend=auto` may only select `ane` when `ane_renderable=true`.

Registry fields:

```json
{
  "version": 1,
  "packages": [
    {
      "name": "vae_decoder_1024x1024",
      "component": "vae_decoder",
      "width": 1024,
      "height": 1024,
      "latent_shape": [1, 16, 128, 128],
      "precision": "fp32",
      "compute_units": "cpu_and_ne",
      "package_path": "/Users/joshkornreich/Models/flux1/ane/vae_decoder_1024x1024.mlpackage",
      "ane_validated": false
    }
  ]
}
```

Capability meanings:

- `ane_packages`: existing Core ML packages listed in the registry.
- `ane_components`: component names present in those packages.
- `ane_validated`: at least one package has recorded external validation.
- `ane_renderable`: a validated `component=pipeline` package exists for
  full-job rendering. VAE decoder packages alone keep this false.

Acceptance criteria:

- fixed sizes are explicit, such as `768x768`, `1024x1024`, or `896x1344`
- unsupported operators fall back intentionally, not silently
- output parity is checked against `mps` or `mlx` with the same seed and prompt
- Instruments shows ANE activity during the target component
- the run reduces at least one meaningful cost: latency, memory pressure, GPU
  occupancy, CPU utilization, or energy
- `flux accel` reports the compiled package and whether ANE validation exists
- `flux bench --backends ane,mps,mlx` records comparable timings in
  `.fluxd/profile.json`

Risks:

- Core ML does not provide hard placement control; compute-unit settings are
  requests, not proof.
- FLUX attention and transformer blocks may require graph rewrites, static
  sequence lengths, layout changes, or partitioning to become ANE-friendly.
- A full FLUX.1-dev ANE graph may not be practical; a partial adapter can still
  be useful if it frees GPU resources or reduces power.
- Direct private ANE APIs are research-grade and should not be a default path
  for this CLI.

### Direct ANE Research Track

The Core ML path is the supported Apple route into ANE. A custom connector below
Core ML is still possible, but it is a separate research/runtime track with a
different risk profile. Do not mix the two claims:

- `coreml`: supported compiler/runtime path; placement still requires proof.
- `ane`: strict backend contract; requires measured Neural Engine execution.
- `direct-ane`: custom runtime path; private/reverse-engineered and
  version-fragile unless Apple exposes a public API.

Direct connector checklist:

1. **Define the target subgraph.** Start with one MMDiT block, then a block
   group, then one full denoise step. Freeze batch, resolution, sequence
   lengths, dtype, and guidance mode. Current first target is
   `dual[0] = FluxTransformerBlock` at `1024x1024`.
   First captured manifest:
   `/Users/joshkornreich/Models/flux1/ane/direct/dual_block_0_1024x1024.json`.
   The captured block takes image hidden states `[1, 4096, 3072]`, text hidden
   states `[1, 512, 3072]`, timestep embedding `[1, 3072]`, and rotary tensors
   `[4608, 128]`. Its parameter inventory is 32 tensors, about 648 MB at the
   captured precision.
   First pack plan:
   `/Users/joshkornreich/Models/flux1/ane/direct/dual_block_0_1024x1024.packplan.json`.
   At bf16 scale the block splits into about 288 MB MLP, 216 MB modulation,
   144 MB attention, and small norm/bias tensors. A `128x128` tile has no
   padding overhead for the captured matrix shapes.
   First projection plan:
   `/Users/joshkornreich/Models/flux1/ane/direct/dual_block_0_1024x1024.projectionplan.json`.
   It isolates 8 dense projection groups covering 432 MB of weights, 13,824
   `128x128` tiles, and 1.044 TFLOP per dual block. An isolated MPS bf16
   baseline for these groups runs in about 55 ms, or 18.9 effective TFLOP/s.
2. **Static shape contract.** Lock production sizes such as `1024x1024`,
   `1344x768`, and `768x1344`. Precompute latent/image token geometry and text
   token bounds.
3. **Layout contract.** Choose ANE-native layouts for hidden states, text
   states, Q/K/V, MLP intermediates, residuals, and boundary tensors. Avoid
   per-block transpose churn.
4. **Precision contract.** Choose fp16/bf16/int8/int4 per tensor class. Validate
   latent and image quality, not only individual op error.
5. **Weight residency and packing.** This is the hardest part. Pack
   linear/attention/MLP weights into ANE-friendly tiles, keep them resident or
   reload them cheaply, avoid per-prompt or per-step recompilation, preserve
   quantization scale metadata, handle the large FLUX transformer weights
   without memory blowups, and version packed artifacts by chip/runtime
   behavior.
6. **Op lowering.** Lower FLUX block operations into executable primitives:
   projections, attention or attention decomposition, MLP/SwiGLU, norms,
   modulation, residual adds, and positional transforms where needed.
7. **Dispatch runtime.** Build the bridge for IOSurface/shared buffers, command
   submission, synchronization, stream scheduling, compiled program caching,
   CPU-bounce avoidance, and observable failure modes.
8. **Scheduler integration.** Keep the FLUX worker queue stable while routing
   unsupported pieces to GPU/MPS and offloaded block groups to ANE. Profile by
   shape and backend.
9. **Validation harness.** Compare against MPS/MLX using per-block tensor error,
   full-step latent error, final image similarity, seed behavior, and a prompt
   regression corpus.
10. **Performance harness.** Measure block latency, full-step latency, data
    movement, GPU occupancy freed, ANE utilization, power, thermals, and
    saturated images/hour.
11. **Fallback matrix.** Define fallback for unsupported shapes, unsupported op
    variants, validation drift, thermal throttling, runtime failure, and memory
    pressure.
12. **Production package format.** Produce a versioned artifact keyed by shape,
    chip, precision, layout, packed weights, and runtime metadata.

The economic target is not single-image latency first. It is saturated
throughput: images/hour/GPU, p95 latency impact, quality parity, and failure
rate. Even a one-percent GPU-lane efficiency gain is material at fleet scale,
but it only counts if it increases saturated throughput rather than moving work
into hidden copy or synchronization costs.

#### Direct ANE Quantitative Baseline

Captured at `1024x1024`, batch 1, max text length 512.

Token and hidden-state geometry:

| Tensor | Shape | Notes |
| --- | ---: | --- |
| image hidden states | `[1, 4096, 3072]` | 4096 image tokens |
| text hidden states | `[1, 512, 3072]` | 512 text tokens |
| joined attention stream | `4608 tokens` | image + text |
| attention heads | `24` | head dim 128 |
| model hidden dim | `3072` | 24 * 128 |
| MLP expansion dim | `12288` | 4x hidden dim |

Per-block measurements from manifests:

| Block type | Count | Weight per block | Counted compute per block |
| --- | ---: | ---: | ---: |
| dual MMDiT block | 19 | 648 MB | 1.305 TFLOP |
| single MMDiT block | 38 | 270 MB | 1.305 TFLOP |

Whole denoiser block stack:

| Stack area | Weight total | Counted compute per denoise step |
| --- | ---: | ---: |
| 19 dual blocks | 12.03 GB | 24.79 TFLOP |
| 38 single blocks | 10.02 GB | 49.58 TFLOP |
| all blocks | 22.05 GB | 74.37 TFLOP |

Full generation counted block compute:

| Steps | Counted block compute |
| ---: | ---: |
| 28 | 2,082 TFLOP |
| 40 | 2,975 TFLOP |

Approximate block weight footprint by precision:

| Precision | All block weights | Dual block | Single block |
| --- | ---: | ---: | ---: |
| bf16/fp16 | 22.05 GB | 648 MB | 270 MB |
| int8 | ~11.0 GB + scales | 324 MB | 135 MB |
| int4 | ~5.5 GB + scales | 162 MB | 67 MB |

One dual block compute split:

| Area | Counted compute |
| --- | ---: |
| MLP image/context | ~696 GFLOP |
| attention projections and outputs | ~406 GFLOP |
| attention QK and AV matmuls | ~261 GFLOP |
| modulation | ~0.2 GFLOP |

Using the local MPS profile of roughly `180s` for `1024x1024` at 28 steps,
the counted block stack implies an end-to-end effective throughput of about
`11.6 TFLOP/s`:

```text
2,082 TFLOP / 180s = 11.6 TFLOP/s
```

This is not hardware peak. It is effective throughput through
Diffusers/PyTorch/MPS plus framework overhead, memory behavior, synchronization,
and non-counted ops. It is the baseline a direct ANE path must beat in
saturated throughput.

Measured phase priority on this machine:

| Phase | Measured time at `1024x1024` |
| --- | ---: |
| prompt/text encode | ~0.91s |
| preencoded denoise, 1 step | ~6.09s |
| preencoded denoise, 2 steps | ~12.15s |
| preencoded denoise, 4 steps | ~24.23s |
| implied denoise cost per step | ~6.05s |
| implied 28-step denoise cost | ~169s |

Conclusion:

- Text encoders are not the first offload target on this machine. They are
  under one second for this prompt.
- The VAE Core ML component did not prove a useful GPU-lane win. In isolated
  tests, MPS VAE decode was faster than the current Core ML package, and
  concurrent Core ML VAE execution materially slowed MPS work.
- The meaningful ANE target is the denoiser block stack, not VAE.
- Within the denoiser, use ANE for what it is good at: static-shape, resident,
  dense tensor work. First candidates are MLP projections and attention
  QKV/out projections, with weights packed once and reused across denoise
  steps.
- The first concrete direct-ANE projection target is one dual block's dense
  projection groups: 432 MB of weights and 1.044 TFLOP per invocation. To beat
  the isolated MPS baseline, an ANE runtime must run this below ~55 ms including
  dispatch and tensor boundary costs. A 2x win would require ~27 ms for the
  group; a 3x win would require ~18 ms.
- Avoid CPU/ANE mixed proof points for performance claims. CPU and ANE are
  different execution domains; a direct-ANE target should minimize CPU bounce
  and make placement measurable.

Rejected side path:

Do not use Core ML `CPU_AND_NE` projection packages as evidence for or against
the direct-ANE track. `CPU_AND_NE` mixes CPU and ANE placement, hides scheduling,
and creates the wrong performance question. The direct-ANE track must measure a
lower-level packed-kernel/runtime prototype with explicit tensor boundaries,
not an Apple-managed CPU/ANE hybrid package.

#### ANE Fit Baseline

The first fit pass uses `ane_fit.py` against
`dual_block_0_1024x1024.projectionplan.json`. The profile is
`m4max_h16g_estimate`: M4 Pro/Max resolves to the H16G compiler target in the
reverse-engineered target table, but the exact multiplier layout is not public
Apple documentation. The sizing rules used here are therefore explicit inputs,
not hidden assumptions:

| Field | Value used |
| --- | ---: |
| advertised Neural Engine throughput | 38 TOPS |
| marketed NE cores | 16 |
| dtype target | fp16 |
| working-set operand cap | 2 MB |
| bank count | 64 |
| bank granule | 16 B |
| K alignment | 4 |
| channel alignment | 64 |

Fit result for one dual block's 8 dense projection groups:

| Metric | Value |
| --- | ---: |
| matmuls | 12 |
| total projection compute | 1.044 TFLOP |
| full matmuls fit without tiling | no |
| chosen tiles | 540 |
| image-token tile | `[1024, 1024, 1024]` |
| text-token tile | `[512, 1024, 1024]` |
| image tile largest operand | 2 MB |
| text tile largest operand | 2 MB |
| image tile compute | 2.147 GFLOP |
| text tile compute | 1.074 GFLOP |
| image tile arithmetic intensity | 341 FLOP/B |
| text tile arithmetic intensity | 256 FLOP/B |

Group tile counts:

| Group | Tiles |
| --- | ---: |
| image QKV | 108 |
| text QKV | 27 |
| image attention out | 36 |
| text attention out | 9 |
| image MLP in | 144 |
| image MLP out | 144 |
| text MLP in | 36 |
| text MLP out | 36 |
| total | 540 |

The ideal arithmetic lower bound for the whole projection plan at 38 TOPS is:

```text
1.044 TFLOP / 38 TOPS = 27.5 ms
```

That is not a runtime estimate. It excludes launch, tiling, data movement,
program scheduling, stores, and synchronization. It is the absolute compute
floor if the ANE multiplier path is fully fed.

Against the measured MPS projection baseline of about 55 ms, the shape fit says:

- A 2x win is only possible if the direct runtime stays near the 27.5 ms
  arithmetic floor.
- Per-tile host dispatch is impossible; `540 * 0.23 ms` would already exceed
  124 ms before useful compute. This is a warning about a naive implementation,
  not the target design.
- The target design is one persistent/fused ANE program with an internal tile
  loop, resident packed weights, and explicit boundary buffers. In that model,
  host dispatch is not paid per tile. The latency model becomes:

```text
T ~= program_launch_or_resume
   + internal_tile_loop_overhead
   + max(compute_time, memory_stream_time)
   + boundary_sync
```

- The selected tile shape is plausible because each tile's largest operand is
  at the 2 MB cap and arithmetic intensity is above the M1 roofline ridge point
  reported by the reverse-engineered study.

Expanded projection-slice measurement:

The same capture/plan/fit/benchmark path was extended to `single[0]`. The
combined summary is stored at:

```text
/Users/joshkornreich/Models/flux1/ane/direct/projection_slice_1024x1024_summary.json
```

Measured isolated MPS projection baselines:

| Block type | Blocks | Projection groups | MPS time per block | Compute per block |
| --- | ---: | ---: | ---: | ---: |
| dual | 19 | 8 | 74.7 ms | 1.044 TFLOP |
| single | 38 | 3 | 74.3 ms | 1.044 TFLOP |

Scaled to a 28-step render:

| Metric | Value |
| --- | ---: |
| projection time per denoise step on MPS | 4.24s |
| projection time over 28 steps on MPS | 118.7s |
| projection compute per denoise step | 59.5 TFLOP |
| projection compute over 28 steps | 1,666 TFLOP |
| 38 TOPS arithmetic floor per step | 1.57s |
| 38 TOPS arithmetic floor over 28 steps | 43.8s |
| ideal floor saving vs MPS projection time | 74.9s/render |
| ideal floor saving vs ~180s render | ~41.6% |

This is still a projection-only estimate from isolated MPS linear benchmarks
with synthetic bf16 tensors. It excludes attention QK/AV, softmax, activations,
norms, scheduler, data movement, and integration overhead. It is useful because
it converts the direct-ANE question into a bounded test:

```text
Can a persistent packed ANE projection program run the dual+single projection
slice materially closer to 43.8s/render than the current MPS projection
estimate of 118.7s/render?
```

Expanded dense-matmul slice:

Attention QK/AV matmuls were added as a separate plan for `dual[0]` and
`single[0]`. Softmax is still excluded. The combined summary is stored at:

```text
/Users/joshkornreich/Models/flux1/ane/direct/dense_slice_1024x1024_summary.json
```

Measured isolated MPS attention baselines:

| Block type | Blocks | MPS QK/AV time per block | Compute per block |
| --- | ---: | ---: | ---: |
| dual | 19 | 18.7 ms | 0.261 TFLOP |
| single | 38 | 18.9 ms | 0.261 TFLOP |

Combined projections + attention QK/AV:

| Metric | Value |
| --- | ---: |
| dense matmul time over 28 steps on MPS | 148.8s |
| dense matmul compute per denoise step | 74.36 TFLOP |
| dense matmul compute over 28 steps | 2,082 TFLOP |
| 38 TOPS arithmetic floor over 28 steps | 54.8s |
| ideal floor saving vs MPS dense time | 94.0s/render |
| ideal floor saving vs ~180s render | ~52.2% |

This dense slice now covers the counted block-stack matmul work: projections
plus attention QK/AV. It still excludes softmax, norms, activations, residuals,
scheduler, tensor-layout costs, and integration overhead. The direct-ANE
question is now sharper:

```text
Can a persistent ANE program keep the dense block-stack matmuls closer to
54.8s/render than the current isolated MPS estimate of 148.8s/render?
```

Runtime contract and break-even budget:

The current contract is stored at:

```text
/Users/joshkornreich/Models/flux1/ane/direct/direct_runtime_contract_1024x1024.json
```

It answers what the MPS dense benchmark means:

- It is an isolated synthetic bf16 PyTorch/MPS timing for the exact FLUX-shaped
  dense matmul families we want to remove from the GPU.
- It is not a full-render benchmark and not ANE proof.
- It is the denominator for the offload question: how much GPU dense-matmul
  time can a resident direct-ANE path plausibly steal?

The dense opportunity is substantial, but it has a strict overhead budget:

| Metric | Value |
| --- | ---: |
| full MPS render reference | 180.0s |
| isolated MPS dense slice | 148.8s |
| dense slice share of render | 82.7% |
| dense compute over 28 steps | 2,082 TFLOP |
| effective MPS dense throughput | 14.00 TFLOP/s |
| ANE arithmetic floor at 38 TOPS | 54.8s |
| ideal dense speedup vs MPS | 2.72x |
| ideal render saving | 94.0s |
| best-case render if only dense changes | 86.0s |

Measured latent-pipeline cross-check:

The latent pipeline benchmark is stored at:

```text
/Users/joshkornreich/Models/flux1/ane/direct/latent_pipeline_1024x1024_benchmark.json
```

It runs the real `FluxPipeline` on MPS with `output_type="latent"` for 1, 2,
and 4 denoise steps. That skips VAE decode but keeps prompt encoding,
transformer denoising, scheduler work, and pipeline overhead.

| Step count | Measured latent-output time |
| ---: | ---: |
| 1 | 6.89s |
| 2 | 12.57s |
| 4 | 24.73s |

Derived line:

| Metric | Value |
| --- | ---: |
| denoise slope without VAE | 5.95s/step |
| fixed overhead/intercept | 0.94s |
| projected 28-step latent run | 167.5s |
| dense share of pipeline step slope | 89.3% |
| block stack share of pipeline step slope | 102.0% |

The block-stack value slightly exceeding the pipeline slope is measurement
noise from using separate harnesses, but the conclusion is useful: the actual
latent denoise step and the measured transformer block stack agree closely.
The dense matmul slice is about 89% of the real pipeline step slope.

Measured block-stack cross-check:

The block benchmark is stored at:

```text
/Users/joshkornreich/Models/flux1/ane/direct/block_stack_1024x1024_benchmark.json
```

It loads the real Diffusers FLUX transformer modules and times actual MPS
forward calls for one representative dual block and one representative single
block using synthetic tensors with the captured shapes. This is still not ANE
execution and not a final-image correctness run; it is a measurement of the
real block module cost.

| Metric | Value |
| --- | ---: |
| dual block median forward | 108.4 ms |
| single block median forward | 105.5 ms |
| scaled block stack over 28 steps | 169.9s |
| measured dense slice over 28 steps | 148.8s |
| dense share of measured block stack | 87.6% |
| non-dense or measurement gap | 21.1s |
| best-case block stack if only dense changes | 75.9s |

This is the strongest current local proof that the ANE target is pointed at the
right part of the denoiser: the dense matmul slice accounts for almost all of
the measured transformer block runtime under the block harness.

Measured component breakdown:

The component benchmark is stored at:

```text
/Users/joshkornreich/Models/flux1/ane/direct/component_1024x1024_benchmark.json
```

It times real FLUX block submodules on MPS with real weights and
captured-shape synthetic tensors. Each component is synchronized independently,
so the component sum is a diagnostic breakdown rather than an exact replacement
for full-block timing. The seven-iteration run still lands close to the block
benchmark:

| Metric | Value |
| --- | ---: |
| component sum over 28 steps | 167.1s |
| measured block stack over 28 steps | 169.9s |
| component sum vs block stack | 98.3% |

Scaled component costs over 28 steps:

| Component | Median per block | Scaled time | Share of component sum |
| --- | ---: | ---: | ---: |
| single attention module | 45.7 ms | 48.6s | 29.1% |
| single fused output projection | 31.5 ms | 33.5s | 20.0% |
| dual attention module | 53.1 ms | 28.2s | 16.9% |
| dual MLP pair | 50.3 ms | 26.8s | 16.0% |
| single MLP-in + activation | 24.8 ms | 26.4s | 15.8% |
| single AdaLN modulation | 2.1 ms | 2.2s | 1.3% |
| dual AdaLN modulation pair | 2.5 ms | 1.3s | 0.8% |

This narrows the remaining implementation focus:

- Attention modules are the largest measured bucket, but include QKV/out
  projections, QK/AV matmuls, rotary application, softmax, reshape, and stores.
- MLP and fused projection work is also large and mostly static dense tensor
  work.
- AdaLN/modulation is not where the time is.
- The direct-ANE runtime should prioritize attention projections/QK/AV and MLP
  projections before spending effort on small normalization paths.

Measured direct-ANE projection evidence:

The first actual direct-ANE timing artifact is:

```text
/Users/joshkornreich/Models/flux1/ane/direct/aneforge_projection_1024x1024_benchmark.json
```

It uses ANEForge `0.2.0`, which dispatches through Espresso/e5rt private runtime
bindings, not Core ML. The tested lowering is:

```text
FLUX projection matmul
  -> ANEForge 1x1 conv layout [B, C, 1, S]
  -> int8 compressed weights
  -> zero-copy input/output views
  -> direct ANE execute
```

This is the first measured time-saving evidence. It is selective, not a
blanket win:

| Projection class | MPS | Direct ANE | Speedup | 28-step impact |
| --- | ---: | ---: | ---: | ---: |
| dual image QKV fused | 16.43 ms | 15.33 ms | 1.07x | +0.6s |
| dual image MLP-in | 21.57 ms | 19.83 ms | 1.09x | +0.9s |
| single joint QKV fused | 18.44 ms | 16.84 ms | 1.10x | +1.7s |
| single joint MLP-in | 24.52 ms | 22.87 ms | 1.07x | +1.7s |
| dual text QKV fused | 3.51 ms | 2.16 ms | 1.62x | +0.7s |
| dual text MLP-in | 2.91 ms | 2.69 ms | 1.08x | +0.1s |
| dual image MLP-out | 22.28 ms | 78.75 ms | 0.28x | -30.0s |
| single fused out | 31.97 ms | 109.28 ms | 0.29x | -82.3s |

Summary:

| Metric | Value |
| --- | ---: |
| selective positive direct-ANE saving | 5.8s / 28-step render |
| positive share of 180s render | 3.2% |
| net result if every tested projection is offloaded | -106.5s |

Conclusion:

- We now have real direct-ANE time savings, not only an arithmetic floor.
- The measured win is currently small but economically meaningful at scale.
- The offload plan must be selective. Input-side projections/QKV/MLP-in can
  move first; high-K output projections must stay on MPS until a better
  lowering exists.
- The earlier 50%+ upside remains an upper-bound target, not a current measured
  result.

Measured optimized direct-ANE projection plan:

The optimized direct-ANE timing artifact is:

```text
/Users/joshkornreich/Models/flux1/ane/direct/aneforge_optimized_projection_plan_1024x1024.json
```

Run it with:

```zsh
flux ane direct-aneforge-optimized
flux ane direct-contract
flux ane direct-report
```

This is still direct ANE through ANEForge/e5rt, not Core ML. The difference
from the first projection benchmark is that it changes the lowering:

- Same-input projections are fused into one larger 1x1 conv program. For a
  dual image block, QKV + attention output + MLP-in become one ANE program.
- High-K output projections are split along K and summed inside the ANE graph.
  This avoids the slow single-conv lowering that made the first output
  projection attempts unusable.

Current measured results:

| Optimized projection group | MPS separate | MPS fused lower bound | Direct ANE | Speedup vs separate | 28-step impact |
| --- | ---: | ---: | ---: | ---: | ---: |
| dual image input fused | 43.71 ms | 42.92 ms | 39.52 ms | 1.11x | +2.2s |
| dual image MLP-out chunk4 | 22.09 ms | 22.11 ms | 21.69 ms | 1.02x | +0.2s |
| dual text input fused | 7.21 ms | 5.53 ms | 5.57 ms | 1.29x | +0.9s |
| dual text MLP-out chunk8 | 3.11 ms | 3.14 ms | 2.64 ms | 1.18x | +0.3s |
| single input fused | 43.07 ms | 42.48 ms | 40.11 ms | 1.07x | +3.1s |
| single fused out chunk4 | 30.82 ms | 31.18 ms | 30.59 ms | 1.01x | +0.2s |

Summary:

| Metric | Value |
| --- | ---: |
| total saving vs current MPS separate projection calls | 7.0s / 28-step render |
| total saving vs fused MPS lower-bound projection calls | 5.4s / 28-step render |
| share of 180s render reference removed | 3.9% |
| share of measured block stack removed | 4.1% |
| share of modeled 94.0s ideal dense saving reached | 7.4% |
| tested optimized cases positive | yes |

The most important change is not the extra `~1.2s` over the first selective
plan. It is that the two catastrophic high-K regressions are no longer
catastrophic:

| High-K group | First single-conv ANE result | Optimized chunked result |
| --- | ---: | ---: |
| dual image MLP-out | -30.0s over 28 steps | +0.2s over 28 steps |
| single fused output | -82.3s over 28 steps | +0.2s over 28 steps |

This proves the next optimization target: the connector is not blocked by the
existence of high-K output projections; it is blocked by the quality of the ANE
lowering. Chunking gets those paths to parity. The remaining work is to make
the projection programs resident and then attack attention QK/AV, which is a
larger measured bucket than the projection groups currently proven on ANE.

Boundary pressure:

| Metric | Value |
| --- | ---: |
| image hidden boundary tensor | 24 MiB |
| text hidden boundary tensor | 3 MiB |
| read+write per block boundary | 54 MiB |
| block invocations per 28-step render | 1,596 |
| dense group invocations per render | 10,640 |
| corrected chosen tile invocations per render | 2,777,040 |
| naive per-block boundary traffic | 84.2 GiB/render |

Break-even budget before the ideal `94.0s` saving disappears:

| Overhead unit | Budget |
| --- | ---: |
| per denoise step | 3356 ms |
| per block boundary | 58.9 ms |
| per dense group boundary | 8.83 ms |
| per chosen tile if host-dispatched | 33.8 us |

Conclusion: the opportunity is large enough to justify this work, but the
runtime shape is constrained. A per-tile host-dispatched connector is dead on
arrival. A meaningful connector must keep weights resident, loop over tiles
inside a persistent/resumable ANE program, and expose boundary tensors in a
layout the remaining GPU-side work can consume without CPU copies.

### CPU / AMX

Role: fallback, validation, and small auxiliary work.

Use it for:

- tokenization and scheduler-side work when unavoidable
- correctness checks
- emergency render fallback

Do not expect full FLUX generation on CPU to be competitive.

## Scheduler Policy

Default policy:

1. For `backend=auto`, use the fastest profiled backend for the requested
   `width x height x steps` key when `.fluxd/profile.json` has evidence.
2. Else use `mps` as the compatibility baseline when available.
3. Use `coreml` only for fixed shapes with compiled packages present.
4. Use `ane` only when the compiled package has an ANE validation record.
5. Use `cpu` only when explicitly requested or when no accelerator is present.

The scheduler should track:

- backend availability
- whether weights are loaded
- memory pressure
- average seconds per step by size
- last error per backend
- queue depth

Run socket benchmarks with:

```zsh
flux bench --backends mps,mlx --width 768 --height 768 --steps 8
```

If the Unix socket is already live, `bench` reuses it and does not start a
second worker. If the socket is down, it starts the lightweight queue process
without preloading the model; the submitted benchmark job is what loads the
selected backend.

## Memory Policy

Loading FLUX.1-dev BF16 into a resident worker can consume roughly 32-36 GB of
memory. That is expected for the current MPS Diffusers path.

Use these commands when memory matters:

```zsh
flux warm --preload=false
flux stop
flux render --backend mlx "prompt"
```

Do not use `flux warm` without `--preload=false` unless you intentionally want
the model loaded into memory immediately.

## Implementation Plan

1. Keep the current MPS Diffusers path as baseline.
2. Add a backend field to job records and status responses. Done.
3. Add `flux accel` for capability reporting. Done.
4. Add `FLUX_BACKEND=auto|mps|mlx|coreml|ane|cpu`. Done.
5. Add `--backend` to local render, warm, serve, and remote render. Done.
6. Add MLX execution through `mflux-generate`. Done, needs benchmark.
7. Add benchmarking: same seed, same prompt, same dimensions, same steps. Done.
8. Use benchmark profile for `backend=auto`. Done.
9. Add strict `ane` backend gating through package registry. Done.
10. Add first fixed-shape VAE decoder conversion command. Done.
11. Evaluate Core ML conversion for additional fixed-size components.
12. Prototype a dedicated `ane` adapter starting with one component and require
    Instruments validation before reporting Neural Engine support.

## Non-Goals

- Mixing PyTorch, MLX, and Core ML inside one denoising pass.
- Starting multiple 32 GB-class workers by accident.
- Converting the main checkpoint unless the backend proves useful.
- Claiming ANE support from Core ML installation alone.
