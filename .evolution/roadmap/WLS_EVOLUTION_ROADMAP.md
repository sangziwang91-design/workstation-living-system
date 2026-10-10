# WLS Evolution Roadmap

This roadmap is an ordered candidate route, not authorization to implement every stage. Each stage becomes active only after the previous stage has evidence, an explicit task packet and owner approval.

## First-generation delivery milestone (G1) — evidence contract, 2026-10-10

This section is the **delivery decision** and supersedes the old candidate
sequencing below wherever the two differ. It is not a second WLS runtime,
dashboard, state authority, or long-term product redefinition. The user goal
remains a persistent multifunctional agent capable of measurable adaptation.
**G1 is the first genuinely useful, deliverable stage**, not unrestricted RSI.

Canonical tracking issue:
[#92](https://github.com/sangziwang91-design/workstation-living-system/issues/92).
Its four executable work packets are
[#93 real two-domain action](https://github.com/sangziwang91-design/workstation-living-system/issues/93),
[#94 owner-host 72h + private memory](https://github.com/sangziwang91-design/workstation-living-system/issues/94),
[#95 independent retained advantage](https://github.com/sangziwang91-design/workstation-living-system/issues/95),
and [#96 signed single-owner delivery](https://github.com/sangziwang91-design/workstation-living-system/issues/96).

### The only G1 critical path

| Order | Evidence/implementation task | Measurable done condition | What is *not* a pass |
|---|---|---|---|
| P0-A | Freeze exact source baseline and reviewer/evaluator authority; resolve write-token workflow security PR #91; merge verified owner-effect audit #90 | Linux/Windows 3.11/3.13 full tests and security checks on the **same candidate commit**; no critical open release security finding | previous branch green CI, user chat, PR created |
| P0-B | **One actual life-loop vertical slice** #93, then broaden to two non-isomorphic task domains | 10 authorized real tasks with goal→observation→safe action→independent verified outcome and at least one self-generated goal; negative no-action control | 10 fixtures, repeated noop, 10 PRs |
| P0-C | Real owner host, restart/recovery, private inheritance #94 | Windows installed exact wheel, 72 **wall-clock** hours, real restart/crash recovery, persistent owner context with no public egress, backup restore | 100 quick CI cycles, July installation claim, owner text uploaded to GitHub |
| P0-D | Frozen-model, same-budget, independent held-out task gain #95 | At least one previously failed task class now passed and reused after restart on fresh tasks, independent frozen arm, no critical regression or hidden evaluator leak | improved prompt aesthetic, memory retrieval count, scoring fixture tuned to solution |
| P1-E | One clean owner handoff #96 | Exact wheel/source digest, owner-visible status/stop, startup/upgrade/rollback, explicit limitations, stage release signoff | README says production or wheel builds only |

The order is a *dependency graph* not a number-of-iterations target.
Do not start a new general-purpose module simply to fill a cycle. Work
on exactly the highest-impact blocking edge; where two edges are truly
independent, keep their evidence separated. Stop adding more capabilities
once they do not change observed real-task success or long-run continuity.

### Completion accounting that cannot inflate on paper

**G1 release score:** 6 binary release gates in #92. A gate changes to PASS
only with current source SHA, execution environment, timestamps, independent
evidence URL or private owner-host receipt, and failure controls. G1 is
**RELEASED only at 6/6**. Current initial evidence: **0/6 G1 release-verified**,
because prior CI, fixture tests and old host receipts do not verify the full
October candidate on one real owner host. This does **not** mean the WLS code
base is 0% implemented; existing ET001-003, owner-only memory ablation, and
PRs #83-#85 are real partial prerequisites.

Track four **different** status columns rather than asserting a subjective
single total-completion percentage:
1. **CODED / CI_VERIFIED** — source and exact-head controlled tests exist.
2. **HOST_VERIFIED** — the exact wheel executed tasks on the intended host.
3. **LONGITUDINAL_VERIFIED** — independently audited elapsed-time survival,
   restart and changing external tasks, not simulated loop count.
4. **LEARNING_VERIFIED** — frozen-model equal-budget independent task advantage,
   post-restart skill transfer and rollback.

No proof is `UNKNOWN`, a complete negative experiment is `NO_GAIN`,
a failing security gate is `BLOCKED`. Neither UNKNOWN nor NO_GAIN is
silently converted to PASS. Evidence files may be private; public GitHub
receives only non-sensitive digest/count/status proofs.

### G1 completion, G2 research, five-year ultimate target

- **G1:** a single-owner self-running bounded organism on one workstation
  with limited reproducible beneficial learning.
- **G2:** multiple real task families, weeks of stable autonomously selected
  work, stronger independent model/procedure adaptation, private evidence
  retained across model upgrades.
- **G3/RSI-C/D research:** repeated portable transfer and a causally supported
  evolving improver beating a **frozen improver** under equal inference
  budgets and independent new tasks. Not claimed by G1.
- No claim of subjective consciousness, open-ended safe self-editing, or AGI
  follows from passing any engineering release gate.

**Stop condition for G1 engineering:** ship the candidate when all six
release gates pass; stop scope expansion if an iteration produces no measured
new task capability or essential reliability improvement. The broader WLS
research may continue separately; G1 must not be held hostage to solving
consciousness or general intelligence.

## Current canonical position

- ET001 — bounded failure-to-skill growth lifecycle: merged and controlled local/CI verified; owner-host longitudinal proof pending.
- ET002 — bounded local cognition: merged and controlled local/CI verified.
- ET003 — causal memory retrieval and measurable controlled advantage: merged; production-scale and owner-host longitudinal proof pending.
- ET004 — persistent goal system and long-horizon continuity: existing draft PR #8 is the sole in-flight implementation branch.
- Repo-native evolution control plane: bootstrap in `evolution-infra-001-repo-native-chain`.

## Route A — finish the functional organism

### ET004 · Persistent Goal System / Long-Horizon Continuity

Required outcomes:

- durable goal lifecycle and goal debt ledger
- bounded decomposition and dependency representation
- pause, resume, revise, abandon and completion semantics
- explicit goal-to-cognition and goal-to-action attribution
- interruption and restart continuity
- frozen goal-disabled comparison
- no parallel planner or goal authority

### ET005 · Homeostasis and Capacity Governor

Purpose: add one cross-cutting resource contract to the canonical `LivingSystem` rather than separate loops in every module.

Candidate controlled budgets:

- perception event intake and storm suppression
- active attention slots
- planning depth, branches, time and no-progress rounds
- tool calls, retries and side-effect budget
- API/token/cost/latency budget
- memory write, active-memory and consolidation budget
- skill candidates, promotions and active-skill capacity
- cooldown, circuit breaker and safe dormancy

Acceptance requires measurable enforcement, evidence, deterministic degradation and restart continuity. Capacity exhaustion must never bypass policy or owner gates.

### ET006 · Provider-Independent Cognitive Organ Gateway

Purpose: make external models replaceable cognitive organs while local WLS state retains identity and authority.

Bootstrap policy:

- ChatGPT remains the only ACTIVE code worker.
- Provider output is always a candidate, never canonical fact by itself.
- model selection uses task capability, risk, cost, latency, historical success and uncertainty
- provider outage causes safe degradation, not identity loss
- secrets remain outside packets and model-visible evidence

Gemini, Claude, GLM and DeepSeek remain PENDING until a separate admission target provides at least two real-task proofs, scope discipline, cost evidence and rollback.

### ET007 · Owner-Host Longitudinal Organism Proof

Purpose: close the gap between controlled CI fixtures and genuine operation on the intended Windows host.

Minimum chain:

```text
real observation
-> world-state update
-> testable prediction
-> durable goal
-> causal memory retrieval
-> governed action
-> observed result
-> repeated failure recognition
-> recovery and skill candidate
-> frozen baseline
-> owner approval
-> promotion and later real reuse
-> retain / revise / rollback / retire
-> restart continuity
```

One successful case proves one bounded real-host lifecycle, not general long-term adaptation. Repeated non-isomorphic cases and learning-on versus frozen learning-off evidence are required for a longitudinal claim.

## Route B — use platform model labor without API dependence

### MLW-001 · GPT-Primary Repo-Native Operation

- ChatGPT reads GitHub state, claims one bounded packet, creates a branch and PR, reads CI, repairs and hands off.
- High-capability mode performs target decomposition, architecture decisions and final review.
- Degraded mode performs deterministic microtasks only.
- conversations are disposable; packets, commits and evidence persist.

### MLW-002 · Notion AI Semantic Operations

Notion AI becomes a deliberately bounded slow-memory organ:

- pre-run historical retrieval
- contradiction audit between Notion narrative and GitHub truth
- packet enrichment with `NOTION_CONTEXT`
- post-merge semantic archive
- future curriculum candidate mining

It does not edit code, approve changes or create VERIFIED engineering claims.

### MLW-003 · Multi-Worker Admission — PENDING

Potential workers: Gemini, Claude, GLM, DeepSeek or future providers.

Admission is optional. Each candidate must use the same packet format and branch isolation. Evaluation measures success, regressions, diff scope, repair rounds, cost, latency and evidence integrity. No worker is activated solely because a vendor offers free or discounted access.

## Route C — grow a local developmental model

This route does not attempt to reproduce a general internet-trained LLM. It builds a software-native local cognition stack from verified longitudinal experience.

### DM-001 · Developmental Trajectory Contract

Persist lossless samples:

```text
state_before
+ observation
+ goal and retrieved memory
+ selected action or code change
+ prediction
+ state_after
+ tool and test outcome
+ cost and duration
+ reviewer decision
+ later retain / rollback result
```

API/model proposals are candidate annotations. Runtime, compiler, tests, GitHub and owner-confirmed outcomes provide training truth.

### DM-002 · Curated Software Curriculum

Ordered stages:

1. basic communication and explicit uncertainty
2. program primitives and executable toy tasks
3. micro-repositories
4. repository topology, tests and dependency graphs
5. commit / issue / PR temporal evolution
6. bounded issue repair
7. long-term maintenance and regression avoidance
8. governed action in WLS-controlled environments

GitHub repositories are schools, not text to ingest indiscriminately. Licensing, buildability, test quality, evolution history and security determine admission.

### DM-003 · Local Action World Model

Train a narrow model from scratch to estimate:

```text
state + action -> next state, success, failure class, risk, cost, duration, uncertainty
```

Start with success/failure prediction, then candidate-action comparison and short bounded rollout. The model must outperform simple frozen baselines and remain calibrated under restart and distribution change.

### DM-004 · Local Software Language Core

Only after enough curated data exists, train or adapt a small language/semantic model for:

- task-language understanding
- repository and test-output interpretation
- structured hypotheses and plans
- bounded code generation
- evidence and uncertainty reporting

Stable abstractions may enter weights. volatile repository facts remain in indexed memory and graphs.

### DM-005 · External-to-Local Distillation

Repeated external-model tasks can be internalized only when real outcomes validate the answer. Candidate checkpoints follow the same lifecycle as skills:

```text
candidate training
-> frozen baseline comparison
-> regression and calibration suite
-> owner approval
-> promotion
-> longitudinal post-measurement
-> retain or rollback
```

The local model may become highly capable inside the owner's software ecosystem without claiming general intelligence.

## Stop rules

Stop the roadmap at the current verified stage when any proposal would:

- create another canonical runtime, memory store, planner or goal authority
- bypass evidence provenance, policy, owner approval or rollback
- activate a provider without an admission packet
- treat Notion narrative or model output as implementation evidence
- train on unlicensed, secret-bearing or unverified data
- train a model on its own output as truth
- expand scope because a model has unused context or quota
- claim consciousness, AGI, unlimited self-modification or production autonomy

## Product horizon

If the route succeeds, WLS becomes an owner-governed, local-first adaptive agent runtime with replaceable external cognitive organs and an increasingly capable local software-world model. Its strongest defensible value is persistent, auditable adaptation in a bounded software environment—not biological life or universal intelligence.