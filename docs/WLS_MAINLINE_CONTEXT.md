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

## Current Single Next Action

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
