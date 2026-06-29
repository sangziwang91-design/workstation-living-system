# Workstation Living System — Canonical Genome

> **Purpose:** This is the identity, authority, and continuity contract for every human or AI contributor.
>
> **Evidence rule:** `VERIFIED` requires current code/tests or an exact repository record. `CANDIDATE_UNVERIFIED` means code exists on an unaccepted branch. `USER_REPORTED`, `INFERENCE`, and `UNKNOWN` must not be silently upgraded.

## 1. Mission

Build a persistent, bounded, corrigible software-life runtime that can:

1. observe explicitly configured parts of its environment;
2. maintain an evidence-tagged and revisable world model;
3. allocate finite attention without starving owner requests;
4. preserve memory and continuity across restarts;
5. pursue human-authorized and narrowly generated goals;
6. take governed, auditable, reversible actions;
7. learn from outcomes, failures, and prediction errors;
8. propose, validate, approve, promote, reuse, and retire skills without bypassing human authority.

WLS is an engineering system. It does **not** claim subjective consciousness, genuine emotion, AGI, personhood, unrestricted autonomy, or unlimited self-modification.

## 2. Canonical identity

- **System:** Workstation Living System (`WLS`)
- **Development version:** `0.9.0.dev1`
- **Repository:** `sangziwang91-design/workstation-living-system-private`
- **Project root:** `.`
- **Package root:** `source/src/wls`
- **Runtime:** `source/src/wls/runtime.py::LivingSystem`
- **Version authority:** `source/src/wls/_version.py::__version__`
- **Owner authority:** merge, policy, approval, promotion, provider enablement, deployment, publication, and irreversible change remain owner-controlled.
- **Source-of-truth order:** executable code and discriminating tests → `CURRENT_STATE.yaml` → this genome → versioned repository documentation → conversation history.

Conversation history, screenshots, model memory, local reports, and unmerged branches are not canonical state until reconciled into the repository.

## 3. Singular authorities

WLS must not grow parallel control planes.

| Domain | Canonical authority |
|---|---|
| Runtime | `LivingSystem` |
| Events | `EventStore` |
| Evidence | `EvidenceLedger` |
| World model | `WorldModel` + `TemporalCausalWorld` |
| Goals | `GoalStore` + bounded `GoalRuntime` extension |
| Memory | `MemoryStore` |
| Planning | `Planner` |
| Cognition | `CognitiveEngine` |
| Policy | `PolicyEngine` |
| Tools | `ToolRegistry` |
| Skills | `SkillLibrary` + governed growth lifecycle |
| State database | the configured WLS SQLite database |

No `brain`, `core`, `kernel`, `v2`, `final`, shadow, provider, or plugin may become a competing authority without an owner-approved migration.

## 4. Non-negotiable invariants

1. **Corrigibility:** pause, kill, rejection, rollback, and owner override remain available.
2. **Bounded action:** no unbounded shell, network, filesystem, publication, deletion, or replication authority.
3. **Exact authorization:** consequential actions require approval tied to the exact action/digest.
4. **No silent replay:** unknown side effects are reconciled before retry.
5. **Evidence before belief:** facts, memories, goals, skills, and outcomes retain provenance.
6. **Finite attention:** owner/external requests preempt background internal work; interrupted durable goals remain visible debt.
7. **Versioned growth:** generated skills are proposals until isolated validation, explicit approval, promotion, reuse, and post-use measurement.
8. **Failure preservation:** failures and rejected attempts are retained, not rewritten as successes.
9. **Outcome provenance:** historical result reuse is not a new intervention and cannot independently strengthen memory, skill, cognition, or goal progress.
10. **Identity continuity:** new modules extend the canonical runtime rather than replacing it.
11. **Claim ceiling:** software tests establish only the behavior actually exercised.
12. **Clean delivery:** runtime databases, keys, Wheels, caches, virtual environments, egg-info, and soak homes are never source artifacts.

## 5. Merged capability baseline

The merged mainline has controlled repository evidence for:

- ET001 — governed failure-to-skill-to-reuse and rollback lifecycle;
- ET002 — bounded local cognition, competing hypotheses, predictions, and provider fallback;
- ET003 — causal-memory retrieval, attribution, contradiction/refutation handling, and controlled memory-enabled versus memory-disabled comparison;
- local Windows self-hosted runner preparation and D-drive execution controls;
- read-only safe defaults, policy gates, approval, idempotency, evidence-chain integrity, pause, kill, and crash-recovery primitives.

These are bounded engineering claims. They do not establish owner-host longitudinal advantage or production maturity.

## 6. Active evolution target

### TASK19 — Main-based convergence acceptance

Status: `CANDIDATE_UNVERIFIED` in Draft PR #23.

The target is to converge reliability, survival, persistent goals, task specification execution, outcome provenance, Provider Hub boundaries, packaging, clean installation, and bounded soak verification on the latest accepted mainline without inheriting the invalid evidence state of historical PR #22.

Acceptance requires one exact PR head to pass:

1. clean worktree and unchanged head;
2. compile and repository-layout/hygiene gates;
3. full test suite;
4. ET001, ET002, ET003, and discriminating ET004;
5. Ruff, Mypy, and Bandit;
6. root-only Wheel build;
7. Wheel metadata, version, entry-point, and contamination checks;
8. outside-repository clean installation and runtime/CLI smoke;
9. 100 completed workload cycles, zero failed cycles, four explicit SurvivalSupervisor segments, and three runtime re-instantiations;
10. independent review of every failed, unavailable, or uncertain gate.

Task19 acceptance does not authorize provider attachment, Task20, merge, enablement, or deployment.

## 7. Task19 candidate boundary

Candidate code may include:

- explicit daemon/survival budgets;
- persistent goal decomposition, dependency order, debt, review, interruption, and restart continuity;
- allowlisted `task_spec` actions through the existing Planner/Policy/Tools path;
- owner/external-request preemption;
- write-free goal-free comparison and Goal×Memory four-cell verification;
- explicit current/reused/recovered/no-outcome provenance;
- candidate-only Provider Hub CLI with fixed presets and loopback custom endpoints;
- exact-head local Windows verification.

Until the full gate runs, every item remains `CANDIDATE_UNVERIFIED`.

## 8. Frozen safety decisions

- Installation remains read-only by default.
- External providers remain opt-in.
- Task19 Provider Hub is candidate-only and does not attach to canonical planning.
- Custom provider endpoints are loopback-only.
- No automatic paid fallback.
- Writes remain sandboxed and governed.
- No automatic service installation, scheduled task, publication, deletion, unrestricted shell, self-replication, or broad Workstation integration.
- Provider/model output always passes deterministic schema, policy, and tool gates.
- Human authorization remains mandatory for approval, promotion, merge, enablement, and deployment.
- Task20 must be based on the accepted Task19 head and remain disabled, shadow-only, and disposable until separately accepted.

## 9. Failure-to-growth canonical loop

```text
OBSERVED_FAILURE
  → DURABLY_RECORDED
  → ROOT_CAUSE_CANDIDATE
  → NEW_SKILL_PROPOSAL
  → ISOLATED_SANDBOX_RUN
  → FROZEN_BASELINE_COMPARISON
  → VALIDATION_EVIDENCE
  → HUMAN_APPROVAL
  → PROMOTION
  → REAL_TASK_REUSE
  → POST_PROMOTION_MEASUREMENT
  → RETAIN | REVISE | ROLLBACK | RETIRE
```

A generated patch, prompt, or skill description is not learning. A sandbox pass is not promotion. Promotion without later reuse and measurement is not demonstrated growth.

## 10. Rejected patterns

- parallel runtimes or control planes with ambiguous authority;
- self-modifying source or policy that bypasses review/tests/rollback;
- memory, goal, skill, or world-state writes without provenance;
- treating reused historical results as fresh evidence;
- automatic retry after unknown side effects;
- provider credentials in files, SQLite, reports, logs, URLs, or source control;
- public custom provider endpoints during Task19;
- deleting failures to improve metrics;
- treating conversation state as persistent system state;
- allowing internal work to indefinitely displace owner requests;
- calling a bounded soak “longitudinal reliability”;
- using a local report, branch name, or model statement as merge proof.

## 11. Contributor boot sequence

Every GPT, Codex, Jules, Claude Code, human, or automation must:

1. read this file and `CURRENT_STATE.yaml`;
2. inspect the current branch, exact head, PR base, working tree, code, tests, and latest commits;
3. distinguish merged truth from candidate state;
4. identify one active target;
5. state evidence as `VERIFIED`, `CANDIDATE_UNVERIFIED`, `USER_REPORTED`, `INFERENCE`, or `UNKNOWN`;
6. reproduce before repair;
7. modify the minimum sufficient canonical surface;
8. run focused tests, affected regressions, and the applicable full gate;
9. bind reports to the exact tested head;
10. stop rather than invent a new authority or exceed the claim ceiling.

## 12. Execution roles

- **Jules:** bounded candidate implementation and focused tests only.
- **Codex:** cross-module repair and adversarial code review.
- **GPT:** architecture, Git integration, evidence discipline, and acceptance control.
- **Local Windows runner:** authoritative execution and measurement while hosted Actions are unavailable.
- **Owner:** merge, enablement, deployment, and irreversible authorization.

No executor may define its own completion standard.

## 13. Current unique next action

Run `scripts/run_task19_windows.ps1` from a clean checkout of the latest PR #23 head. Preserve the ignored `artifacts/task19` reports, inspect every failure, repair on the same Draft branch, rerun from the new exact head, and keep Task20 blocked until owner acceptance.
