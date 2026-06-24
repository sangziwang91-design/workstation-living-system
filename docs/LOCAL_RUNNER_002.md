# LOCAL-RUNNER-002 — CI Control-Plane Migration

## Status

`SCOPED -> READ -> MODIFIED -> HOST_PREREQUISITES_VERIFIED -> RUNTIME_VERIFICATION_ACTIVE`

## Objective

Move automatic repository verification from exhausted GitHub-hosted runners to the repository-scoped Windows self-hosted runner while retaining Ubuntu as an explicit manual cross-platform check.

## Automatic local checks

The following workflows now target `[self-hosted, Windows, X64]`:

- `WLS CI`
- `Verify Repository Invariants`
- `Verify Repo-Native Evolution Chain`
- `Verify EVOLUTION-TARGET-001`
- `Verify EVOLUTION-TARGET-002`
- `Verify EVOLUTION-TARGET-003`

Python 3.11 and 3.13 matrix jobs are serialized with `max-parallel: 1` where applicable. Temporary files and pip cache are directed to `D:\actions-runner`.

All Windows workflows use the shared prerequisite bootstrap at `.github/scripts/prepare-windows-runner.ps1`. The bootstrap verifies and exposes `D:\Tools\7-Zip\7z.exe` before `actions/setup-python` provisions isolated Python versions for the `NETWORK SERVICE` runner account.

## Manual Ubuntu check

`Manual Ubuntu Cross-Platform Verification` is `workflow_dispatch` only. It is not triggered by push or pull request and therefore does not consume GitHub-hosted minutes automatically.

## 24-hour bidirectional soak

`Local Runner 24h Bidirectional Soak` is armed but cannot start from a pull request. After this migration is merged, each successful main-branch verification completion invokes a short local guard. The guard starts campaign `LOCAL-RUNNER-24H-001` only when all six required main-branch workflows are successful on the same commit.

The campaign consists of six chained four-hour phases. Every phase is a fresh GitHub-to-runner dispatch; each phase emits five hourly runner-to-GitHub heartbeats and appends local JSONL evidence under `D:\actions-runner\_evidence\24h-bidirectional`.

## Preserved boundaries

- Repository token permission remains read-only (`contents: read`, plus `actions: read` for the soak guard).
- All external actions are pinned to immutable full commit SHAs.
- No live five-system directory is referenced.
- No deployment, release, or runtime promotion is performed.
- The self-hosted runner remains a verification node, not the canonical WLS runtime.
- The 24-hour soak starts only after the owner-approved migration is merged and all required main checks are green.

## Acceptance criteria

1. The migration pull request is dispatched to the Windows self-hosted runner.
2. `WLS CI` passes on Python 3.11 and 3.13.
3. Repository invariants pass, including root install and package build.
4. ET001, ET002, ET003, and the evolution-chain checks pass when their path filters select them.
5. No automatic repository verification job is assigned to a GitHub-hosted runner.
6. The Ubuntu workflow remains manual and unexecuted while hosted minutes are exhausted.
7. After merge, all six main-branch workflows pass on one commit before `LOCAL-RUNNER-24H-001` starts.
8. The 24-hour campaign produces thirty successful heartbeats across six independently dispatched phases.

## Claim boundary

A green migration pull request proves the repository checks run on the current Windows host. A completed 24-hour campaign proves sustained GitHub-to-runner dispatch and runner-to-GitHub heartbeat return for that host and commit. Neither result proves Linux compatibility, production readiness, five-system runtime safety, or autonomous local evolution.
