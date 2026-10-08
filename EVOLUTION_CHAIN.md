# WLS Repo-Native Evolution Chain

**Status:** ACTIVE_SINGLE_TRUNK_ET004_COMPLETE
**Canonical runtime remains:** `source/src/wls/runtime.py::LivingSystem`
**Canonical branch:** `main` (current HEAD is verified from GitHub, not this historical document); version `0.9.0.dev2`.

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

Maintained one canonical `LivingSystem` runtime on `main`, while preserving historical branches. Historical P01-P89 admissions contain 1 `ADMIT`, 88 `ADMIT_SHADOW_ONLY`, 0 `BLOCKED`: these are architectural verdicts, not independent production-task proofs. Six integration pipelines were recorded historically.

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

- Historical P01-P89 admission: 1 `ADMIT` + 88 `ADMIT_SHADOW_ONLY`; no independent production claim follows from this distribution.
- Public main Git tree snapshot (2026-10-08): 107 package Python files including `__init__.py`, 104 excluding them, and 74 test Python files.
- Hosted GitHub Actions now verify full WLS regressions and packaging on Linux/Windows; a local owner-host installation remains a separate measurement.
- ET001, ET002, ET003 are separately verified by hosted Actions; time-series benefit over real tasks remains unproven.

## Current handoff

ET001-ET004 remain historical engineering milestones. ET005 owner-host receipts and one qualified ET006 measurement are recorded in `CURRENT_STATE.yaml`; they are not evidence of repeated task-class transfer. The active target is `MAINLINE-TARGET-001`: get the canonical WLS life loop and owner-approved Patch Mission through a real end-to-end action with independently checked outcomes, without conflating a simulated sandbox with RSI.

## Activation boundary

The repository has a single runtime authority, source-level hosted CI and retained historical owner-host receipts. It does **not** claim production installation of the latest commit, multiweek self-improvement, provider-independent agency, owner-free irreversible actions, or a trained local model.
