# WLS RSI Pilot: Five Candidate Gates

Status: **candidate-only integration**, not an autonomous self-improving agent.
Canonical runtime is still `wls.runtime.LivingSystem`; this component is
available as `runtime.rsi_pilot` and uses the **existing** WLS SQLite database
and HMAC EvidenceLedger. No new identity, memory, scheduler, version authority
or unrestricted write permission is created.

## Gates installed

1. `experiment_decision`: reject nonfinite values, missing baseline gates,
   mismatched evaluator digests, zero gain and invalid budget counters.
2. `loop_control`: reject nonfinite observations and make owner/budget stop
   sticky so extra calls cannot silently consume turns.
3. `coding_workers`: missing or contradictory text test markers are **not**
   considered passing. A marker is an untrusted hint, never independent proof.
4. `rsi_evolution`: owner-started, finite candidate generations, two or more
   independent branches, champion inheritance, immutable policy digest and
   atomic WLS state + evidence updates at phase transitions.
5. Focused hosted Python 3.11/3.13 CI: replay generations, fail closed on
   invalid results, reject changed contracts, block interrupted generations
   without replay, verify evidence-chain integrity.

## Invoking the candidate protocol

After creating `LivingSystem` from its normal config, use
`runtime.rsi_pilot.start(...)` with a frozen `ExperimentPolicy`, a concrete
baseline artifact ID and trusted `MetricResult`. Then call
`runtime.rsi_pilot.run_bounded(...)` with explicit `propose` and `evaluate`
callbacks.

The proposer receives `(champion_artifact_id, generation, branch)`. It must
create a **new candidate** in a disposable worktree, never mutate the
champion, live runtime, evaluator, sealed tasks, or policy. The independent
evaluator receives only the candidate ID and returns measured `MetricResult`.
The champion ID is passed to the next generation; the proposer must explicitly
load the corresponding artifact and its generation strategy.

This is deliberately NOT an automatic Codex execution entrypoint:
`coding_workers.CodexWorker` currently returns text-based test hints and is
not a sufficient independent scoring adapter. Caller-supplied callbacks must
enforce actual sandboxing, deadlines, test-set separation and model cost limits
before any real coding experiment. The package does not mark candidate
selection as live WLS promotion.

## Checkpoint and recovery

`READY` means the next generation may run. `IN_FLIGHT` is persisted
**before** any external callback. If the process dies, reopening the same
`run_id` will refuse to replay it. A callback exception writes `BLOCKED`,
keeps already measured candidate evidence, and likewise refuses replay.
There is no automatic resume of unknown side effects. Reconciliation requires
a separate owner-authorized procedure; do not reset status by editing SQLite.

All `rsi_*` evidence events use the same HMAC ledger as existing WLS records.
This proves origin and mutation detection within WLS, **not** that self-reported
scores correspond to real independent tests.

## Proof boundary and next acceptance

- **This PR:** deterministic three-generation/six-candidate fixture proof,
  fail-closed safety cases and canonical runtime integration.
- **Not yet proven:** live model/provider calls, separate hidden tasks,
  statistically valid gain, real sandbox containment, meter-verified spend,
  working independent scorer, owner-host long-term stability, automated
  reconcile-and-resume, or recursive improvement of the improver itself.
- **Next:** implement one disposable Codex adapter plus a frozen independent
  evaluator, measure performance against a fixed-improver control and require
  exact-head Windows verification before allowing real unattended work.
