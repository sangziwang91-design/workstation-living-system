# WLS 100-Round Roadmap

Each round is one evidence-gated vertical slice, not a conversation count.

## Rounds 001–010 — Truthful engineering baseline

1. Reproducible source, release, tests, claims, and capability inventory.
2. Sensor contract, health state, backoff, and failure isolation.
3. Durable event bus invariants and event-storm behavior.
4. Database migration and backup/restore protocol.
5. Evidence anchoring, export, and truncation detection limits.
6. Approval expiry, cancellation, recovery, and operator workflow.
7. Planner schema fuzzing and hostile-output rejection.
8. Tool sandbox boundary and command-policy verification.
9. Long-run performance, log rotation, and resource budgets.
10. Frozen baseline release `0.2.0a1` or explicit failure decision.

## Rounds 011–020 — Real perception

Deliver complete observation loops for filesystem, process/service, and Git state. Add provenance, deduplication, backpressure, stale-state handling, and synthetic fault injection. No new sensor counts unless an existing sensor closes an end-to-end loop.

## Rounds 021–030 — Corrigible world model

Build typed entities, temporal facts, relationships, source reliability, contradictions, predictions, prediction error, retraction, and uncertainty propagation. Compare against a plain event-queue baseline.

## Rounds 031–040 — Memory that changes outcomes

Separate episodic, semantic, failure, procedural, relationship, and self-model stores. Require retrieval attribution and memory-on/memory-off controlled experiments. Delete memory paths that do not improve measured behavior.

## Rounds 041–050 — Regulation and finite attention

Calibrate resource, safety, curiosity, frustration, confidence, and fatigue as control variables. Each retained variable must change policy and improve a declared metric without increasing safety failures.

## Rounds 051–060 — Governed action body

Add verified read tools first, then reversible writes in isolated workspaces, service control, patch generation, and rollback. Every action carries purpose, prediction, risk, acceptance, evidence, and actual result.

## Rounds 061–070 — Learning and skill growth

Close the loop from repeated failure to hypothesis, isolated experiment, comparison, versioned skill, promotion, and rollback. At least three promoted skills must originate from runtime evidence rather than being prewritten.

## Rounds 071–080 — Self-model and relationship continuity

Calibrate capability claims from observed success rates, track commitments and permissions, model uncertainty, and distinguish stable user values from transient commands. Self-description must never outrun evidence.

## Rounds 081–090 — Long-horizon resilience and sleep

Run multi-day tests, crash injection, event floods, provider failure, malicious input, prompt injection, database growth, memory compaction, and low-activity consolidation. No high-risk external action occurs during sleep.

## Rounds 091–100 — Ablation, external validation, and release decision

Compare memory on/off, world model vs queue, regulation on/off, frozen vs learning policy, and self-model on/off. Perform Windows integration, security review, upgrade/rollback, and decide whether `1.0` evidence exists. The program may end in `INVALIDATED`; a forced success is prohibited.
