# Known limits and claim ceiling

## Verified directly

- Package code compiles.
- 25 package tests pass.
- Two independent deterministic replays of the ten-round sandbox produce the same report digest.
- Two independent deterministic replays of the five-round adversarial suite produce the same report digest.
- Installer conflict preflight, apply, hash-safe rollback, and preservation of pre-existing identical files are tested.
- Constitution, anchor set, executable implementation, and evaluator version are digest-bound.
- Promotion is owner-gated, decision-digest-bound, replays the sealed comparison, and commits atomically.

## Not verified

- Full regression against the canonical private WLS repository.
- Windows owner-host behavior.
- Interaction with the current Jules worktree or any unsubmitted diff.
- Production private holdout performance.
- Real-world social, clinical, commercial, or personal value.
- Long-duration resource behavior.
- Resistance to an attacker with unrestricted write access to Python source and the database.

## Important sandbox limitation

The 80-case holdout generator is included in this package to make the ten-round result independently reproducible. It is therefore **not private**. Before any real evaluator promotion, the owner must provide a separately controlled holdout whose labels are inaccessible to candidate generation and whose manifest digest is committed before comparison.

## Evidence resolver limitation

The examiner validates evidence identity, uniqueness, level, provenance family, and SHA-256 shape. It does not fetch every external receipt by itself. Target integration should add resolvers for canonical WLS evidence records, workflow runs, owner receipts, and external-use receipts. Until then, evidence existence outside the canonical ledger remains a separate verification gate.

## Static tools

`ruff`, `mypy`, and `bandit` were unavailable in the delivery runtime. Their absence is recorded as unavailable, not passed. Compile, pytest, deterministic sandbox replay, adversarial replay, database integrity, installer tests, and artifact hygiene were executed.
