# Beauty Pipeline — Distributed H100 Topology

Pixtral is part of the studio eye and always stays beside FLUX. Qwen supplies
the builder-observation pass, Pixtral is the independent critic, and Gemma
synthesizes their evidence. The operator—not a model—owns the final verdict.

~~~mermaid
flowchart LR
    APP[Tea / FLUX App<br/>HTTP :7861]

    subgraph S[Studio H100]
        F[FLUX.1-dev BF16<br/>UDS flux-gpu0.sock]
        P[Pixtral critic<br/>HTTP :8002]
        D[DINOv2-Giant + SigLIP<br/>in-process gates]
        K[FLUX Kontext<br/>optional or resident by profile]
    end

    subgraph Q[Clean Qwen machine]
        W[Qwen3.8-27B witness<br/>OpenAI-compatible HTTP]
    end

    subgraph G[Dedicated Gemma machine]
        GOV[Gemma 4 31B synthesist<br/>OpenAI-compatible HTTP]
    end

    O[Operator eye-gate]
    T[(taste-log.jsonl<br/>anchors + laws)]

    APP --> F
    F --> D
    D -->|passing image + manifest| P
    D -->|passing image + manifest| W
    P -->|palette + medium evidence| GOV
    W -->|structure + anatomy evidence| GOV
    GOV -->|ranked recommendation| O
    O -->|crown / kill / note| T
    T -->|recalibrated taste context| APP
    O -->|approved next mutation| APP
    GOV -.->|approved edit brief| K
~~~

## Profiles

| Profile | Studio machine | Qwen machine | Gemma machine |
|---|---|---|---|
| h100 | FLUX + Qwen + Pixtral + gates | — | remote governor |
| h100-remote-witness | FLUX + Pixtral + gates; Kontext optional | FP8 witness | remote governor |
| h100-distributed-atelier | FLUX + Pixtral + gates + Kontext | dedicated BF16 witness | dedicated FP8 governor |

Every tenant remains tensor_parallel=1. These are service-placement profiles,
not tensor-parallel layouts.

## Connection contract

| Component | Placement | Connection | Role |
|---|---|---|---|
| FLUX worker | Studio | Unix socket .fluxd/flux-gpu0.sock | Candidate generation |
| Pixtral critic | Studio | Local HTTP :8002 | Palette, medium, and aesthetic evidence |
| DINO/SigLIP | Studio | In-process | Fast novelty, alignment, and sensory gates |
| Qwen witness | Remote profiles: Qwen machine | MOJ_VISUAL_WITNESS_URL | Spatial, anatomy, line, and defect evidence |
| Gemma governor | Gemma machine | MOJ_GOVERNOR_URL or GOVERNOR_BASE_URL | Evidence synthesis and prompt evolution; never the final gate |
| Tea/FLUX app | Studio/app host | HTTP :7861 | Operator control and live status |

The evaluator fans the passing image out to Qwen and Pixtral independently.
Their reports converge at Gemma; neither judge's report is allowed to overwrite
the other before synthesis. Machine tiers enter `eye-gate-candidates.jsonl` and
do not enter the masterpiece vault until the operator crowns them.

## Invariants

- Pixtral is local to the FLUX studio in every Beauty profile.
- Moving Qwen remote must remove its local VRAM reservation and suppress its
  local vLLM container.
- Remote endpoints must expose OpenAI-compatible /v1/models and
  /v1/chat/completions.
- Cross-machine traffic uses HTTP and authentication. Unix sockets never cross
  machine boundaries.
- A failed witness, critic, or governor health gate is not a successful Beauty
  deployment.
- Operator words are persisted verbatim. Machine scores rank the full slate but
  never hide or automatically kill a candidate.
