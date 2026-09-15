# EGRL — Operator Eye-Gate Ralph Loop

A protocol for iterative production where machine judgment is necessary but not
sufficient, and a human's judgment is the only ground truth available.

**Registered:** 2026-06-12.
**Empirical basis:** five generations of Blender loop art produced in two days by an agent
swarm — 40+ agents, roughly 4.5M tokens, pipeline of recon → art direction → builder loops
→ critic → operator eye-gate → finale. The generation-1 winner was scored 8/10 by the
operator; generation-5 winners reached 9.5/10.

This document is self-contained. Where it draws on an idea from elsewhere, the idea is
explained inline rather than referenced by name alone.

---

## Thesis

In creative production, calibrated machine judgment is necessary but not sufficient: the
operator's eye is the only ground-truth oracle for taste.

Structure iterative production as three nested judgment gates — builder self-critique via
vision, independent critic ranking against calibrated prior winners, and an operator
eye-gate whose verdict overrides the critic *and* is persisted as taste data that
recalibrates the next generation.

The loop does not merely select winners. It learns the operator's taste function.

---

## The three gates

```
 [BUILDER LOOP]                [CRITIC]                  [OPERATOR EYE-GATE]
  build -> render ->      rank candidates vs         human verdict on survivors
  READ own output ->  ->  calibrated prior      ->   OVERRIDES critic score  ->  persist
  self-critique vs        winners (anchored           ("SICK" / kill / note)      verdict
  explicit design laws    scale, not vibes)                                     as taste data
        ^                                                                            |
        +---------------------- recalibrated design laws + priors <-----------------+
```

### Gate 1 — builder vision self-critique (intra-iteration)

- Builder agents iterate autonomously: build → render → assess → revise.
- **Mandatory: the builder reads its own rendered output with vision, every iteration.**
  No submission of unviewed work. "It compiled and rendered" is evidence of execution, not
  evidence of quality.
- Self-critique is scored against **explicit design laws** — written, numbered, versioned
  (composition laws, palette laws, camera doctrine, whatever the domain needs) — never
  against the builder's private aesthetic. Laws are decision premises: they shape judgment
  without micromanaging construction.
- A builder may only submit a candidate that passes its own design-law checklist, with the
  checklist results attached as evidence.

**Evidence.** One ten-concept batch was built without proper renders and without viewing
the output; **9 of 10 failed**. The autopsy assigned the failure to three compounding
causes, of which "written once, checked (if at all) with a fast preview renderer,
submitted" was dominant — the fast preview misreports subsurface scattering, emission
balance, and transmission, which is exactly what the judged render depends on. The same
nine concepts, rebuilt under a mandatory "render properly and read every preview" rule,
went **9/9**.

**Generalisation beyond images.** Whatever the artifact's ground truth is — a screenshot,
a rendered page, a chart, a diff, an audio waveform — the builder must *observe it*, not
merely observe that the build exited 0.

### Gate 2 — independent critic, calibrated priors (inter-candidate)

- A critic agent **that built nothing** ranks all submitted candidates. Structural
  separation: no agent judges its own output.
- The critic scores against **calibrated prior winners** — an anchor set of previously
  operator-approved pieces with their verdicts attached. Scores are comparative
  ("better/worse than anchor X on axis Y"), never absolute private vibes.
- The critic views every candidate before ranking. Ranking from descriptions, filenames,
  or builder self-reports is forbidden.
- Output: a ranked slate plus, per candidate, a **typed evidence trace** — which design
  laws were satisfied, which violated, which anchors beaten, which lost to. Typed evidence
  means the reason for a score is a citable, checkable claim rather than an impression.

### Gate 3 — operator eye-gate (ground truth)

- The operator views the critic's slate. The operator's verdict is **final** and
  **overrides the critic in both directions**: a critic 9 can be killed; a critic 6.5 can
  be crowned.
- Every verdict — especially override verdicts — is **persisted** as taste data:
  candidate, critic score, operator verdict, the operator's words verbatim, and the delta.
- Operator notes are first-class doctrine inputs. A casual observation ("the camera should
  pass through the centroid") becomes a candidate design law for the next generation,
  marked **provisional** until validated.

---

## The recalibration loop

This is what makes EGRL a learning system rather than a pipeline. After each generation:

1. **Anchor update** — operator-approved pieces enter the critic's prior-winner anchor set
   with their verdicts.
2. **Critic-delta audit** — every operator override is a measured critic calibration
   error. Recurring deltas in one direction (the critic systematically underrating a
   register) force a critic-prompt recalibration that cites the override evidence.
3. **Law promotion** — provisional laws distilled from operator notes are tested in the
   next generation. If the eye-gate validates the outputs those laws shaped, they are
   promoted into the numbered design laws.
4. **Register expansion** — an operator override *in favour of* a low-scored candidate
   defines a new approved register: a taste territory the critic did not know existed. The
   register is named, exemplified, and added to both the design laws and the anchors.

---

## Core principles

1. **Vision before submission.** Every builder reads its own output. Unviewed output is
   undefined output.
2. **Laws, not vibes.** All machine judgment — builder and critic — scores against
   explicit written laws and anchored priors. Any disagreement must be expressible as
   "violates law N" or "loses to anchor X".
3. **The critic proposes; the operator disposes.** Critic ranking economises operator
   attention. It never substitutes for the eye-gate, and it never hides a candidate.
4. **Overrides are the gold.** An operator-critic disagreement is the highest-information
   event in the system. It is never discarded — it is the training datum.
5. **Taste data compounds.** Persisted verdicts make generation N+1's critic measurably
   closer to the operator's eye than generation N's. The asset being built is the
   calibrated taste function, not any single piece.
6. **Doctrine from operator language.** Operator notes are captured verbatim and distilled
   into provisional laws — never paraphrased into blandness before validation.

---

## Implementation phases

- **Phase 0 — Charter.** Write and version the design-laws file. Assemble the anchor set
  (prior winners with their verdicts). Define the output format builders must produce for
  vision review.
- **Phase 1 — Parallel builder loops.** N heterogeneous builders iterate. Each iteration
  is build → render → vision read → design-law checklist → revise or submit. Bounded
  budget: minimum 3, maximum 6 iterations; submit at the aspiration level rather than
  polishing past it. Past roughly six iterations the problem is usually the concept, not
  the execution — one builder in the reference campaign burned 8 iterations on materials
  physics and still lost on composition.
- **Phase 2 — Critic pass.** The independent critic ranks submissions against anchors and
  emits the slate plus typed evidence.
- **Phase 3 — Eye-gate.** The operator reviews the slate, highest-ranked first, with **all
  survivors viewable**. Verdicts captured verbatim.
- **Phase 4 — Recalibration.** Persist verdicts, update anchors, audit critic deltas,
  promote or demote laws, name new registers. Feed forward into the next generation's
  Phase 0.

---

## Failure modes

| Failure | Symptom | Countermeasure |
|---|---|---|
| Blind submission | Builder ships output it never viewed | Hard gate: checklist evidence required with submission |
| Critic absolutism | Scores drift to a private scale, anchors ignored | Comparative scoring format mandatory; anchor citations required |
| Verdict evaporation | Operator says "SICK", nothing is written down | Persistence is a pipeline step, not a habit |
| Taste laundering | Operator notes paraphrased into generic principles | Verbatim capture; distillation marked provisional |
| Critic-as-gate | Low-critic candidates hidden from the operator | Operator can always view the full slate; kill decisions are operator-only |
| Law ossification | Old laws block a new register the operator loves | Override-in-favour events trigger a register-expansion review |

---

## Metrics

- **Critic–Operator Agreement Rate** — rising across generations. Target: top-1 agreement
  above 70% by generation 3.
- **Override Yield** — fraction of operator overrides that produce a promoted law or a
  named register. Target above 50%. Overrides should be mined, not absorbed.
- **Blind Submission Rate** — exactly 0.
- **Provisional Law Validation Rate** — above 60% of distilled operator notes survive the
  next generation's eye-gate.
- **Anchor Freshness** — every generation adds at least one anchor.

---

## Evidence

- **abyssal-mantle.** The critic scored it 6.5 on composition grounds. The operator's
  verdict was "SICK". The override was persisted, the "melt" register was named from it,
  and the piece became the gold standard for the following generation. A critic-as-gate
  design would have killed it unseen.
- **Camera doctrine.** The operator's evening notes on camera movement were captured
  verbatim and distilled into provisional camera laws. The next morning's generation, built
  under those laws, passed the eye-gate — the laws were promoted to doctrine.
- **Ten-pack → Proper Nine.** 9 of 10 failed when built blind; 9 of 9 succeeded when
  rebuilt under a mandatory observation rule.
- **Generation trend.** Generation-1 winner scored 8/10 by the operator; generation-5
  winners reached 9.5/10, across 5 generations, 40+ agents, ~4.5M tokens, 2 days.

---

## Ideas this composes with

EGRL sits alongside several other patterns. Each is summarised here so it is usable
without any external registry.

- **Multi-critic tribunal.** When one critic is not enough, run several with genuinely
  different framings and treat their *disagreement* as the signal, not their consensus.
  EGRL's Gate 2 is a tribunal with the operator as supreme court. Compose when the cost of
  a bad ranking exceeds the cost of a second critic.
- **Typed evidence with reliability weighting.** Score claims carry a type and a source, so
  a judgment can be audited rather than trusted. Contributes Gate 2's evidence trace, and
  the idea of gating on the critic's own confidence before spending operator attention on
  a slate the critic is not sure about.
- **Satisficing with an aspiration level.** Set a "good enough" bar in advance and stop
  the moment it is met, rather than optimising indefinitely. Contributes the min-3/max-6
  iteration stop rule inside each builder loop: submit when the design-law checklist
  passes; do not polish past the aspiration level.
- **Memory as re-arousable constellations.** Past successes are stored as whole
  configurations — which builder, which register, which laws — and reactivated together
  when a similar brief appears. Contributes the reading of persisted taste data as a
  reusable memory rather than a log.
- **Censor library from failures.** Killed candidates and the reasons they were killed are
  kept as explicit negative knowledge — what *not* to build again — rather than being
  discarded. Contributes the value of persisting kills, not just crowns.

---

## How the agents in this pack implement each gate

| Gate / step | Agent | File |
|---|---|---|
| Task selection (pre-gate) | `ralph-task-picker` | [`../agents/ralph-task-picker.md`](../agents/ralph-task-picker.md) |
| Build + Gate 1 observation and design-law scoring | `ralph-orchestrator` | [`../agents/ralph-orchestrator.md`](../agents/ralph-orchestrator.md) |
| Automated gates (proves it ran, not that it is good) | `ralph-quality-checker` | [`../agents/ralph-quality-checker.md`](../agents/ralph-quality-checker.md) |
| Gate 2 — independent critic, anchored comparative ranking | `ralph-critic` | [`../agents/ralph-critic.md`](../agents/ralph-critic.md) |
| Gate 3 — operator eye-gate, verbatim capture, persistence | `ralph-eyegate-recorder` | [`../agents/ralph-eyegate-recorder.md`](../agents/ralph-eyegate-recorder.md) |
| Recalibration (all four steps) | `ralph-eyegate-recorder` | [`../agents/ralph-eyegate-recorder.md`](../agents/ralph-eyegate-recorder.md) |

Lifecycle skills that drive them: [`../skills/ralph-setup/`](../skills/ralph-setup/)
(Phase 0 charter), [`../skills/ralph-loop/`](../skills/ralph-loop/) (Phase 1),
[`../skills/ralph-eyegate/`](../skills/ralph-eyegate/) (Phases 3–4),
[`../skills/ralph-status/`](../skills/ralph-status/) and
[`../skills/ralph-cancel/`](../skills/ralph-cancel/) (supervision).

---

## Limitations

- **The eye-gate requires a human.** This is deliberate. Gates 1 and 2 run unattended;
  Gate 3 batches operator attention rather than eliminating it. EGRL is not a fully
  unattended system.
- **It pays off across generations.** For a one-shot task the taste-compounding machinery
  is overhead.
- **Vision self-critique costs tokens.** The reference campaign spent roughly 4.5M tokens
  over two days.
- **Proven on visual output.** Gate 1 and the automated quality gates are proven on code.
  The three-gate judgment stack is proven on visual art; its application to non-visual
  domains is argued, not yet demonstrated.
