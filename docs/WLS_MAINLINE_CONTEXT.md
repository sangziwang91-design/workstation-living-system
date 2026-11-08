# WLS Mainline Context

Updated: 2026-07-20

This file is the compact mainline map for WLS. It replaces scattered iteration
reading as the first stop for understanding the system.

## Mainline Identity

WLS is a single-owner local long-running action runtime. It is not primarily an
app shell, project manager, evidence dashboard, architecture poster, or test
collection.

The current mainline is:

```text
sense -> remember -> judge -> plan -> act under policy -> observe outcome
      -> learn -> update self-model -> sleep/compact -> resume
```

Evidence, health, rollback, retention, readiness, and audit features remain
important, but they are immune-system organs. They protect action chains; they
are not the main product experience.

## 2026-10-08 long-term identity and candidate growth contract

The enduring objective is a **continually improving autonomous long-running
agent**, not an automatic code repair product. Code repair is one measurable
skill. The July 2026 "repo continuity" product cut was a delivery strategy,
not a cancellation of the living-agent objective.

Source of intent: [Notion EXP-082](https://app.notion.com/p/38940ff6ad6281b6bd69d700c9322d77),
including the 2026-07-17 hypothesis archive and 2026-07-19 mainline reset.
GitHub runtime, code, real executed outcomes, and CI remain engineering truth.

**Candidate PR #36 integration:**

1. Every canonical LivingSystem.run_cycle() may read a bounded window of
   completed actual WLS tool failures. Unknown-side-effect, rejected,
   unstarted and unverified outcomes are not treated as learning labels.
2. LearningSystem creates at most one new stable failure hypothesis per
   cycle (after three matched real failures). New occurrence counts do not
   create duplicate candidates; an unchanged rejected hypothesis stays rejected.
3. AutonomySystem may select one supported candidate as an endogenous,
   read-only inspection goal in canonical GoalStore. Its evidence and goal
   persist over restarts. It cannot silently re-open that goal.
4. A newly selected eligible goal triggers at most one existing GrowthCycle
   contract_recovery experiment for bounded fixed tools (noop/read_file/
   list_directory), with independent result checking and candidate skill
   validation. This uses a path-rewritten test directory and is **not**
   OS isolation for model-authored executable code. No failed or ambiguous
   experiment is silently replayed after interruption.
5. A growth goal is **not** authority to run arbitrary generated code,
   self-edit policy/approval boundaries, or automatically promote skills.
   Skill validation is candidate-only; existing owner approval, real-task
   transfer verification and rollback remain in force.
6. Model-proposed RSI candidates still require an independently isolated
   evaluator, matched frozen-improver control, held-out tasks and longitudinal
   real-task evidence before any general RSI claim.

Acceptance: observed-failure goal formation != verified transfer gain !=
improver improves its own improvement efficiency. PR checks are candidate
evidence, not installed owner-host or multi-week autonomy proof.

## Canonical Trunk

```text
repository:        D:\WLS-Dev\workstation-living-system-private
branch:            main
runtime authority: source/src/wls/runtime.py::LivingSystem
package root:      source/src/wls
tests:             source/tests
installed home:    D:\WLS\wls-0.9.0.dev1-ui-20260706\home
```

`D:\WLS` is the installed/runtime workspace. Its `archive`, `campaigns`,
`acceptance`, `WLS-GapLedger-50Iterations`, and `WLS-Phase2-Integration`
directories are historical evidence/backlog material unless a concrete capability
has been promoted into the canonical trunk above.

## Current Organ Map

Brain:
- mission/goal state, planning, cognition traces, memory influence, goal
  pressure, action candidate selection, self-model calibration.

Trunk:
- SQLite state, runtime cycles, scheduler, daemon, loopback API, Owner Console,
  health preflight, recovery of stale cycles and pending actions.

Limbs:
- local file reads/writes through governed tools, command execution through
  policy, repository inspection, Patch Mission local code/test/patch/git chain.

Perception:
- sensor observations, daily perception classification, meaningful change
  summaries linked to goals and memory.

Immune system:
- evidence ledger, approval boundaries, health, retention, rollback drills,
  security policy, claim ceilings.

Self-evolution:
- failure learning, repair candidates, sandbox/validation/approval/promotion
  lifecycle, owner outcome feedback, skill confidence calibration.

## Active Mainline Capabilities

Life loop:
- `wls life-state` returns a bounded living-system state instead of a readiness
  report.
- Daily perception suppresses noise and links meaningful observations to goals
  and memory.
- Goal pressure ranks active work and can propose a single small next step.
- Action candidates classify risk and preserve owner approval boundaries.
- `wls outcome-feedback` records owner feedback and updates goal progress only
  with evidence.

Patch Mission:
- `wls patch-mission REPO_PATH "mission"` creates mission/goal/action state and
  performs scoped repo inspection.
- `wls-patch-missions --config CONFIG --continuity-only` shows persisted Patch
  Mission continuity without mutating runtime state.
- `wls patch-mission-step --mode resume-next` continues the next concrete Patch
  Mission action from persisted continuity.
- Existing modes cover local inspection, owner-gated pytest, outbox patch draft,
  owner-gated apply, verification, PR summary, git metadata, commit draft, remote
  readiness, branch draft, push draft, draft PR creation, PR/CI status inspection,
  CI log evidence, CI fix planning, PR update verification, and repair skill
  candidate lifecycle.
- Push, PR creation, external reads, repo writes, and command execution remain
  separately owner-approved actions.

## What Is Still Not Mainline

- Standalone `gapXX` prototype files in extracted packages.
- Poster/capability-map HTML files.
- Readiness gates that do not protect a real action chain.
- Dashboard/report additions that do not help WLS choose, execute, learn, or
  resume a real task.
- Multi-user SaaS, compliance certification, unrestricted autonomy, or claims of
  sentience/personhood.

## Merge Rule

Promote old iteration material only when it satisfies all five checks:

1. It adds or repairs a real action chain.
2. It enters through canonical CLI/API/UI/runtime surfaces.
3. It stores state/evidence in the canonical SQLite/evidence system.
4. It has focused tests or a bounded owner-host receipt.
5. It preserves owner approval for writes, commands, external publication, and
   irreversible operations.

Otherwise keep it as history/backlog, not mainline.

## Mainline Completion Plan · 2026-11-08

Baseline `650db41`. The counts below describe acceptance items, not a percentage
of WLS or an ETA. Each round closes three items. Failed trials and unresolved
results stay open. Use the existing `LivingSystem`, state, policy, and workflow
surfaces. Run engineering qualification on GitHub first.

| Round | Deliverable and three acceptance items | Verified at baseline | Cumulative target |
|---|---|---:|---:|
| 1. Bounded unattended operation | **1.1** Inherit earlier completed tasks in a genuine scheduled run, select a new repository task, and independently verify its candidate. **1.2** A broken baseline cannot permanently suppress a valid retry. **1.3** Observe at least seven days with pause, interrupted-run recovery, no duplicate side effects, and per-run resource ceilings. | **2/3** | **3/18** |
| 2. Measurable model decisions | **2.1** Archive actual public prompt and reply, and verify both against the inference proof. **2.2** Execute the short-ID model protocol in hosted CI with strict allowlists, real pytest, and at most three attempts per task. **2.3** Compare the same model and fixed controls on at least 12 public development cases, with exact restoration, invalid replies, cost, and failures counted. Admit the model for the tested scope only if it beats both controls under the same budget. | **1/3** | **6/18** |
| 3. Governed action and reversible delivery | **3.1** Run candidate-authored code in the existing C1 isolation boundary, with hostile tests proving no evaluator writes, credential reads or network access. **3.2** Carry one real task through canonical Patch Mission selection, isolated execution and independent acceptance, recording its exact inputs and outcome. **3.3** Prove digest-bound approval, promotion and rollback on that same task, including replay or stale-approval rejection. | **0/3** | **9/18** |
| 4. One durable, multifunctional runtime | **4.1** Complete a code task and a non-code information or document task with shared identity, goals and memory, plus a verified external result for each. **4.2** Swap between two approved live backends on the same task and resume without changing task identity, and test unavailability and budget stop. **4.3** Install or upgrade on owner Windows and verify restart, offline recovery and rollback across 72 hours of real usage. | **0/3** | **12/18** |
| 5. Cumulative improvement | **5.1** Freeze separate public development, selection and private final sets, with at least 40 sealed final cases and a scorer inaccessible to candidate execution. **5.2** Admit one skill or strategy with a receipt, then use it on an unseen task after restart and verify old-task regression coverage. **5.3** Compare learning enabled vs frozen state over at least three spaced checkpoints on matched tasks and budgets, reporting paired differences, costs and failures. | **0/3** | **15/18** |
| 6. Recursive improver evidence | **6.1** Register the frozen and evolving improver arms, common budgets, seeds, stopping rules and evaluation hashes before observing results. **6.2** Run at least three paired seeds on unseen tasks and publish success, cost, regression and uncertainty, including negative runs. **6.3** Replicate a positive result on a separate sealed split with another predeclared seed set. Admit the RSI claim only if the advantage survives both studies without hard regressions. | **0/3** | **18/18** |

Baseline evidence: [scheduled repair with inherited history](https://github.com/sangziwang91-design/workstation-living-system/actions/runs/42403251393),
[baseline-failure and retry checks](https://github.com/sangziwang91-design/workstation-living-system/pull/124),
and [offline R2 output and score](https://github.com/sangziwang91-design/workstation-living-system/actions/runs/42395547786).
The archived Qwen response is structurally valid JSON and copies `sha256` from
the example as its candidate IDs. It was scored at 0/3, versus 1/3 for fixed
line order and 0/3 for seeded random. The short-ID trial is the immediate
next experiment. Rounds 1 and 2 can advance independently. Round 3 isolation
must precede execution of candidate-authored code in Rounds 3–6. Round 4 may
proceed with previously approved backends while model admission is measured.
An inconclusive result or a baseline win is an experimental result, and leaves
the corresponding capability gate open.

## Historical Patch Mission Handoff

Finish the source-level Patch Mission mainline proof with an executable handoff,
not another placeholder note:

```powershell
wls-patch-missions --config CONFIG --continuity-only
wls --config CONFIG approve ACTION_ID --reason "owner approves exact recovered commit-draft"
wls --config CONFIG resume-action ACTION_ID
wls --config CONFIG patch-mission-step --mode resume-next
wls-patch-missions --config CONFIG --continuity-only
```

After the local commit is verified in continuity, continue only to the already
implemented remote-readiness path and preserve the hard split between local work,
branch push, and PR creation:

```powershell
wls --config CONFIG patch-mission-step --mode remote-summary --action-id ACTION_ID
wls --config CONFIG patch-mission-step --mode branch-draft --action-id ACTION_ID
wls --config CONFIG patch-mission-step --mode remote-live
```

Do not add more dashboards, labels, readiness gates, or pure reports before this
action chain is stable and installed into the owner-host WLS.


## 2026-11-08 candidate-only model-core research adaptation

This section records a research-inspired *opt-in* experiment, not a claimed
self-improving release, training recipe, or accepted holdout gain.

- [OpenEvolve (Apache-2.0)](https://github.com/algorithmicsuperintelligence/openevolve):
  island/MAP-Elites archive and diversity search are reference mechanisms.
  WLS reuses its **own** signed archive and evaluator; it does not vendor,
  execute, or import upstream source.
- [GEPA (MIT)](https://github.com/gepa-ai/gepa): multiple non-dominated
  candidates and measured outcomes motivate the optional Pareto parent picker.
  No private evaluator traces or scoring oracle are exposed to the proposer.
- [Darwin Godel Machine](https://arxiv.org/abs/2505.22954): candidate ancestry
  as a source of alternative improvement paths; authority remains candidate-only.
- [Continual harness evolution, 2026-11-02](https://arxiv.org/abs/2611.03219):
  later evolution can erase prior task gains. This is why existing hard regression
  gates remain mandatory, and model/harness gains must survive replay on previous
  task families before promotion.
- [Agent Lightning v1.0 (MIT)](https://github.com/microsoft/agent-lightning):
  later weight-training architecture is deliberately **not** adopted on a
  standard CPU-only GitHub Runner, which cannot establish a practical RL loop.

The optional setting `exploration_strategy="pareto"` only changes how the
existing RsiEvolutionPilot samples completed, independently safe archived
parents during the configured `exploration_branches`. It does not change the
primary champion, global acceptance baseline, scoring policy, model weights,
live runtime, or promotion permissions. Default remains
`least_sampled` with zero exploration branches. Gate metric provenance,
scorer independence, and sealed held-out gain remain separate acceptance work.

