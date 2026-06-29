# Task19 Main-Based Convergence

## Decision and authority

Task19 is rebuilt from authoritative `main` after `LOCAL-RUNNER-002`. Historical PR #22 is source material only. The canonical runtime remains `source/src/wls/runtime.py::LivingSystem`; this work does not create a second planner, state store, policy engine, goal authority, memory authority, execution authority, or acceptance authority.

## Integrated candidate scope

- bounded Survival Supervisor with explicit daemon budgets and backward-compatible defaults;
- persistent goal lifecycle, atomic decomposition, debt, review, interruption recovery, and controlled ablation;
- schema-validated `task_spec` translation into the existing Planner, PolicyEngine, and ToolRegistry;
- write-free goal-free comparison using the existing bounded cognitive ranker;
- policy-before-reuse idempotency and exact action-contract matching;
- persisted action outcome provenance for current execution, historical reuse, durable recovery, no observable outcome, and legacy rows;
- owner/external-request preemption over background durable goals;
- candidate-only Provider Hub CLI with fixed presets, loopback-only custom endpoints, redirect blocking, response limits, secure-vault fail-closed behavior, and probes outside the global state lock;
- deterministic root build, Wheel verification, clean installation, exact-head reporting, and a 100-cycle bounded workload with runtime re-instantiation after cycles 25, 50, and 75.

## Cleanup convergence

The final cleanup pass removed the duplicate provenance module, duplicate provenance test module, second PowerShell acceptance entry, and nested Python acceptance wrapper. Unique restart-persistence coverage was merged into the retained idempotency test file.

The only supported acceptance path is:

```text
scripts/run_task19_windows.ps1
  → source/scripts/verify_task19_convergence.py
  → focused single-purpose verifiers
```

Development commits remain on the candidate branch for audit. Any accepted merge into `main` must use squash merge.

## Rejected or deferred

- stale Wheels, egg-info, build metadata, committed soak homes, databases, keys, caches, and timestamped intermediate reports;
- browser Provider UI until an authenticated loopback interface has an independent threat model;
- automatic Provider Hub attachment to canonical planning;
- GitHub-hosted capacity as a prerequisite for evidence;
- Task20 causal-shadow integration;
- owner-host deployment or live-data mutation.

## Verification commands

Full acceptance command from a clean exact-head checkout:

```powershell
.\scripts\run_task19_windows.ps1
```

Diagnostic preflight without the soak:

```powershell
.\scripts\run_task19_windows.ps1 -SkipSoak
```

The diagnostic command cannot set `acceptance_ready=true`.

The full gate runs compilation, repository-layout and hygiene checks, the complete pytest suite, ET001–ET004, Ruff, Mypy, Bandit, root Wheel build, Wheel metadata and contamination validation, outside-repository clean installation, and the 100-cycle workload. Reports belong under ignored `artifacts/task19/` and must identify the exact tested head.

## Hard stop rules

Keep the PR Draft when any mandatory command fails, times out, or is unavailable; when generated or secret-like artifacts are tracked; when `task_spec` bypasses canonical policy; when reused results advance goals, memory, cognition, or skills as new interventions; when Provider Hub attaches itself to planning; when exact-head evidence diverges; or when a requested change would require a parallel authority.

## Claim ceiling

Even after all gates pass, the strongest supported statement is:

> The exact tested WLS candidate passed its repository suite, evolution verifiers, clean Wheel installation, persistent provenance integrity, and a bounded 100-cycle local workload with three runtime re-instantiations.

This does not establish production maturity, multi-day or indefinite reliability, owner-host benefit, unrestricted autonomy, consciousness, external utility, permission to merge, permission to enable Provider Hub routing, or permission to start Task20.

## Current status

PR #23 is open, Draft, and remains `CANDIDATE_UNVERIFIED`. Content cleanup is complete; acceptance remains closed until the local Windows gate passes on the current exact head and independent review finds no unresolved P1/P2 defects. Missing or queued GitHub-hosted checks are never interpreted as PASS.
