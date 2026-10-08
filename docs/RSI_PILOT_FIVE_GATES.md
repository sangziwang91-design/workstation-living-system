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


## Follow-up: one hundred measured harness generations

The owner requested 100 iterations as an RSI evaluation. This is interpreted
**only for this proof** as 100 generations of *actual artifact production,
scoring, selection, persistence and inheritance*. These are not 100 source
revisions of WLS and must not be represented as 100 autonomous AI improvements.

`python source/scripts/verify_rsi_100_generations.py --report rsi-100-proof.json`
runs a deterministic reference task on real JSON candidate artifacts in a
disposable directory. It starts with zero known answers. Each generation
produces two new separately hashed artifacts: a no-gain branch and a branch
which adds exactly one correct case to its parent's artifact. The objective
scorer checks content of the artifacts, not candidate names, and independently
reopens the persisted state and HMAC ledger after all 100 generations.

| Generations | Expected verified coverage |
|---|---|
| 001–010 | 10/100 |
| 011–020 | 20/100 |
| 021–030 | 30/100 |
| 031–040 | 40/100 |
| 041–050 | 50/100 |
| 051–060 | 60/100 |
| 061–070 | 70/100 |
| 071–080 | 80/100 |
| 081–090 | 90/100 |
| 091–100 | 100/100 |

**Acceptance**: 100 different parent-to-champion transitions, 200 evaluated
candidate artifacts, 201 distinct candidate/baseline IDs, 401 HMAC-backed
receipts, policy/evaluator digest continuity, artifact byte-digest continuity,
100/100 fixture cases and verification on a reopened database. The CI workflow
exports the full 100-row lineage JSON. It must pass on Python 3.11 and 3.13.

The experiment uses a scripted deterministic proposer which already knows
how to compute the next correct case. Therefore the increase from 0 to 100
is **designed and expected by the test author**; it is a reliable demonstration
of long-horizon harness mechanics, not evidence of LLM learning, discovery,
generalization, recursive reasoning or improvement of its own improver.
It has no sealed task set, no API calls and no cost-per-generation evidence.

Before calling the system actual RSI, an additional study must replace the
scripted proposer with a bounded real coding model, use an independently
maintained held-out test corpus, match budgets to a fixed-improver control and
demonstrate improved capacity to *generate improvements*, rather than only
improved task accuracy.
