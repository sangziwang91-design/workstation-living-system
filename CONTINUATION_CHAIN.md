# WLS Continuation Chain

This file is the persistent handoff point for GPT-driven and multi-model GitHub iterations.

## Canonical authority

- Subject/runtime: `source/src/wls/runtime.py::LivingSystem`
- Identity/invariants: `LIVING_SYSTEM_GENOME.md`
- Machine state: `CURRENT_STATE.yaml`
- Current implementation PR: `#8`, branch `evolution-target-004-persistent-goals`
- External models are temporary workers and teachers, never identity or canonical memory.

## Verified phase chain

1. **ET001 — failure → candidate skill → validation → promotion/reuse/rollback**
2. **ET002 — bounded local cognition, predictions, action and calibration**
3. **ET003 — causal memory retrieval, refutation and frozen memory-disabled comparison**
4. **ET004 — persistent goal lifecycle, decomposition, debt, review, attribution and restart continuity**

ET004 is locally full-gate verified but remains remote-CI pending until its implementation
is committed to PR #8 and the exact head passes the required matrix.

## Planned phase chain after ET004 merge

5. **ET005 — Repo-Native Evolution Control Plane**
   - `.evolution/CURRENT_TARGET.yaml`
   - atomic task packets and allowed-file boundaries
   - worker capability registry
   - claim / complete / block / stale-claim recovery
   - deterministic acceptance and evidence capture

6. **ET006 — Canonical Developmental Trajectory Contract**
   - state before
   - observation and provenance
   - selected action and approval
   - prediction before action
   - state after and real outcome
   - cost, duration, risk and evidence
   - replay/training eligibility and truth level

7. **ET007 — Homeostasis / Capacity Governor**
   - one auditable cycle budget consumed by perception, planning, action, memory,
     skill growth and external model calls
   - circuit breaking, cooldown, degradation and safe sleep

8. **ET008 — Minimum Local Action-Outcome Predictor**
   - begin with deterministic/frequency/logistic/tree baselines
   - predict action success/failure only
   - no Transformer unless simpler baselines are demonstrably insufficient
   - candidate/frozen baseline/owner promotion/rollback lifecycle

## Unique next action

Land ET004 on PR #8, run and repair the complete remote matrix, then stop at the owner merge
gate. ET005 must branch from verified merged `main`, not from an unmerged ET004 branch.

## Handoff protocol

Every iteration must report:

- exact branch and head SHA;
- actual PR and CI state;
- VERIFIED / INFERENCE / UNKNOWN evidence;
- files changed and commands executed;
- current claim ceiling;
- one unique next action;
- explicit stop conditions.
