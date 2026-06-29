# WLS Durable Runtime v1

Status: `CANDIDATE_UNVERIFIED`

## Canonical authorities

- `LivingSystem`: identity and cycle orchestration.
- `Database`: durable transaction boundary.
- `GoalRuntime` / `GoalReviewer`: Goal truth.
- `PolicyEngine` and execution guard: Action policy, outcome and provenance.
- `CycleJournal`: cycle phase history and restart reconciliation only.
- `SurvivalSupervisor`: daemon budgets and run terminal state.
- `EvidenceLedger`: evidence-chain authority.
- Existing Learning/Growth modules: episode, Skill and promotion authority.

No plugin may create a second runtime, planner, Goal store, Memory store, policy system, provenance ledger, or acceptance system.

## Cycle state machine

```text
RUNNING
  ├─ interrupted before durable Plan ─> FAILED
  └─ Plan persisted ─> PLAN_PERSISTED
       ├─ approval or unknown side effect ─> RECOVERY_PENDING
       └─ all Actions terminal ─> ACTIONS_TERMINAL
            ├─ normal post-processing ─> SUCCEEDED
            └─ restart reconciliation ─> RECOVERY_RESOLVED ─> RECOVERED
```

Cycle checkpoints are immutable and monotonic:

1. `PLAN_PERSISTED`
2. `ACTIONS_TERMINAL`
3. `RECOVERY_RESOLVED`

The same phase and payload may be recorded again. A changed payload or backward phase transition is corruption.

## Startup order

1. Initialize canonical stores and plugins.
2. Acquire the runtime startup lease.
3. Classify interrupted cycles before generic cleanup.
4. Recover safe Actions; quarantine possible side effects.
5. Recover interrupted Survival runs and stale event reservations.
6. Initialize identity/runtime metadata.
7. Release the startup lease.

## Recovery rules

- A persisted Plan is never flattened into an ordinary failed cycle.
- Safe incomplete Actions may resume through the canonical executor.
- `WAITING_APPROVAL` and `UNKNOWN_SIDE_EFFECT` keep the cycle pending.
- Terminal Actions are reconstructed from durable rows.
- Original Cognition, Memory and Goal attribution are resolved once.
- A missing episodic memory is restored only from attributable fresh execution.
- `REUSED_PRIOR_RESULT` never creates new Goal progress, Memory credit, Skill credit or Learning episodes.

## Action provenance

Allowed values:

- `EXECUTED_CURRENT_ACTION`
- `RECOVERED_DURABLE_ACTION`
- `REUSED_PRIOR_RESULT`
- `NO_OBSERVABLE_OUTCOME`
- `LEGACY_UNATTRIBUTED`

Policy and exact contract matching run before result reuse. A failed operation with possible side effects becomes `UNKNOWN_SIDE_EFFECT` and is never replayed automatically.

## Deferred surfaces

- Provider runtime selection and automatic paid fallback.
- Provider attachment to canonical planning.
- Automatic resolution of unknown side effects.
- Task20 before Task19 owner acceptance.
- Merge/deployment based on queued or missing Actions.

## Forward order

1. Keep PR #25 as the sole Draft candidate.
2. Resume exact-head verification when the owner chooses.
3. Repair only observed failures.
4. Squash into `main` only after owner acceptance.
5. Rebase Task20 on the accepted Task19 head.
6. Run Task20 read-only, opt-in and without feedback into canonical state.

## Claim ceiling

After exact-head verification, WLS may claim bounded durable Plan/Action persistence, restart classification, safe replay prevention, and original-cycle reconciliation. It may not claim production reliability, unrestricted autonomy, long-duration stability, Provider safety, or Task20 correctness.
