# Task19 Main-Based Convergence

## Decision and authority

Task19 is rebuilt from authoritative `main` after `LOCAL-RUNNER-002`. Historical PR #22 diverged before packaging and Windows-runner convergence and is source material only. The canonical runtime remains `source/src/wls/runtime.py::LivingSystem`; this work does not create a second planner, state store, policy engine, goal authority, memory authority, or execution authority.

## Integrated candidate scope

- bounded Survival Supervisor with explicit, validated daemon budgets and backward-compatible defaults;
- persistent goal lifecycle, decomposition, debt, review, interruption recovery, and controlled ablation;
- schema-validated `task_spec` translation into the existing Planner, PolicyEngine, and ToolRegistry;
- write-free goal-free comparison using the existing bounded cognitive ranker;
- explicit action outcome provenance for current execution, historical reuse, durable recovery, and no observable outcome;
- goal progress, cognition calibration, memory attribution, and skill learning gated on attributable current/recovered outcomes;
- candidate-only Provider Hub CLI with SSRF controls, redirect blocking, response limits, secure-vault fail-closed behavior, and network probes outside the global state lock;
- deterministic root build, clean Wheel installation, exact-head reporting, and a 100-cycle non-idle bounded workload with runtime re-instantiation after cycles 25, 50, and 75.

## Rejected or deferred

- stale Wheels, egg-info, build metadata, committed soak homes, databases, keys, caches, and timestamped intermediate reports;
- browser Provider UI until a complete authenticated loopback interface has an independent threat model;
- automatic Provider Hub attachment to canonical planning;
- GitHub-hosted capacity as a prerequisite for evidence;
- Task20 causal-shadow integration;
- owner-host deployment or live-data mutation.

## Verification entry points

Primary Windows command from a clean exact-head checkout:

```powershell
.\scripts\run_task19_windows.ps1
```

Diagnostic preflight without the soak:

```powershell
.\scripts\run_task19_windows.ps1 -SkipSoak
```

The full gate runs compilation, repository-layout verification, the complete pytest suite, ET001–ET004, Ruff, Mypy, Bandit, root Wheel build, outside-repository clean installation, and the 100-cycle workload. Reports belong under ignored `artifacts/task19/` and must identify the exact runtime head.

## Review order

1. Confirm the PR is based on current `main` and contains no historical PR merge.
2. Review `config.py`, `task19_stabilization.py`, Provider Hub boundaries, and focused tests.
3. Run the diagnostic Windows gate and repair deterministic failures.
4. Run the full Windows gate including the bounded workload.
5. Independently inspect JSON/Markdown evidence and tracked-file cleanliness.
6. Only the owner may decide whether to merge.

## Hard stop rules

Keep the PR Draft when any mandatory command fails, times out, or is unavailable; when generated or secret-like artifacts are tracked; when `task_spec` bypasses canonical policy; when reused results advance goals, memory, cognition, or skills as new interventions; when Provider Hub attaches itself to planning; when exact-head evidence diverges; or when a requested change would require a parallel authority.

## Claim ceiling

Even after all gates pass, the strongest supported statement is:

> The exact tested WLS candidate passed its repository suite, evolution verifiers, clean Wheel installation, and a bounded 100-cycle local workload with three runtime re-instantiations.

This does not establish production maturity, multi-day or indefinite reliability, owner-host benefit, unrestricted autonomy, consciousness, external utility, permission to merge, permission to enable Provider Hub routing, or permission to start Task20.

## Current status

Repository construction is complete on Draft PR #23, but acceptance remains closed until the offline Windows gate passes on the final exact head and independent review finds no unresolved P1/P2 defects. Missing GitHub-hosted checks are never interpreted as PASS.
