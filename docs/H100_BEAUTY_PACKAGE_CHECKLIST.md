# H100 Beauty Package — Delivery Checklist

This is the implementation checklist for the GMan deployment. It is
deliberately concrete so an agent can execute it without reinterpreting the
topology.

## Hardware contract

- [ ] Single-card profile: one H100 80 GiB.
- [ ] Remote-witness profile: studio H100 plus a clean Qwen machine.
- [ ] Distributed-atelier profile: studio, Qwen, and Gemma machines.
- [ ] Studio: FLUX.1-dev BF16, 35 GiB; Pixtral critic, 8.8 GiB; DINOv2 + SigLIP, 3 GiB.
- [ ] Pixtral stays beside FLUX in every topology.
- [ ] Qwen is remote in both expanded profiles; Gemma is remote in all H100 profiles.
- [ ] Every tenant uses `tensor_parallel=1`.
- [ ] First GMan test provisions GPU 0 only; GPU 1 is not touched until GPU 0 passes.

## Artifacts and runtime

- [ ] Use the verified R2 vLLM `sm90` wheel:
      `r2://governor/wheels/vllm/65b7662d3fcb773afaf751ab29ac6960a0cf011d/sm90/`.
- [ ] Wheel: `vllm-0.26.1rc1.dev602+g65b7662d3.d20260820.cu129-cp312-cp312-linux_x86_64.whl`.
- [ ] Pin CUDA/Python compatibility before installing.
- [ ] Keep FLUX BF16; do not quantize the generator.
- [ ] Use `TRITON_ATTN` on H100; no speculative decoding.
- [ ] Governor is local only in the two-H100 profile; remote in the single-H100 profile.

## Deployment package

- [ ] Authoritative `h100`, `h100-remote-witness`, and
      `h100-distributed-atelier` profiles in the continuum/config.
- [ ] One deploy command with `--profile`, `--dry-run`, `--status`, and `--health`.
- [ ] Explicit GPU, port, socket, state, output, and model paths.
- [ ] Idempotent service installation and restart behavior.
- [ ] No duplicated two-lane generation topology.
- [ ] Remote Qwen and Gemma endpoints are independently configurable and gated.
- [ ] No implicit tensor parallelism or port shifting as a substitute for a profile.

## Nonstop pipeline

- [ ] Resident FLUX worker.
- [ ] Beauty protocol stream / queue.
- [ ] Qwen witness, Pixtral critic, Gemma governor, and sensory gates.
- [ ] Jury evaluator and durable verdict/audit ledger.
- [ ] R2 output/ledger synchronization.
- [ ] Worker, app, stream, and pipeline sentinels with bounded repair.
- [ ] Restart-safe state and no false success when a judge is unreachable.

## Operator surface

- [ ] Tea/FLUX app starts as the single control surface.
- [ ] Controls expose start/stop, prompt, collection/branch, jury calibration,
      status, and health.
- [ ] App displays the active hardware profile and exact tenant placement.
- [ ] 45 prompts are available by category and preserved in manifests.
- [ ] CLI variants expose the five beauty categories.

## GMan verification

- [ ] Sync the committed package to GMan.
- [ ] Hydrate the model and the R2 `sm90` wheel.
- [ ] Run hardware, import, wheel, and VRAM checks.
- [ ] Start GPU 0 only.
- [ ] Verify FLUX and Gemma health plus memory ownership.
- [ ] Start GPU 1 only after GPU 0 passes.
- [ ] Verify the complete pipeline and app controls.
- [ ] Run the same checks a second time after restart.
