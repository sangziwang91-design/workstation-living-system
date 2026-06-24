# EVOLUTION-TARGET-004 — Persistent Goal Continuity

## Result boundary

ET004 extends the existing `GoalStore` and canonical `LivingSystem`. It does not create a second planner, brain, runtime, or state authority.

The implemented vertical slice provides:

- durable goal lifecycle states and structured goal metadata;
- bounded dependency-ordered decomposition;
- explicit goal debt for interruption and failed actions;
- periodic and sleep-time goal reconciliation;
- goal-to-cognition decision attribution with a goal-free counterfactual;
- interruption recovery and restart continuity;
- a controlled `goal_mode=enabled` versus `goal_mode=disabled` comparison;
- completion archival that removes finished work from active attention while preserving evidence.

## Canonical data path

```text
GoalStore
  → GoalRuntime.prepare_cycle
  → dependency-satisfied actionable goals
  → bounded CognitiveEngine candidates
  → governed ActionSpec with goal_id
  → real tool outcome
  → GoalReviewer / GoalDebtLedger
  → parent progress aggregation
  → sleep review and archive
```

`GoalRuntime` coordinates the lifecycle but does not replace `GoalStore`. Goal-disabled mode removes goals from cognition without deleting or mutating the durable goal records, allowing a frozen comparison on the same event structure.

## Controlled evidence

The verifier constructs one parent goal with four ordered child tasks. It introduces an unrelated owner event before goal work, restarts the runtime after partial progress, and runs an equivalent goal-disabled arm.

Required outcomes:

| Measure | Goal enabled | Goal disabled |
|---|---:|---:|
| Completed children | 4 / 4 | 0 / 4 |
| Parent progress | 1.0 | 0.0 |
| Task completion | true | false |
| Interruption recovery | true | false |
| Wrong governed goal action rate | 0.0 | 0.0 |
| Progress preserved across restart | true | n/a |

The local report is written to:

`source/verification/EVOLUTION_TARGET_004_LOCAL_20260624.json`

## Verification commands

```bash
python -m pytest source/tests -q
python source/scripts/verify_evolution_target_001.py
python source/scripts/verify_evolution_target_002.py
python source/scripts/verify_evolution_target_003.py
python source/scripts/verify_evolution_target_004.py \
  --output source/verification/EVOLUTION_TARGET_004_LOCAL_20260624.json
python -m ruff check source/src source/tests source/scripts
python -m mypy source/src/wls source/tests source/scripts --ignore-missing-imports
python -m bandit -q -r source/src/wls source/scripts
python -m build source
```

## Claim ceiling

This verifies controlled local and CI behavior only. It does **not** prove production-host weeks-or-months autonomy, unrestricted long-horizon planning, free will, genuine motivation, AGI, consciousness, subjective emotion, or unrestricted self-rewrite.

The remaining proof is genuine owner-host longitudinal use with preserved comparison policy and resource/fault testing.
