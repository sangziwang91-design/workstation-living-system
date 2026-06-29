# Task19 Clean Main-Based Convergence

## Authority

Task19 is represented by Draft PR #25 on branch `task19-clean-squash`. It is based directly on the accepted `main` head after `LOCAL-RUNNER-002`. PRs #22, #23, and #24 are closed, unmerged audit records.

The canonical runtime remains `source/src/wls/runtime.py::LivingSystem`. This candidate does not create a second planner, state store, policy engine, goal authority, memory authority, execution authority, provenance authority, or acceptance authority.

## Retained value

- bounded Survival Supervisor with explicit daemon budgets;
- atomic persistent goal decomposition, debt, review, interruption, and recovery;
- allowlisted `task_spec` translation through the existing Planner, PolicyEngine, and ToolRegistry;
- write-free goal-free comparison and Goal×Memory ET004;
- policy-before-reuse idempotency and exact action-contract checks;
- durable current, reused, recovered, no-outcome, and legacy provenance;
- owner or external-request preemption;
- candidate-only Provider Hub with fixed presets and loopback custom endpoints;
- optional shadow-only Examiner organ on the canonical SQLite migration chain;
- root build, Wheel verification, clean installation, repository hygiene, deployment ZIP roundtrip, and the bounded 100-cycle workload.

## Removed pollution

- duplicate provenance module;
- duplicate provenance test module after merging unique restart coverage;
- duplicate PowerShell acceptance entry;
- nested Python acceptance wrapper;
- dead internal execution wrapper;
- multiple active Task19 PRs;
- development commit history from the active candidate.

## Single verification path

```text
scripts/run_task19_windows.ps1
  → source/scripts/verify_task19_convergence.py
  → focused verifiers
```

Full command:

```powershell
.\scripts\run_task19_windows.ps1
```

Diagnostic command without the soak:

```powershell
.\scripts\run_task19_windows.ps1 -SkipSoak
```

A diagnostic pass cannot set `acceptance_ready=true`.

The full gate runs clean-head checks, compilation, repository hygiene, SQLite convergence checks, Examiner integrated and adversarial checks, full pytest, ET001–ET004, Ruff, Mypy, Bandit, root Wheel build, Wheel metadata and contamination checks, outside-repository installation, deployment bundle hash/roundtrip checks, and the 100-cycle workload. Reports belong under ignored `artifacts/task19/` and must identify the exact tested head.

## Stop rules

Keep PR #25 Draft when any mandatory command fails, times out, or is unavailable; when generated artifacts are tracked; when `task_spec` bypasses policy; when historical reuse is treated as a fresh intervention; when Provider Hub attaches itself to canonical planning; when exact-head evidence diverges; or when a requested change would require a parallel authority.

## Claim ceiling

Even after all gates pass, the strongest supported statement is:

> The exact tested WLS candidate passed its repository suite, evolution verifiers, clean Wheel installation, provenance integrity, and a bounded 100-cycle local workload with three runtime re-instantiations.

This does not establish production maturity, longitudinal reliability, owner-host benefit, unrestricted autonomy, consciousness, merge authorization, provider enablement, deployment authorization, or permission to start Task20.

## Current status

PR #25 is open, Draft, and `LOCAL_EXACT_HEAD_VERIFIED / CI_PENDING` for the latest locally verified branch head recorded in the PR body and ignored `artifacts/task19/TASK19_CONVERGENCE_<sha12>.json` report. Local Windows verification passed with `acceptance_ready=true`, and the final deployment ZIP passed hash verification plus install/verify/uninstall roundtrip. Merge and deployment remain closed until same-head CI concludes and the owner accepts the candidate.
