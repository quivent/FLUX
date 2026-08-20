# Influx Vision · Mixture of Judges (MoJ) Autonomous Evaluation Pipeline

## 1. System Topology & VRAM Distribution

```
Total VRAM: 140.4 GiB (NVIDIA H200 SXM 141GB HBM3e)
Allocated: 132.7 GiB | Safety Headroom: ~7.7 GiB

┌──────────────────────────────────────────────────────────────────────────────────┐
│ Governor LLM Brain (Gemma 4 31B FP8)                0.40 Util    [ 56.0 GiB ]    │
├──────────────────────────────────────────────────────────────────────────────────┤
│ FLUX.1-dev Resident Worker (BF16 Flow Matching)     UDS Daemon   [ 35.0 GiB ]    │
├──────────────────────────────────────────────────────────────────────────────────┤
│ Multimodal Feature Decoder (Gemma 4 12B)            0.10 Util    [ 14.0 GiB ]    │
├──────────────────────────────────────────────────────────────────────────────────┤
│ Artistic Judge & Colorist (Pixtral 12B)             0.10 Util    [ 14.0 GiB ]    │
├──────────────────────────────────────────────────────────────────────────────────┤
│ Structural Inspector (Qwen3-VL 8B / Qwen2.5-VL 7B)  0.08 Util    [ 11.2 GiB ]    │
├──────────────────────────────────────────────────────────────────────────────────┤
│ Fast Sensory Gates (DINOv2-Giant + SigLIP Head)     Dedicated    [  2.5 GiB ]    │
├──────────────────────────────────────────────────────────────────────────────────┤
│ Dynamic CUDA Free Buffer                            Unallocated  [  7.7 GiB ]    │
└──────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Real-Time Feedback & Evolution Pipeline

```mermaid
flowchart TD
    A[1. Perpetual Sieve Feeder] -->|Dispatch 4-Vector Prompt| B[2. FLUX.1-dev Resident Worker]
    B -->|Settled 1024x1024 Frame ~6.3s| C{Fast Sensory Triage <10ms}
    
    C -->|SigLIP Aesthetic Score| D1[Instant Triage Filter]
    C -->|DINOv2 Semantic Novelty| D2[Duplicate Rejection Gate]
    
    D1 & D2 -->|Passes Quality Floor| E[Multimodal Jury Chamber]
    
    subgraph Jury Chamber
        J1[Qwen3-VL / Qwen2.5-VL 8B: Structural Defects & Anatomy]
        J2[Pixtral-12B: Lighting, Medium Authenticity & Palette]
    end
    
    E --> J1
    E --> J2
    
    J1 & J2 -->|Multi-Perspective Critiques| F[3. Gemma 4 12B Decoder]
    F -->|Synthesized Jury Scorecard| G[4. Governor LLM 31B Advisor]
    
    G -->|Composite >= 90.0| H[🏆 Masterpiece Vault / Rosarium]
    G -->|Composite < 90.0| I[Live Stream Gallery]
    
    G -->|Extract Winning Aesthetic Traits & Prune Flaws| A
```

---

## 3. Evaluation Dimensions & Scoring Formula

1. **Harmony Score ($S_H$ — 35%)**:
   * Evaluated by **Pixtral-12B** and **SigLIP**.
   * Measures tonal balance, palette restraint, atmospheric depth, and medium authenticity.
2. **Structural Coherence ($S_S$ — 35%)**:
   * Evaluated by **Qwen3-VL / Qwen2.5-VL**.
   * Identifies micro-defects, anatomical deformations, broken geometry, and boundary artifacts.
3. **Semantic Fidelity ($S_P$ — 30%)**:
   * Evaluated by **DINOv2-Giant** and **Governor 31B**.
   * Verifies that all requested subjects, lighting directions, and materials are faithfully realized.

$$\text{Composite Score } S = 0.35 S_H + 0.35 S_S + 0.30 S_P$$
