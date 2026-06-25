# LOCAL-RUNNER-002 — CI Control-Plane Migration and 24-Hour WLS Soak

## Status

`SCOPED -> READ -> MODIFIED -> HOST_PREREQUISITES_VERIFIED -> RUNTIME_VERIFICATION_ACTIVE`

The implementation is candidate-only on the draft pull request. The 24-hour campaign has not yet run and no production-readiness claim is authorized.

## Objective

Move automatic repository verification from exhausted GitHub-hosted runners to the repository-scoped Windows self-hosted runner, then verify sustained GitHub-to-runner dispatch and isolated canonical WLS restart continuity for 24 hours. Ubuntu remains an explicit manual cross-platform check.

## Automatic local checks

The following workflows target `[self-hosted, Windows, X64]`:

- `WLS CI`
- `Verify Repository Invariants`
- `Verify Repo-Native Evolution Chain`
- `Verify EVOLUTION-TARGET-001`
- `Verify EVOLUTION-TARGET-002`
- `Verify EVOLUTION-TARGET-003`

Python 3.11 and 3.13 matrix jobs are serialized with `max-parallel: 1` where applicable. Temporary files and pip cache are directed to `D:\actions-runner`.

All Windows workflows use `.github/scripts/prepare-windows-runner.ps1` to bind an isolated host Python visible to the runner service account. Required local prerequisites are checked before repository verification proceeds.

## Windows restart-continuity defect closed

The first real Windows run exposed an evidence-ledger failure after reconstructing `LivingSystem`: `signature_mismatch` at sequence 1. The evidence and approval keys were created through low-level file descriptors without explicitly requesting Windows binary mode. A random byte `0x0A` could therefore be persisted as CRLF while the first process retained the original bytes in memory.

The candidate repair:

- opens both key files with `O_BINARY` when the platform provides it;
- writes until all 32 bytes are persisted;
- performs a binary read-back comparison before accepting the key;
- rejects existing key files whose length is not exactly 32 bytes instead of silently adopting corrupted identity material;
- includes deterministic newline-byte regression tests for evidence and approval key restart continuity.

Legacy malformed keys are not automatically repaired because replacing a key would invalidate existing evidence or approval signatures.

## Manual Ubuntu check

`Manual Ubuntu Cross-Platform Verification` is `workflow_dispatch` only. It is not triggered by push or pull request and therefore does not consume GitHub-hosted minutes automatically.

## 24-hour bidirectional WLS soak

`Local Runner 24h Bidirectional Soak` is armed but cannot start from a pull request. After an owner-authorized merge, each successful main-branch verification completion invokes a local guard. The guard starts a unique instance of campaign family `LOCAL-RUNNER-24H-001` only when all six required main-branch workflows are successful on the same commit.

The campaign consists of six chained four-hour phases. Each phase is a fresh GitHub-to-runner dispatch. Five samples are taken per phase at hourly spacing, producing thirty samples and twenty-four elapsed hourly intervals.

Every sample performs all of the following:

1. verifies the Windows runner service is running;
2. verifies TCP 443 reachability for GitHub and the Actions broker;
3. verifies the pinned local 7-Zip prerequisite and D-drive free space;
4. launches a fresh Python process against one campaign-scoped WLS home;
5. reconstructs the canonical `LivingSystem` from its persisted configuration and SQLite state;
6. runs one canonical observe-model-attend-plan-act-learn cycle with the local cognitive provider;
7. runs full database, evidence-chain, cognition, causal-memory, and memory-attribution integrity checks;
8. checkpoints SQLite and appends one combined runner/WLS JSONL heartbeat.

The isolated WLS state is stored under `D:\actions-runner\_state\24h-wls\<campaign-instance>`. Evidence is appended under `D:\actions-runner\_evidence\24h-bidirectional`. The campaign does not use the owner’s normal WLS home or any live five-system directory.

A one-cycle, zero-wait version of the same PowerShell/Python path is executed in WLS CI on Python 3.13. That fast check validates wiring only; it is not 24-hour evidence.

## Preserved boundaries

- Repository token permission remains read-only (`contents: read`, plus `actions: read` for the soak guard).
- All external actions are pinned to immutable full commit SHAs.
- No live five-system directory is referenced.
- No deployment, release, runtime promotion, Notion write, or autonomous repository mutation is performed.
- The self-hosted runner is a verification node, not the canonical owner runtime.
- The soak uses read-only WLS policy and forbids autonomous reversible writes.
- The 24-hour soak starts only after owner-authorized merge and six successful main checks on one commit.

## Acceptance criteria

1. The migration pull request is dispatched to the Windows self-hosted runner.
2. `WLS CI` passes on Python 3.11 and 3.13.
3. Repository invariants pass, including root install and package build.
4. ET001, ET002, ET003, and the evolution-chain checks pass on the same pull-request head.
5. The deterministic key regression reproduces newline-sensitive bytes and preserves ledger verification after restart.
6. The zero-wait WLS/local-chain heartbeat passes through the exact candidate scripts.
7. No automatic repository verification job is assigned to a GitHub-hosted runner.
8. After owner-authorized merge, all six main-branch workflows pass on one commit before the campaign starts.
9. The campaign produces thirty healthy JSONL samples across six independent phases and twenty-four elapsed hours.
10. Every sample reports `cycle_status=SUCCEEDED`, `integrity.ok=true`, an unpaused and unkilled WLS state, a running runner service, and successful GitHub/broker connectivity.

## Claim boundary

A green migration pull request proves that the repository checks and one bounded WLS heartbeat run on the current Windows host. A completed campaign proves 24-hour persistence of the tested GitHub-to-runner dispatch, runner-to-GitHub reporting, and isolated read-only WLS cycle/restart/integrity path for one host and one commit.

Neither result proves Linux compatibility, unattended host reboot recovery, Windows service installation for WLS itself, production readiness, live five-system safety, unrestricted autonomous repair, indefinite operation, or external utility.
