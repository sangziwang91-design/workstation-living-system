# Five-round adversarial judgment and repair

The final adversarial pass targeted implementation defects rather than merely re-running ordinary examples. The pre-fix observations were reproduced before repair and retained in `evidence/adversarial-pre-fix.json`.

| Trial | Attack | Pre-fix result | Repair | Post-fix result |
|---:|---|---|---|---|
| 1 | `path_traversal_scope_bypass` | `ALLOW` | reject absolute, traversing, backslash, NUL, and non-canonical paths before rule evaluation | `BLOCK` / **PASS** |
| 2 | `restart_primary_key_collision` | `UNIQUE constraint failed: examiner_verdicts.verdict_id` | runtime IDs use UUID entropy; deterministic counters require explicit sandbox mode | `NO_COLLISION` / **PASS** |
| 3 | `evaluator_version_identity_collision` | `ACCEPTED_BY_INSERT_OR_IGNORE` | version ID and digest form an immutable identity; epoch IDs cannot be replaced or reopened | `BLOCK` / **PASS** |
| 4 | `forged_promotion_decision` | `APPLIED` | digest-bind the decision and atomically replay comparison against the same holdout before owner-approved application | `BLOCK` / **PASS** |
| 5 | `duplicate_evidence_id_and_malformed_digest` | `ALLOW` | reject duplicate evidence IDs and require SHA-256 shape for verified evidence | `BLOCK` / **PASS** |

## Additional adversarial controls retained

- constitution digest substitution rejection;
- holdout-manifest mismatch rejection;
- submission nonce replay rejection;
- critical failure dominance over aggregate scores;
- protected anchor/constitution surface rejection;
- atomic rollback when an evidence-ledger write fails during promotion.

## Result

- Trials: 5/5 passed.
- SQLite integrity: `ok`.
- Report digest: `a77f15a21f0947e8e07aee440c8f4fd7d53fd9f65ee63bc76f507d87d963ede6`.
