# Task19 Main-Based Convergence

## Decision

Task19 is rebuilt from authoritative `main` after `LOCAL-RUNNER-002`. Historical PR #22 diverged before the packaging and Windows-runner convergence and is therefore retained as source material rather than used as the merge base.

## Integrated candidate scope

- bounded Survival Supervisor with explicit, validated daemon budgets;
- persistent goal lifecycle, decomposition, debt, review, interruption recovery, and local ablation;
- schema-validated `task_spec` translation into the existing cognitive planner and policy/tool path;
- write-free goal-free comparison using the existing bounded cognitive ranker;
- explicit action outcome provenance for current execution, historical reuse, durable recovery, and no observable outcome;
- candidate-only Provider Hub with SSRF controls, no redirects, response limits, secret-vault fail-closed behavior, and network probes outside the global state lock;
- deterministic root build, clean Wheel installation, exact-head reporting, and a 100-cycle non-idle bounded workload with runtime re-instantiation after cycles 25, 50, and 75.

## Rejected or deferred material

- stale Wheels and build metadata;
- committed soak homes, databases, keys, and caches;
- browser Provider UI until a complete authenticated loopback interface has an independent threat model;
- reports tied to historical or mismatched heads;
- GitHub-hosted workflow dependence while capacity is unavailable;
- automatic attachment of the Provider Hub to the canonical planner;
- Task20 causal shadow integration.

## Verification surfaces

Primary offline entry point on Windows:

```powershell
.\scripts\run_task19_windows.ps1
```

The wrapper creates an isolated environment, installs the root project with development and provider extras, and runs:

```text
compileall
repository-layout verifier
full pytest suite
ET001 / ET002 / ET003 / ET004
Ruff
Mypy
Bandit
root Wheel build
outside-repository clean install
100-cycle bounded workload with three runtime re-instantiations
```

Reports are written under `artifacts/task19/` and are intentionally ignored by Git. A failed or unavailable mandatory gate keeps the pull request Draft.

## Claim ceiling

Passing this gate can establish exact-head local behavior for the tested checkout, clean Wheel, and bounded 100-cycle workload. It cannot establish production maturity, multi-day or indefinite reliability, owner-host benefit, external utility, consciousness, unrestricted autonomy, or permission to merge, enable, or deploy.
