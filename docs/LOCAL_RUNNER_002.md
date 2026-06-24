# LOCAL-RUNNER-002 — CI Control-Plane Migration

## Status

`SCOPED -> READ -> MODIFIED -> STATIC_VERIFIED`

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

## Manual Ubuntu check

`Manual Ubuntu Cross-Platform Verification` is `workflow_dispatch` only. It is not triggered by push or pull request and therefore does not consume GitHub-hosted minutes automatically.

## Preserved boundaries

- Repository token permission remains `contents: read`.
- No secrets are loaded.
- No live five-system directory is referenced.
- No deployment, merge, release, or runtime promotion is performed.
- The self-hosted runner remains a verification node, not the canonical WLS runtime.

## Acceptance criteria

1. The migration pull request is dispatched to the Windows self-hosted runner.
2. `WLS CI` passes on Python 3.11 and 3.13.
3. Repository invariants pass, including root install and package build.
4. ET001, ET002, ET003, and the evolution-chain checks pass when their path filters select them.
5. No automatic job is assigned to `ubuntu-latest` or `windows-latest` GitHub-hosted runners.
6. The Ubuntu workflow remains manual and unexecuted while hosted minutes are exhausted.

## Claim boundary

A green migration pull request proves the repository checks run on the current Windows host. It does not prove Linux compatibility, production readiness, five-system runtime safety, or autonomous local evolution.
