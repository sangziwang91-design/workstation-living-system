# WLS Evolution Roadmap

This roadmap is an ordered candidate route, not authorization to implement every stage. Each stage becomes active only after the previous stage has evidence, an explicit task packet and owner approval.

## 2026-11-09 current execution track: REAL MODEL SELF-CODE (R5)

This section overrides any stale *status* prose below; the original genome,
release gates and approval boundaries remain authoritative. Do not count
new modules, role lists, prompts, CI greens or generation counters as measured
cognitive improvement. **One primary objective:** demonstrate a fixed,
locally inferred Qwen improving the *existing WLS agent's code/behavior*
through independent evaluation, feedback inheritance and a repeatable control.

### Exact inspected baseline (2026-11-09)

- Main `375b45b7afcca5535bc9b4c3dd2e0993f44a4460`; no active or queued
  Actions at inspection. Open draft PRs: #137, #138, #139, #141, #142.
  They are NOT merged or validated as intelligence gains merely because
  exact-head type/full-suite CI is green.
- Genuine pinned Qwen2.5-Coder 1.5B experiment:
  [run 42486361106](https://github.com/sangziwang91-design/workstation-living-system/actions/runs/42486361106)
  made 2 actual model calls on the same frozen WLS `SkillCompiler` defect.
  [Independent C1 replay 42487306662](https://github.com/sangziwang91-design/workstation-living-system/actions/runs/42487306662)
  admitted **1 model-authored distinct complete source**, yielded **0
  independently passing changed sources**. Unguided JSON was malformed;
  guided candidate changed validation evidence but failed mixed-pass/fail
  behavior. No code self-improvement verified, no held-out transfer.
- [Windows cross-run 42512132492](https://github.com/sangziwang91-design/workstation-living-system/actions/runs/42512132492)
  reports generation 60 across 61 distinct runs and ~706.6 cumulative
  generation hours, but `autonomous_skill_gain_proven=false`.
  Its Ruff lint repair is mechanical, NOT Qwen intelligence.
- Artifact privacy/provenance incident: historical public experimental
  artifacts include ephemeral HMAC `evidence.key` and `state.sqlite`.
  Possession of that key defeats independent authenticity claims about the
  corresponding bundled HMAC receipts. Do not reuse those signed bundles
  as independent proof of trust. PR #142 now restricts future uploads to
  reports, exact candidate source and manifests; **historical artifacts
  remain exposed until separately reviewed**. No implicit deletion.
- PR #142 now freezes the historic failing WLS source in
  `source/tests/fixtures/rsi_skill_compiler_20261108.py.txt`, checked
  against git blob `d86bb4f7cab2202dbaabdb50e016856db51b7828`.
  This prevents subsequent fixes to the real `skill_compiler.py` (PR #139)
  from silently changing/invalidating the controlled research baseline.

### Ordered work packages, no parallel runtime

| Order | Existing owner/target | Verifiable exit criterion | Status |
| --- | --- | --- | --- |
| 0 | #142: C1 report/Artifact export | No HMAC keys, SQLite state, paid tokens or credentials in any new downloadable experimental Artifact. Original failed model examples remain inspectable; exact-head CI passes. | CODED; latest CI/Artifact check pending |
| 1 | #142: original Qwen and replay | Exact model/model SHA, old source blob, full model output or digest, per-case C1 behavior and baseline, true model call count and no gain explicitly preserved. | MEASURED NEGATIVE (2 calls; 1 code candidate; 0 passes) |
| 2 | #139: live `SkillCompiler` | Merge the verified false-positive skill-validation fix into canonical runtime **after** frozen fixture is independent; test missing/extra/mixed failures and no regression. Re-read current main and PR SHA first. | PR GREEN; LIVE FIX NOT MERGED |
| 3 | Existing `RsiModelCandidateBuilder`, `RsiEvolutionPilot`, C1 | From the archived model-authored partial repair, run a *new genuine Qwen inference* G2 with public failure **summary only**; compare against equal-budget fresh-baseline restart. Preserve source bytes, tests, tokens, wall time, model SHA and parent lineage. No evaluator content in either prompt. | UNVERIFIED; NEXT KEY EXPERIMENT |
| 4 | Same fixed model and evaluator | At least one genuinely changed G2 source passes the frozen behavior plus regression tests; equally measured control, no invented performance gain. Then repeat from fresh runner; **positive result cannot yet be called transfer**. | UNVERIFIED |
| 5 | Existing WLS planning/context/learning/tool code | Independently pre-register disjoint held-out WLS self-code tasks; compare full-source vs minimal-diff proposal, guided vs no feedback, memory on/off and fixed controls using matched tokens/test budgets. Report per-task success, regression, time, cost and confidence, including losses. | UNVERIFIED |
| 6 | Existing `AgenticHarness`, `WorkerRegistry`, reviewer | Only activate model-powered planner/executor/independent verifier separation if matched-budget comparison beats a single Qwen and simple deterministic baseline. Leases/roles alone do NOT count. | ROUTING CODED in #138/#141; INTELLIGENCE UNVERIFIED |
| 7 | Existing cross-run WLS state/ledger | G1 improvement is inherited from independent GitHub run G2 and reused on a later different task; verify exact source, model and test manifests, rollback and no serious regression after restart. | CONTINUITY VERIFIED; LEARNING INHERITANCE UNVERIFIED |
| 8 | Existing G1 release gate R5 | Multiple independent held-out tasks show repeated positive net gains over frozen baselines with no irreversible policy change or evaluator leakage; release authority stays with owner. | BLOCKED ON 3–7 |

**Decision rule:** no additional coordinator, new model store, score service,
memory database, extra role or local-owner-computer dependency without a
measured failure that cannot be repaired in the existing code path. A G2
failure is a valid negative scientific result; do not inflate version or
claimed intelligence. Investigate transport/JSON rejection, context-length
truncation, limited model reasoning, and anchoring from its own failed patch
as competing explanations before changing the architecture.

### External methods checked (not copied, not WLS proof)

- [OpenEvolve](https://github.com/algorithmicsuperintelligence/openevolve):
  diversity/parent archives, evaluator feedback, and mixing complete rewrites
  with diffs; WLS already has Pareto archive, so first test proposal format,
  not a second archive.
- [RSIAgent](https://github.com/AetherLabsAI/RSIAgent): actor/verifier/curriculum,
  controlled memory reuse and explicit matched-budget caveats; adopt only
  verified memory-on/off experiments.
- [RSIBench](https://harness-rsibench.com/): self-code editable surfaces,
  trace-driven changes, frozen independent task evaluation and ablations.
- [SIA experiment protocol](https://github.com/vermayuvraj/self-improving-agent):
  negative controls matter; model self-repair can underperform blind resampling.
  Directly test that alternate explanation before claiming cumulative learning.
- [Darwin Gödel Machine research implementation](https://github.com/lemoz/darwin-godel-machine):
  selected mutations transferred in a bounded held-out setting, while Qwen
  candidates in its reported experiment did not qualify. Model choice and
  task-specific difficulty remain hypotheses, not borrowed proof of WLS.

## First-generation milestone: G1-GITHUB on Windows Actions (owner deployment deferred)

**Binding owner decision, 2026-10-10:** do **not** deploy/install on the
owner's computer while GitHub-hosted WLS has not completed successive
autonomous generations. GitHub `windows-latest` is the first-generation
**execution and validation target**. Personal hardware deployment is a
**separate later milestone**, only after successful GitHub generations and a
new owner authorization. No user time should be consumed by repeated basic
local verification.

Tracking: [G1 GitHub release #92](https://github.com/sangziwang91-design/workstation-living-system/issues/92);
[real Windows task #93](https://github.com/sangziwang91-design/workstation-living-system/issues/93),
[cross-run 72h continuity #94](https://github.com/sangziwang91-design/workstation-living-system/issues/94),
[frozen-budget learning #95](https://github.com/sangziwang91-design/workstation-living-system/issues/95),
[Windows installation and rollback #96](https://github.com/sangziwang91-design/workstation-living-system/issues/96).

### G1-GITHUB critical path and acceptance

| Order | Gate and minimum observed proof | Rejected proxy |
|---|---|---|
| P0 | R1 — immutable main/branch SHA, signed workflow provenance, exact-head full Ubuntu + Windows Python 3.11/3.13 CI, write-token boundary fixed, independent evaluator | previously passing unrelated commit |
| P0 | R3 — ≥10 real non-noop permitted tool tasks across two distinct task classes in disposable **Windows GitHub-hosted** environments; at least one real-evidence endogenous choice and one justified no-action | fixture-only, noop or PR count |
| P0 | R2 — ≥72 true elapsed hours and ≥4 **different GitHub workflow IDs** on fresh Windows VMs, reliable validated WLS state restore via immutable Actions artifacts, SQLite/ledger integrity, deliberate crash and restart | a single 6h job or 100 fast simulated cycles |
| P1 | R4 — source-controlled **synthetic non-sensitive owner-context** fixture import, memory retrieval and owner-only on/off negative control after Windows runner restart; private personal bundle is **never** uploaded | claim that real ChatGPT history moved to GitHub |
| P0 | R5 — frozen model/budget/tool access, independent hidden task evaluation across genuine restored generations, real success after previous failure, later successful reuse, no serious regression, rollback control | static benchmark score, scoring modified by candidate |
| P1 | R6 — Windows runner wheel clean install, CLI smoke, backup/upgrade/rollback on disposable Windows, pause/stop and evidence receipt; G1-GITHUB candidate build with immutable hashes | owner desktop deployment or seller-ready consumer release |

**GitHub Actions constraint:** standard GitHub-hosted job execution is capped
at 6 hours. Each fresh VM is ephemeral. Multi-day continuity must be tested
as genuine **inter-run** restoration, not a false persistent-VM claim.
Use `actions: read`-scoped previously successful artifact receipt/state
with source/workflow/provenance and digest verification; never restore
untrusted PR artifacts as execution context; fail closed on loss/mismatch.
Actions artifacts/workflow logs expire by repository retention policy.
Persist only synthetic WLS homes, never owner personal chat content,
credentials, hidden holdouts or provider secrets.

The installed Windows version is a **hosted runner test instance**, not
the owner's workstation. A completed G1-GITHUB gate is
`HOSTED_WINDOWS_VERIFIED`; after distinct successful runs it may be
`CROSS_RUN_VERIFIED`. It must **not** be renamed `OWNER_HOST_VERIFIED`.
When no model call is actually observed, model-driven RSI remains unproven.

**Accounting:** only source-linked, independent, same-protocol evidence at
an exact SHA can raise one of the six binary release gates in #92.
At this owner-policy revision, no G1-GITHUB release gate is yet marked PASS;
existing ET001–003, merged personal-context import, owner-only retrieval
ablation, and Linux/Windows CI are **valuable prerequisites** rather than
a fabricated release percentage. Report CODED/CI_VERIFIED separately from
HOSTED_WINDOWS_VERIFIED/CROSS_RUN_VERIFIED/LEARNING_VERIFIED.

**Stop condition:** do not start personal machine installation in any of the
G1-GITHUB issues; after 6/6 hosted gates are proven, report a first-generation
GitHub research candidate and continue subsequent GitHub generations.
Owner-host deployment remains DEFERRED_BY_OWNER until a future explicit
decision, even if all GitHub checks are green. G1 does not establish
consciousness, AGI, model-weight self-retraining, or unbounded RSI.

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