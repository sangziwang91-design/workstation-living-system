# Task19 Convergence Authority

## Status

`CANDIDATE / DRAFT / EXACT-HEAD LOCAL VERIFICATION REQUIRED`

Task19 is rebuilt from the current main lineage. GitHub-hosted Actions are not treated as available, and no absent check is interpreted as a pass. The authoritative acceptance entry point is:

```powershell
.\scripts\run_task19_windows.ps1
```

The command must run from a clean checkout of the exact candidate head. Generated reports belong under ignored `artifacts/task19/`.

## Integrated repair rounds

1. Atomic goal decomposition and one canonical task-spec contract.
2. Policy-before-reuse idempotency guard and durable outcome provenance.
3. Aggregate goal completion with one goal-state authority.
4. Recovery outcome isolation from current-cycle learning and attribution.
5. Final-plan-bound goal counterfactual attribution.
6. Loopback-only custom providers and current-probe-bound selection.
7. Durable atomic Provider Hub state replacement.
8. WAL-aware survival preflight with passive checkpoints.
9. Self-hosted workflows restricted to manual execution or main pushes.
10. Repository and pull-request authority consolidation.

## Mandatory gates

- compileall;
- repository-layout verifier;
- complete pytest suite;
- ET001, ET002, ET003, ET004;
- Ruff, Mypy, Bandit;
- root Wheel build;
- outside-repository clean Wheel installation and smoke;
- 100-cycle bounded non-idle workload with runtime re-instantiation after cycles 25, 50, and 75;
- clean working tree and no tracked runtime databases, keys, caches, Wheels, virtual environments, or soak homes.

Any failed, timed-out, or unavailable mandatory gate keeps the pull request Draft.

## Owner gate

Only the owner may merge, enable a provider, deploy to the live workstation, or authorize Task20. Passing tests alone performs none of those actions.

## Claim ceiling

A complete pass can establish only that the exact tested checkout passed its repository suite, evolution verifiers, clean Wheel installation, and bounded local workload. It cannot establish production readiness, indefinite reliability, owner-host benefit, unrestricted autonomy, external utility, or consciousness.
