# EVOLUTION-TARGET-003 — Causal Memory Retrieval and Measurable Learning Advantage

## Status

This target extends the canonical `LivingSystem`. It does not create a second runtime, planner identity, or memory authority.

## Problem

Before ET003, `MemoryStore.retrieve()` primarily ranked active memories by token overlap, importance, and confidence. ET002 could show that one promoted procedural memory changed one selected hypothesis, but that did not establish a repeatable task advantage, contradiction handling, or restart-safe suppression.

## Implemented path

```text
persisted MemoryItem
  -> causal_memory_index
  -> project/entity/time/failure/causal/applicability filtering
  -> selected and suppressed memory identifiers
  -> bounded CognitiveEngine hypothesis competition
  -> memory_decision_attributions
  -> actual action and prediction outcome
  -> ACTIVE | WEAKENED | REFUTED | EXPIRED | SUPERSEDED
  -> restart-safe retrieval or suppression
```

`MemoryStore` remains the memory authority. `CausalMemoryIndex` is an index and validity layer over the existing `memories` table, not a replacement store.

## Retrieval dimensions

The index can use project and context identifiers, entity identifiers and names, time validity windows, failure signatures, causal-hypothesis and prediction identifiers, linked skills, outcome type, applicability conditions, and validity/refutation state.

Lexical similarity remains a low-weight fallback. It is not sufficient by itself when a structured query context is available.

## Decision attribution

Every local cognitive trace can be paired with a durable attribution record containing `selected_memory_ids`, `suppressed_memory_ids`, `counterfactual_without_memory`, `memory_changed_decision`, `memory_delta`, `decision_trace_id`, `causal_reason`, and the observed outcome with resulting memory-state transitions.

Only memories attributed to a changed decision receive outcome updates.

## Refutation and expiry

State transitions preserve evidence:

- `ACTIVE -> WEAKENED` after an attributed failure;
- `WEAKENED -> REFUTED` after repeated attributed failure;
- `ACTIVE/WEAKENED -> EXPIRED` when the validity window elapses;
- `ACTIVE/WEAKENED -> SUPERSEDED` when a linked skill or candidate is rolled back;
- `WEAKENED -> ACTIVE` after sufficient later success.

`REFUTED`, `EXPIRED`, and `SUPERSEDED` memories are suppressed. A refuted memory is not silently reactivated.

## Frozen ablation

The verifier creates two isolated runtimes with the same memory record and the same semantic event sequence:

- A: `memory_mode=enabled`;
- B: `memory_mode=disabled`.

The sequence includes repeated instances of a known failure mechanism, a restart, and an applicability-mismatched control task. The report compares success rate, failure recurrence, wrong-tool rate, prediction confirmation, decision stability, task completion, and regressions.

A separate probe supplies repeated counterevidence to a misleading memory, verifies `WEAKENED -> REFUTED`, restarts the runtime, and confirms that the memory stays suppressed while the unmodified bounded action succeeds.

## Verification

```bash
python -m compileall -q source/src source/tests source/scripts
python -m pytest source/tests -q
python source/scripts/verify_evolution_target_001.py
python source/scripts/verify_evolution_target_002.py
python source/scripts/verify_evolution_target_003.py
python -m ruff check source/src source/tests source/scripts
python -m mypy source/src/wls source/tests source/scripts --ignore-missing-imports
python -m bandit -q -r source/src/wls source/scripts
python -m build source
```

## Claim boundary

A passing ET003 report supports only this statement:

> WLS has a locally verified causal-memory retrieval and memory-advantage evaluation mechanism. In a controlled frozen ablation, an attributable memory changes a bounded decision and improves the specified task outcome; repeated counterevidence weakens and refutes a memory, and valid or refuted state persists across restart.

It does not prove production-host advantage, multi-month adaptation, general intelligence, consciousness, subjective emotion, or unrestricted self-rewrite.
