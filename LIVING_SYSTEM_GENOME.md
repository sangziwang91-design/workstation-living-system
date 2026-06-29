# Workstation Living System — Canonical Genome

## Identity

- Repository: `sangziwang91-design/workstation-living-system-private`
- Project root: `.`
- Package root: `source/src/wls`
- Runtime: `source/src/wls/runtime.py::LivingSystem`
- Version authority: `source/src/wls/_version.py::__version__`
- Evidence order: executable code and tests → `CURRENT_STATE.yaml` → this file → other documentation.
- Owner controls merge, approval, enablement, deployment, and irreversible change.

WLS is a bounded engineering system. It makes no claim of consciousness, personhood, AGI, genuine emotion, or unrestricted autonomy.

## Mission

WLS may observe configured environments, maintain an evidence-tagged world model, preserve continuity across restarts, pursue authorized goals, take governed actions, learn from measured outcomes, and grow reviewed skills.

## Singular authorities

| Domain | Authority |
|---|---|
| Runtime | `LivingSystem` |
| Events | `EventStore` |
| Evidence | `EvidenceLedger` |
| World model | `WorldModel` + `TemporalCausalWorld` |
| Goals | `GoalStore` + bounded `GoalRuntime` |
| Memory | `MemoryStore` |
| Planning | `Planner` |
| Cognition | `CognitiveEngine` |
| Policy | `PolicyEngine` |
| Tools | `ToolRegistry` |
| Skills | `SkillLibrary` |
| State | configured WLS SQLite database |

No parallel runtime, planner, goal, memory, policy, provenance, or acceptance authority may be introduced without an owner-approved migration.

## Invariants

1. Pause, kill, rejection, rollback, and owner override remain available.
2. Actions remain bounded, governed, and auditable.
3. Current policy and exact action contracts outrank historical result reuse.
4. Facts, memories, goals, skills, and outcomes retain provenance.
5. Owner requests preempt background work; interrupted goals remain visible debt.
6. Historical reuse is not a fresh intervention and cannot independently strengthen learning or goal progress.
7. Generated runtime data and build output are not source artifacts.
8. One public Windows command calls one Python verification orchestrator.
9. Tests establish only the behavior actually exercised.

## Merged baseline

The accepted mainline has bounded evidence for ET001, ET002, ET003, local Windows runner preparation, read-only defaults, policy gates, approval, idempotency, evidence integrity, pause, kill, and crash recovery.

## Active target

Task19 is `CANDIDATE_UNVERIFIED` in open Draft PR #25 on branch `task19-clean-squash`.

PRs #22, #23, and #24 are closed, unmerged audit records. PR #25 is the only active Task19 integration surface and contains one main-based candidate commit.

Retained candidate value:

- bounded survival supervision;
- atomic persistent goals and task specifications;
- policy-before-reuse and durable action provenance;
- owner-request preemption;
- controlled Goal and Memory ET004 comparison;
- candidate-only loopback Provider Hub;
- root package, Wheel, clean-install, repository, and bounded-soak verification.

Removed pollution:

- duplicate provenance implementation and tests;
- duplicate PowerShell and Python acceptance layers;
- dead execution wrapper;
- multiple active Task19 pull requests;
- development commit history from the active candidate.

## Acceptance path

```text
scripts/run_task19_windows.ps1
  → source/scripts/verify_task19_convergence.py
  → focused verifiers
```

The exact PR head must pass a clean worktree check, compilation, repository hygiene, full pytest, ET001–ET004, Ruff, Mypy, Bandit, root Wheel checks, outside-repository installation, and the 100-cycle workload.

Task19 acceptance does not authorize merge, provider attachment, deployment, or Task20.

## Contributor sequence

1. Read this file and `CURRENT_STATE.yaml`.
2. Inspect the exact branch, head, base, worktree, code, and tests.
3. Distinguish merged truth from candidate state.
4. Reproduce before repair.
5. Modify the minimum canonical surface.
6. Run focused and full applicable verification.
7. Bind reports to the exact tested head.
8. Stop rather than create a duplicate authority or unsupported claim.

## Roles

- Jules: bounded implementation and focused tests.
- Codex: cross-module repair and review.
- GPT: architecture, Git integration, cleanup, and acceptance control.
- Local Windows runner: authoritative execution while hosted Actions are unavailable.
- Owner: merge, enablement, deployment, and irreversible authorization.

## Unique next action

Run `scripts/run_task19_windows.ps1` from a clean checkout of the current PR #25 head, preserve ignored evidence under `artifacts/task19`, and repair only observed failures. Task20 remains blocked pending owner acceptance.
