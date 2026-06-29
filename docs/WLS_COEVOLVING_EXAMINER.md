# WLS Co-Evolving Examiner

## Architectural position

The examiner is an **organ of the canonical `LivingSystem`**, not a replacement system. It is loaded through the existing WLS plugin mechanism and attaches one advisory object at `runtime.examiner`. It writes only examiner-prefixed tables into the canonical database and examiner events into the canonical evidence ledger.

It has no authority to execute tools, change goals, modify memory, alter plans, approve actions, merge code, deploy, or promote itself.

## Decision pipeline

```text
CandidateChange
  → constitutional action gate
  → epoch/version digest verification
  → enabled rule bundle
  → dimension findings
  → hard-failure dominance
  → ALLOW / BLOCK / ESCALATE / UNKNOWN
  → digest-bound verdict record
```

Dimensions currently covered:

1. scope integrity;
2. gate weakening;
3. claim ceiling;
4. artifact and secret hygiene;
5. canonical authority uniqueness;
6. evidence laundering;
7. incremental value and maintenance burden;
8. epoch freshness;
9. constitution/anchor protection;
10. evaluator-source diversity.

## Controlled evaluator evolution

The implementation separates three surfaces:

### Immutable constitution

The constitution contains hard boundaries that the examiner cannot rewrite during ordinary evolution. Evaluator versions carry its digest. A mismatch aborts evaluation or promotion.

### Epoch-frozen rule bundle

An evaluator version is frozen for the active epoch. Challenger versions may be tested, but they do not become active automatically.

### Owner-controlled anchor manifest

Incumbent and challenger must both bind to the same holdout manifest digest. A candidate cannot substitute a friendlier dataset during promotion.

## Promotion contract

A challenger is eligible only when all conditions hold:

- zero critical false passes;
- no false-pass regression;
- no false-reject regression;
- positive paired accuracy gain;
- no lower-bound regression;
- one-sided paired sign-test `p ≤ 0.05`;
- unchanged constitution digest;
- unchanged anchor-manifest digest.

Eligibility produces `AWAITING_OWNER`, not promotion. Applying promotion requires a non-empty owner actor and approval reference.

## Selective erasure

The system does not delete old records. When the evaluator changes:

- verdicts from the displaced epoch are marked `stale=1`;
- the reason names the replacement evaluator;
- original JSON, digest, evidence references, and timestamps remain intact;
- stale scores no longer count as current-criterion evidence.

This preserves auditability while preventing mixed-criterion statistics.

## Innovation beyond a direct RQGM port

1. **Constitution/utility split:** some rules may evolve; canonical authority, owner gates, and evidence semantics cannot.
2. **No partial promotion:** a candidate that improves one category but still false-passes other critical categories remains unpromoted.
3. **Hard-failure dominance:** no aggregate score can hide a constitutional failure.
4. **Evidence-family diversity:** correlated model judges trigger escalation unless deterministic or independent evidence exists.
5. **Value and burden gate:** technically valid work can still be blocked when maintenance cost is high and incremental effect is absent.
6. **Replay resistance:** submission nonces prevent duplicate governance records.
7. **Protected benchmark surface:** anchor and constitution edits are examined as governance attacks, not normal refactors.
8. **Claim-ceiling output:** every verdict states what it does not prove.
9. **Safe overlay:** code is built-in but opt-in; no runtime modification is required.
10. **Rollback by installed hash:** the overlay uninstaller deletes only unchanged installed files.

## Data tables

- `examiner_epochs`
- `examiner_versions`
- `examiner_verdicts`
- `examiner_promotions`
- `examiner_anchor_manifests`

All are additive and use the existing database transaction surface.
