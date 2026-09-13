# Beauty

Beauty is the reduced operator-facing surface for the FLUX Beauty Protocol.
It shares Tea's live FLUX server, model workers, output archive, jury state,
and event streams, but exposes only five rooms:

- `/gallery/` — the live image stream
- `/collections` — named, isolated output walls
- `/protocol` — the governing Beauty Protocol and live topology
- `/jury` — current verdicts and juror state
- `/control` — owner controls for generation, pace, and jury posture

The control loop is intentionally latency-first: one resident FLUX worker,
one 512×512 frame at 18 steps, immediate publication, then one timeout-bounded
advisor. Pixtral is preferred because it sees the image; Qwen is the fallback
director and Gemma the text-only fallback. Dead advisors cool down for 30
seconds and never stall every frame. State is exposed at
`/api/beauty/pipeline` and written to `.fluxd/protocol_stream_gpu3.json`.

Run it from the repository root:

```sh
make beauty-check
make beauty-dev
```

The development listener is `http://127.0.0.1:7863`. Public deployments that
expose Controls must use the authenticated `flux beauty serve` posture.
