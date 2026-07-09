# WLS Repo-Native Evolution Chain

**Status:** ACTIVE_SINGLE_TRUNK_ET004_COMPLETE
**Canonical runtime remains:** `source/src/wls/runtime.py::LivingSystem`
**Current head:** `75786a5` (main, v0.9.0.dev2)

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

Consolidated all branches into a single canonical `main` trunk. P01-P89 architecture validation all green. 6 integration pipelines tested end-to-end.

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

- P01-P89 architecture validation: 89/89 passes on installed v0.9.0.dev2 instance.
- 41 test files, all unit + integration tests pass locally.
- Installed instance: self-check, verify, and WLS once all pass.
- ET-001, ET-002, ET-003 previously CI-verified on GitHub Actions.

## Current handoff

All ET-001 through ET-004 have been completed. The canonical repository is a single main trunk at head `75786a5` tagged `v0.9.0.dev2`. The next target is ET-005 (owner-host longitudinal validation).

## Activation boundary

The repository is single-trunk, architecture-validated, and locally verified. It does not claim unattended evolution, provider-independent autonomy, production-host longitudinal learning, or a trained local model.
