# WLS Repo-Native Evolution Chain

**Status:** ACTIVE_SINGLE_TRUNK_ET004_COMPLETE
**Canonical runtime remains:** `source/src/wls/runtime.py::LivingSystem`
**Canonical baseline main head:** `0a38c77b61ba91bb5a0fa7f88df028d030e569ba` (v0.9.0.dev2)

## Completed waves

| Wave | Title | Status |
|---|---|---|
| ET-001 | Verified failure-to-skill-to-reuse loop | MERGED_CI_VERIFIED |
| ET-002 | Bounded local cognition vertical slice | MERGED_CI_VERIFIED |
| ET-003 | Causal Memory Retrieval and Measurable Learning Advantage | MERGED_CI_VERIFIED |
| ET-004 | Capability Gaps Closure and Single-Trunk Consolidation | IMPLEMENTED_LOCAL_VERIFIED |

## ET-004 summary

Closed 6 real capability gaps identified by ZIP iteration audit:
- `repo_explorer.py` — Repository file/symbol/dependency explorer
- `merge_node.py` — Multi-worker artifact merge with conflict detection
- `result_promotion.py` — Cross-agent result-to-memory gated promotion
- `anti_repeat.py` — Failure signature deduplication with cooldown
- `capabilities.py` — Duplicate authority detector
- `coding_workers.py` — Claude Code / Codex / OpenCode worker adapters

Added `acceptance.py`, `security.py`, `benchmark.py`, `fault_injection.py`, `scheduler.py` (persistent), `memory_projection.py`, `compaction.py`, `reviewer.py`, `graph_recovery.py`, `qos_router.py`, `skill_compiler.py`, `longitudinal.py`.

Consolidated the runtime authority into a single canonical `main` trunk. The installed-instance P01-P89 architecture admission recorded 1 `ADMIT` + 88 `ADMIT_SHADOW_ONLY` + 0 `BLOCKED`; this is an architecture-admission result, not 89 production proofs. Six integration pipelines were recorded as tested end-to-end in the historical ET-004 evidence.

## Authority order

1. Runtime evidence and governed tool outcomes.
2. GitHub commits, pull requests, Actions and versioned verification records.
3. `.evolution/CURRENT_CHAIN.json` and validated evolution packets.
4. Model-generated proposals (context only).

## Current worker policy

- `readonly-inspector`, `planner-shadow`, `owner-gated-executor-shadow` are default LOCAL_SHADOW workers.
- ChatGPT may operate as external worker through A2A adapter.
- Coding workers (Claude Code, Codex, OpenCode) are available via `CodingWorkerFactory` when installed.
- No external provider receives automatic routing, credentials, or write authority.

## Queue states

- `ready`: dependencies satisfied; a compatible ACTIVE worker may claim.
- `claimed`: one worker owns the task lease.
- `blocked`: a named dependency or owner decision is missing.
- `verification`: implementation exists and awaits deterministic gates.
- `completed`: evidence and final disposition are recorded.

## Verified evidence

- Historical installed-instance P01-P89 architecture admission: 1 `ADMIT` + 88 `ADMIT_SHADOW_ONLY` + 0 `BLOCKED`.
- Current repository inventory: 62 Python test files; the historical 41-test-file figure is no longer current.
- Historical installed instance: self-check, verify, and one WLS cycle were recorded as passing.
- ET-001, ET-002, ET-003 were previously CI-verified on GitHub Actions.
- Current draft verification-boundary repair: repo-native evolution control-plane and portable repository-invariant jobs pass on GitHub-hosted CI; Windows root-install/full-runtime gates remain pending on the self-hosted Windows runner.

## Current handoff

ET-001 through ET-004 remain the completed early evolution targets. The canonical baseline main head is `0a38c77b61ba91bb5a0fa7f88df028d030e569ba`. Later ET-005/ET-006 evidence is tracked in `CURRENT_STATE.yaml`; one qualified owner-task measurement is recorded, while repeated longitudinal evidence across distinct task classes remains pending.

## Activation boundary

The repository is single-trunk, architecture-validated, and locally verified. It does not claim unattended evolution, provider-independent autonomy, production-host longitudinal learning, or a trained local model.
