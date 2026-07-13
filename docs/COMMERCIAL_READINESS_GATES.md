# WLS Personal Commercial Readiness Gates

This checklist defines what "mature enough for personal commercial use" means for WLS.
Claims must be backed by owner-host receipts, tests, or explicit operator evidence.

## Current Position

WLS is a guarded local workstation system with health checks, evidence ledger,
read-only garbage audit, bounded soak receipts, causal memory, bounded local
cognition, owner-gated actions, and an installed owner-host instance.

It is not yet a finished commercial product. The current work is moving it from
prototype toward dependable personal production use.

## Gate 1: Operational Health

Required evidence:
- `wls health` returns `OK` on the installed owner-host instance.
- No runtime or daemon lock remains after bounded commands.
- Pending action count remains bounded.
- Latest cycle is `SUCCEEDED` or a non-dangerous explicit stopped state.
- Garbage audit is available and read-only by default.

Current evidence:
- Latest owner-host health is `OK`.
- Latest locks are absent.
- Latest garbage audit reports `CLEAN` with 0 candidates.

Status: `PASS_CURRENTLY`

## Gate 2: Performance Budget

Required evidence:
- Health snapshot stays below budget.
- Runtime init stays below budget.
- Self-check and garbage audit stay below budget.
- Canonical owner-host cycle has phase timings and bounded soak receipt.
- Multi-cycle soak has no failures.

Current evidence:
- Latest performance budget receipt is passing:
  `performance_budget_audit_a8897e044943443dbd8f53b8293cf769`.
- UI/API endpoint budgets remain included and passing after the owner review UI:
  `/api/health` 0.0184s, `/api/bootstrap` 0.5062s, `/api/product` 0.6095s.
- Owner review write workflows are measured on a disposable temporary runtime:
  garbage scan 0.3835s, garbage cleanup/quarantine 0.2977s, quarantine clear
  0.3765s.
- Disposable rollback workflow is measured by the same performance budget path:
  `rollback_disposable_clone` 1.3785s against a 30s budget.
- Latest three-cycle owner-host soak passed:
  `bounded_soak_audit_c141e460bf294e11a97fb37eb25087b2`.
- Latest three-cycle DB growth: 212992 bytes.
- Latest three-cycle evidence growth: 48 records.

Status: `PASS_BOUNDED_SOAK`

Remaining work:
- Add explicit commercial targets for P50/P95 canonical cycle latency.
- Keep measuring rollback on both the lightweight disposable path and the
  owner-host-sized standalone drill before any live rollback claim.

## Gate 3: Data Growth and Cleanup

Required evidence:
- DB growth per cycle is measured.
- Garbage candidates are detected without automatic deletion.
- Cleanup execution requires owner approval and leaves a receipt.
- Reversible cleanup path exists where possible.

Current evidence:
- DB growth is measured in bounded soak receipts.
- Garbage audit is read-only and receipt-bound.
- Owner-approved cleanup execution exists behind explicit approval reference.
- Owner-approved cleanup now quarantines candidates instead of directly deleting
  them where possible.
- Owner-approved quarantine clear exists as a second explicit decision gate.
- Owner-host cleanup drill `garbage_audit_22ccd2f5865c41469030e1e1dafff050`
  predated quarantine mode; it deleted only audited candidates and preserved the
  non-candidate file.
- Owner-host quarantine drill `garbage_audit_782e4038f2a2479bbc2b49309df14c39`
  quarantined 2 audited LOW-risk candidates, deleted 0 candidates, and preserved
  the non-candidate file.
- Owner-host quarantine clear
  `garbage_quarantine_clear_7bf8cf834e26425594be21926844a8a1` cleared the drill
  quarantine after explicit approval.
- Post-drill home audit `garbage_audit_2348fa4a68204bf29a0e191bf9be96cf`
  reported `CLEAN` with 0 candidates.
- Latest post-quarantine home audit `garbage_audit_b60975718ef141b497b3eb84c54b5645`
  reported `CLEAN` with 0 candidates.
- Owner review API is now split into read-only latest audit lookup and owner-gated
  write actions: `POST /api/garbage-audit/scan`,
  `POST /api/garbage-audit/cleanup`, and
  `POST /api/garbage-audit/quarantine-clear`.
- `GET /api/garbage-audit` is deliberately non-mutating, preventing the UI from
  creating audit receipts just by rendering.
- Runtime receipt retention pressure is now audited by `wls retention-audit`.
  Latest owner-host audit `retention_audit_875771e18ef841788d57964c114a2222`
  found 7 receipt lists, 42 receipts, about 90KB JSON, no invalid keys, and no
  over-limit lists. No receipts or evidence rows were deleted.

Status: `PASS_QUARANTINE_CLEAR_REVIEW_AND_RETENTION_AUDIT`

Remaining work:
- Add owner-approved retention compaction only after repeated audits show a
  real over-limit list or oversized projection.

## Gate 4: Upgrade and Rollback

Required evidence:
- Wheel build is reproducible.
- Installed instance can be upgraded with a health gate.
- DB backup is taken before upgrade.
- Rollback path is tested and receipt-bound.

Current evidence:
- Wheel build and owner-host reinstall are verified for
  `workstation_living_system-0.9.0.dev2-py3-none-any.whl`.
- Owner-host install reports `wls health` status `OK` after install.
- Upgrade/rollback drill `upgrade_drill_11e80b82704c4150a119bda4f0bec3c4`
  created an online DB backup, copied it as a restore-check DB, and passed
  SQLite `integrity_check` plus foreign-key validation.
- Drill backup size: 123138048 bytes.
- Drill backup time: 10.9434s.
- Drill explicitly did not modify the live install or restore the live DB.
- Disposable cloned-host rollback drill
  `upgrade_drill_0d35becebd224aa287549c3b48b2f2ad` passed. It backed up the
  owner-host DB, copied it into a temporary clone, wrote a simulated upgrade
  marker, verified clone health, restored the clone DB from backup, verified
  `integrity_check=ok`, confirmed the marker was removed, matched the backup
  hash after rollback, and deleted the temporary clone.
- Latest disposable clone backup time: 7.8045s.
- Latest disposable clone backup size: 123138048 bytes.
- Live install and live database were not restored or modified by the rollback
  step.

Status: `PASS_DISPOSABLE_CLONE_ROLLBACK_DRILL_LIVE_ROLLBACK_PENDING`

Remaining work:
- Add a one-command owner-approved rollback operator workflow.
- Keep live rollback claims blocked until an explicit owner-approved live
  rollback is requested and verified.

## Gate 5: Safety and Authority

Required evidence:
- Default install remains read-only.
- Writes stay sandboxed.
- High-risk actions require explicit owner approval.
- No cleanup, promotion, publication, or rollback runs without owner authority.
- Evidence ledger records material state changes.

Current evidence:
- Current owner-host is read-only.
- Garbage cleanup is audit-only.
- Action and self-model updates preserve evidence order.

Status: `PASS_CURRENTLY`

## Gate 6: Personal Business Value

Required evidence:
- Real owner tasks are admitted, executed, and reviewed.
- Outcomes compare against a preserved baseline.
- Longitudinal benefit is measured over repeated tasks.
- Failures produce actionable repair candidates, not silent drift.

Current evidence:
- Synthetic and bounded learning loops exist.
- Owner-host longitudinal protocol
  `long_9f0a7520d7c64d1d88b2df1c9c863ab6` is started with frozen baseline
  `077f416`.
- Initial longitudinal report
  `long_report_c6079a9232884215885a9552ec69f697` is present in `wls health`
  and correctly reports `LONGITUDINAL_REPORT_NEEDS_MORE_EVIDENCE` with 0
  measurements.
- Longitudinal reports now distinguish qualified owner-task measurements from
  partial measurements. A measurement without both `task_reference` and
  `owner_review` cannot make the report pass.
- First qualified owner-task measurement
  `meas_27d88947206142499a87780d5c8922ec` was recorded for the ET006
  commercial-readiness iteration. It references the rollback workflow budget,
  disposable clone rollback drill, and focused regression tests.
- Latest owner-host longitudinal report
  `long_report_f433092b98c24dc7a524cd75f9b89b84` reports
  `LONGITUDINAL_REPORT_PASSED` for exactly 1 qualified measurement, success
  rate 1.0, no regressions, and claim ceiling limited to that recorded
  workload.

Status: `FIRST_QUALIFIED_MEASUREMENT_RECORDED_REPEATABILITY_PENDING`

Remaining work:
- Record repeated real owner-task measurements across different task classes.
- Record task value, time saved, failure rate, and recovery quality.

## Gate 7: Owner UI and Operator Experience

Required evidence:
- Health, soak, garbage audit, pending actions, locks, and latest cycles are visible.
- Owner can approve cleanup/action/promotion from a clear review surface.
- UI/API checks have performance budgets and regression tests.

Current evidence:
- UI health endpoint exists.
- CLI health, garbage audit, performance budget, and soak audit exist.
- Owner-console read API performance is receipt-bound through
  `performance_budget_audit_a8897e044943443dbd8f53b8293cf769`.
- Owner console now has a `Review` surface for garbage candidates, latest audit
  status, owner-approved quarantine, and second-approval quarantine clear.
- Review workflow regression tests verify owner write gates, approval reference
  requirements, read-only GET behavior, audit detail lookup, and static UI wiring.
- `performance-audit --include-write-workflows` measures the owner-gated Review
  write path against a disposable runtime home instead of mutating the real
  owner-host home.
- `performance-audit --include-rollback-workflow` measures the disposable clone
  rollback command path and records it beside the UI/API budgets.

Status: `PASS_OWNER_REVIEW_AND_ROLLBACK_WORKFLOW_BUDGETS`

Remaining work:
- Add an owner-facing approval workflow for live rollback before any live
  rollback can be claimed.

## Next Mainline Tasks

1. Record first real owner-task longitudinal measurements.
2. Continue reducing planner, cognition-learning, and memory retrieval latency.
3. Add an owner-facing approval workflow for live rollback.
4. Add owner-approved retention compaction only when audit evidence justifies it.
