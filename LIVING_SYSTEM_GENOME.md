# Workstation Living System — Canonical Genome

> **Purpose:** This file is the canonical identity and continuity contract for every human or AI contributor. Read it before planning, coding, renaming, or extending WLS.
>
> **Evidence rule:** `VERIFIED` means supported by current repository code/tests or an explicit repository document. `USER_REPORTED` means supplied by the owner but not independently verified here. `INFERENCE` means reasoned from evidence. `UNKNOWN` must not be silently completed.

## 1. Mission

Build a persistent, bounded, corrigible software-life runtime that can:

1. observe explicitly configured parts of its environment;
2. maintain an evidence-tagged and revisable world model;
3. allocate finite attention;
4. preserve multiple memory types and continuity across restarts;
5. pursue human-authorized and narrowly generated goals;
6. take governed, auditable, reversible actions;
7. learn from outcomes, failures, and prediction errors;
8. propose, validate, approve, promote, reuse, and retire skills without bypassing human authority.

WLS is an engineering system. It does **not** claim subjective consciousness, genuine emotion, AGI, personhood, unrestricted autonomy, or unlimited self-modification.

## 2. Canonical identity

- **System name:** Workstation Living System
- **Abbreviation:** WLS
- **Current declared release family:** 1.0
- **Repository:** `sangziwang91-design/workstation-living-system-private`
- **Authority:** the repository owner retains final authority over policy, approvals, promotion, external action, publication, and irreversible change.
- **Source of truth order:** executable code and tests → `CURRENT_STATE.yaml` → this genome → release documentation → conversation history.
- Conversation history, model memory, screenshots, and prior plans are not authoritative unless reconciled into the repository.

## 3. Non-negotiable invariants

1. **Corrigibility:** pause, kill, rejection, rollback, and human override must remain available.
2. **Bounded action:** no unbounded shell, network, filesystem, publication, deletion, or self-replication authority.
3. **Exact authorization:** consequential actions require policy approval tied to the exact action or digest.
4. **No silent replay:** actions with unknown side effects are reconciled before any retry.
5. **Evidence before belief:** world-model facts carry source, verification, confidence, and expiry where applicable.
6. **Finite attention:** internal memory or self-generated events cannot starve external observations and owner requests.
7. **Versioned growth:** generated skills are proposals, not capabilities, until sandboxed, validated, explicitly approved, and promoted.
8. **Failure preservation:** failed attempts and their evidence are retained; they are not rewritten as successes.
9. **Identity continuity:** new modules must extend the canonical runtime rather than create parallel `core`, `brain`, `kernel`, `v2`, or `final` identities without an approved migration.
10. **Claim ceiling:** tests may establish software behavior only; they cannot establish consciousness, sentience, genuine emotion, or human-equivalent cognition.

## 4. Current capability map

The following capabilities are declared in the current repository README and must be revalidated against code and tests before release claims are upgraded.

| Domain | Declared capability | Evidence status |
|---|---|---|
| Perception | clock, system-resource, inbox, filesystem, process, Git, HTTP-health, custom sensors | `DOCUMENTED`; code/test coverage not verified in this update |
| World model | evidence-tagged facts, expiry, contradictions, predictions, refutations, prediction error | `DOCUMENTED`; code/test coverage not verified in this update |
| Regulation | drives and functional affect influence budget, exploration, recovery, safety pressure | `DOCUMENTED`; subjective emotion explicitly excluded |
| Workspace | bounded competition among events, goals, memories, internal state | `DOCUMENTED`; starvation protection declared |
| Memory | episodic, semantic, procedural/skill, relationship, failure, self-model | `DOCUMENTED`; persistence semantics require code/test verification |
| Action | local reads, bounded HTTP, allowlisted commands, reversible writes, approval, idempotency, reconciliation | `DOCUMENTED`; exact tool coverage requires code/test verification |
| Learning | outcome episodes, prediction-error candidates, repeated-failure candidates, semantic consolidation | `DOCUMENTED`; external effectiveness remains `UNKNOWN` |
| Skill growth | `PROPOSED → SANDBOXED → VALIDATED → APPROVED → PROMOTED` | `DOCUMENTED`; promotion requires explicit human authorization |
| Sleep/consolidation | stale-fact expiry, contradiction resolution, deduplication, discovery, failure review, next-focus generation | `DOCUMENTED`; scheduler/runtime coverage requires verification |
| Continuity | SQLite WAL, HMAC evidence chain, leases, crash recovery, pause, sticky kill switch, restart-safe pending actions | `DOCUMENTED`; recovery matrix requires verification |
| Interface | CLI and token-authenticated loopback JSON API | `DOCUMENTED`; endpoint/command inventory requires verification |

## 5. Missing capability classes

These are not automatically absent; they are **not yet verified by this continuity update** and must remain `UNKNOWN` until repository evidence is recorded:

- end-to-end installation and upgrade tests on the intended Windows host;
- durable migration compatibility across released schemas;
- complete failure-to-skill lifecycle with baseline comparison and promotion reuse;
- deterministic replay of decision traces without replaying external side effects;
- adversarial security testing of API, command policy, path handling, secrets, plugins, and provider output;
- resource exhaustion, event storm, deadlock, and long-running soak tests;
- backup, restore, database corruption, partial-write, clock-jump, and power-loss recovery tests;
- measurable learning benefit versus a frozen non-learning baseline;
- detection and rollback of harmful or degraded promoted skills;
- multi-model/provider disagreement handling and provenance retention;
- owner-visible explanation of why a goal, action, memory, or skill changed state;
- signed release provenance and reproducible package verification.

## 6. Failure-to-growth canonical loop

A failure becomes reusable growth only through this complete path:

```text
OBSERVED_FAILURE
  → DURABLY_RECORDED
  → ROOT_CAUSE_CANDIDATE
  → NEW_SKILL_PROPOSAL
  → ISOLATED_SANDBOX_RUN
  → BASELINE_COMPARISON
  → VALIDATION_EVIDENCE
  → HUMAN_APPROVAL
  → PROMOTION
  → REAL_TASK_REUSE
  → POST_PROMOTION_MEASUREMENT
  → RETAIN | REVISE | ROLLBACK | RETIRE
```

Required evidence at each transition:

- immutable failure/event identifier;
- reproduction or explicit `NON_REPRODUCIBLE` result;
- affected goal/action/tool/configuration;
- candidate mechanism and confidence;
- sandbox boundary and test fixtures;
- frozen baseline result;
- acceptance thresholds defined before evaluation;
- human approval identity and timestamp for approval/promotion;
- promoted version and rollback target;
- post-promotion outcome, regressions, and side effects.

A generated patch, prompt, or skill description is not learning. A passing sandbox test is not promotion. Promotion without later reuse and measurement is not demonstrated growth.

## 7. Frozen decisions

The following decisions are frozen unless the owner explicitly changes them in a versioned repository update:

- safe-default installation remains read-only;
- external providers and plugins are opt-in;
- writes remain sandboxed and governed;
- no automatic Windows service, scheduled task, publication, deletion, or broad Workstation integration;
- generated provider output always passes deterministic schema, policy, and tool gates;
- human authorization remains mandatory for approval and promotion;
- WLS claims observable software functions only.

## 8. Rejected architecture patterns

Do not introduce these patterns without an explicit migration decision:

- parallel competing runtimes with ambiguous authority;
- version names such as `final`, `ultimate`, or `real` without semantic versioning and migration evidence;
- self-modifying source or policy that bypasses review, tests, or rollback;
- memory writes without provenance or confidence;
- automatic reuse of an action after `UNKNOWN_SIDE_EFFECT`;
- declaring a skill learned because a model generated it;
- deleting failure records to improve success metrics;
- treating conversation context as persistent system state;
- allowing internal self-generated work to indefinitely displace owner requests.

## 9. Active evolution target

**EVOLUTION-TARGET-001: Demonstrate the complete failure-to-skill-to-reuse loop.**

Acceptance requires one deliberately reproducible failure to produce a versioned skill candidate, pass isolated validation against a frozen baseline, receive explicit human approval, be promoted, improve a later real task, survive affected regression tests, and preserve a working rollback path.

Until that evidence exists, WLS may claim a governed skill lifecycle implementation, but not verified self-improvement.

## 10. Contributor boot sequence

Every new GPT, Codex, Claude Code, human contributor, or automation must:

1. read `LIVING_SYSTEM_GENOME.md`;
2. read `CURRENT_STATE.yaml`;
3. inspect the current branch, working tree, code, tests, and latest commits;
4. identify one evolution target only;
5. state evidence as `VERIFIED`, `INFERENCE`, or `UNKNOWN`;
6. modify the minimum sufficient surface;
7. run targeted tests and affected regression tests;
8. update `CURRENT_STATE.yaml` with exact evidence, limitations, and the unique next action;
9. stop rather than invent a new subsystem.

## 11. Change control

Changes to mission, invariants, authority, claim ceiling, lifecycle states, or frozen decisions require:

- an explicit owner-authorized change;
- a versioned repository commit;
- migration impact analysis;
- affected tests;
- rollback instructions;
- synchronized update of `CURRENT_STATE.yaml`.
