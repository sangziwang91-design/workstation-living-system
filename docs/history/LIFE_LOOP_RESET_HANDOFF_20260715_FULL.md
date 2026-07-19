# WLS Life Loop Reset Handoff

This handoff redirects WLS from evidence-led commercial hardening back to the
original living-system line.

The commercial evidence layer is retained as an immune system. It must no longer
be treated as the main brain.

## Current Diagnosis

WLS has become strong at proving that it is safe, auditable, recoverable, and
deliverable. Recent iteration strengthened:

- health gates
- receipts
- rollback drills
- retention audits
- readiness audits
- external single-user delivery evidence

That work is useful, but it moved the center of gravity away from the original
life loop:

```text
sense -> remember -> judge -> act -> observe outcome -> update self -> sleep
```

The next chain must make that loop the primary product experience again.

## Non-Negotiable Direction

Do not add more readiness layers unless a life-loop task is blocked without one.

Do not measure success by receipt count.

Measure success by whether WLS can, every day:

1. notice meaningful change,
2. connect it to memory and goals,
3. choose one bounded useful action,
4. ask for approval when needed,
5. learn from the result,
6. update its self-model,
7. consolidate during sleep,
8. explain what changed in itself.

## Scope

In scope:

- single-owner local WLS
- persistent daemon life cycle
- owner goals and projects
- memory influence
- self-model calibration
- sleep consolidation
- bounded autonomous suggestions
- owner-approved actions
- life-state UI

Out of scope for this chain:

- multi-user tenancy
- province or regional partitioning
- more external product readiness gates
- SaaS infrastructure
- compliance certification

## Architecture Reframe

Evidence layer becomes immune system:

```text
health / audit / rollback / retention / readiness
```

Life layer becomes main system:

```text
sensors / goals / memory / cognition / planning / action / learning / self_model / sleep
```

The immune system may block dangerous behavior. It should not decide what WLS
cares about.

## Iteration Chain

### L1: Life State Baseline

Goal: Make the current living state visible in one compact object.

Implement or improve a `life-state` view with:

- current active goals
- latest meaningful observations
- top memory influences
- current self-model confidence
- pending owner approvals
- last sleep consolidation summary
- one proposed next action

Acceptance:

- `wls life-state` or equivalent API returns a bounded JSON payload.
- It reads existing organs rather than creating another evidence report.
- It includes a `next_action_candidate` or explains why none exists.

### L2: Daily Perception Loop

Goal: Make sensing produce useful daily context, not just rows.

Tasks:

- classify observations as noise, context, opportunity, risk, or owner-relevant
- preserve the top daily changes
- connect observations to goals and memory

Acceptance:

- one cycle can identify at least one meaningful observation from local state
- noisy observations do not dominate attention
- output is visible in life-state

### L3: Goal Pressure

Goal: Make goals exert pressure on attention and planning.

Tasks:

- rank active goals by priority, age, blocked state, and recent evidence
- surface neglected goals
- propose a next small step for the top goal

Acceptance:

- WLS can explain why one goal is currently most alive
- a stale high-priority goal becomes visible without manual search
- no action executes without policy and approval gates

### L4: Memory Influence Proof

Goal: Make memory affect judgment in a human-legible way.

Tasks:

- for each plan candidate, include the memory IDs that influenced it
- mark whether each memory supported, warned, or contradicted the plan
- penalize stale or low-confidence memories

Acceptance:

- WLS can answer: "What memory changed this decision?"
- selected memories are not merely displayed; they alter ranking or rationale
- false or stale memory can be corrected or deactivated

### L5: Bounded Action Candidate

Goal: Restore action as part of life, while keeping owner authority.

Tasks:

- generate one bounded next-action candidate per cycle when useful
- classify risk as read, draft, write, cleanup, rollback, or external
- route anything material to owner approval

Acceptance:

- a cycle can produce an actionable draft, not only a receipt
- read-only actions can be executed within policy
- write actions remain pending until owner approval

### L6: Outcome Learning

Goal: Convert outcomes into better future behavior.

Tasks:

- record outcome after owner review
- update goal progress when evidence supports it
- capture what worked, what failed, and what should be avoided

Acceptance:

- repeated failed suggestions are suppressed
- successful patterns become reusable skills or heuristics
- goal progress moves only with evidence

### L7: Self-Model Calibration

Goal: Make WLS know what it is good and bad at.

Tasks:

- maintain capability confidence by task class
- lower confidence after corrections or blocked actions
- raise confidence after successful repeated outcomes
- expose "I should not attempt this yet" when appropriate

Acceptance:

- self-model changes after real outcomes
- WLS can refuse or defer tasks outside its proven competence
- confidence is not derived from receipt volume alone

### L8: Sleep as Integration

Goal: Make sleep compress experience into better future behavior.

Tasks:

- consolidate repeated observations
- deduplicate low-value memories
- extract stable owner preferences
- generate a morning summary and next-focus suggestion

Acceptance:

- `wls sleep` changes memory/self-model state in a bounded way
- sleep summary is useful to the owner
- sleep does not create uncontrolled actions

### L9: Life UI

Goal: Make the Owner Console feel like a living dashboard, not a compliance desk.

Tasks:

- first screen shows life state
- evidence/readiness moves to a secondary safety panel
- active goals, memory influence, and next action become primary

Acceptance:

- owner can see what WLS noticed, why it matters, and what it wants to do next
- readiness receipts are available but not dominant
- UI supports approve/reject/feedback for proposed next actions

### L10: Seven-Day Life Trial

Goal: Prove sustained life-loop usefulness without adding new product gates.

Run for seven daily cycles or simulated owner-day probes:

- daily observation summary
- one next-action candidate
- owner feedback
- outcome learning
- sleep consolidation

Acceptance:

- at least 5 of 7 days produce useful owner-visible output
- repeated noise decreases over the trial
- self-model and memory show real adaptation
- no unsafe autonomous write occurs

## Task Order

1. `life-state` command/API
2. observation classification
3. goal pressure ranking
4. memory influence attribution
5. next-action candidate generation
6. owner feedback/outcome learning
7. self-model calibration
8. sleep integration summary
9. UI life-first redesign
10. seven-day life trial

## Success Definition

The reset is successful when WLS can say, in one compact daily summary:

```text
I noticed this.
It matters because of these goals and memories.
I think the next useful step is this.
I need approval for this part.
Yesterday's feedback changed me in this way.
Tonight I will consolidate these patterns.
```

## Guardrails

- Keep commercial readiness commands intact.
- Keep rollback and retention safety intact.
- Do not delete receipts blindly.
- Do not let evidence reports become the main output.
- Do not create a second brain in the UI.
- Do not claim sentience, consciousness, or unrestricted autonomy.

## Handoff Prompt

Use this prompt for the next implementation chain:

```text
Continue WLS from docs/LIFE_LOOP_RESET_HANDOFF.md.
Do not add more commercial/readiness gates unless necessary.
Treat existing evidence systems as immune/safety organs.
Implement L1-L10 in order.
The goal is to restore the living-system loop:
sense -> remember -> judge -> act -> learn -> self-model -> sleep.
Start with a bounded life-state command/API and tests.
```

## Current Implementation Handoff

Updated: 2026-07-15.

### Completed In This Chain

- L1 life-state command/API exposes the bounded living loop.
- L2 perception classifies daily observations and suppresses noise.
- L3 goal pressure ranks active/blocked/stale goals and proposes one small step.
- L4 memory influence proof shows support/warn/contradict effects.
- L5 bounded action candidates classify read/draft/write/cleanup/rollback/external and route material actions to owner approval.
- L6 owner outcome feedback records helped/failed/avoid/neutral feedback, moves goal progress only with owner evidence, suppresses repeated failed action signatures, and creates procedural heuristic memories from repeated useful/failed patterns.
- L7 self-model calibration now learns owner-calibrated capability confidence from feedback and can defer a future matching action class before persistence/execution.
- Task A initial GitHub Patch Mission link now exists: `patch-mission` accepts a local repo path and mission text, creates mission/goal/cycle/action state, builds a repo map, grants scoped read-only access to the explicit repo root, inspects CONTRIBUTING/README or repo root through canonical tools, persists outcomes, and keeps writes/push/PR out of scope.
- Task A follow-up link now exists: `patch-mission-step` can inspect a target repo file with a real read action, create a pytest probe as a canonical `run_command` action that waits for owner approval under policy, or create an outbox-only patch draft action that also waits for approval. Canonical repo writes are still blocked.
- Task A approved-test-result link now exists: after the owner approves and resumes a pytest `run_command` action, `patch-mission-step --mode from-test-result --action-id <action>` reads the persisted action result, identifies a likely failing repo file, and creates an outbox-only draft action. The canonical repo is not modified.
- Task A diff-synthesis link now exists for a bounded Python pytest case: when approved pytest output exposes a simple literal mismatch and the failing test imports an adjacent source module, WLS reads the test/source files, synthesizes a minimal unified diff into the outbox draft, and still leaves canonical repo writes owner-gated.
- Task A apply-patch link now exists: after the owner approves/resumes the outbox draft write, `patch-mission-step --mode apply-patch --action-id <draft_action>` reads the outbox unified diff, verifies it against the current repo file, creates an exact HIGH-risk canonical repo `write_file` action, waits for owner approval, and can then rerun pytest against the changed repo state.
- Patch Mission pytest actions now include a bounded repo-state digest in their idempotency key, so verification tests rerun after approved repo changes instead of reusing stale results.
- Task A verified PR summary link now exists: after an approved apply-patch and passing pytest rerun, `patch-mission-step --mode pr-summary --action-id <passing_test_action>` writes an outbox-only PR summary that cites the failing test, applied diff, apply action, and passing rerun evidence. No branch, commit, push, or PR is created.
- Task A local git prep link now exists: `patch-mission-step --mode git-metadata` runs a fixed read-only git metadata inspection for branch/status/diff, and `patch-mission-step --mode git-prep --action-id <git_metadata_action>` writes an outbox-only commit-ready checklist. No commit, branch creation, push, or PR is created.
- Task A local commit draft link now exists: after the owner approves/resumes the PR summary and commit checklist outbox writes, `patch-mission-step --mode commit-draft --action-id <git_prep_action>` creates an exact HIGH-risk local `git commit -am ...` action. It waits for owner approval before committing and still does not push, create branches, or open PRs.
- Task A remote-readiness link now exists: git metadata inspection captures local remote URLs, branch metadata, and last commit without fetching or contacting GitHub, and `patch-mission-step --mode remote-summary --action-id <git_metadata_action>` writes an outbox-only remote readiness summary. No branch, push, or PR is created.
- Task A local branch draft link now exists: after the owner approves/resumes the remote readiness summary and the local commit has succeeded, `patch-mission-step --mode branch-draft --action-id <remote_summary_action>` creates an exact HIGH-risk local `git checkout -b wls/...` action. It waits for owner approval before creating/switching branches and still does not push or open PRs.
- Task A live remote inspection link now exists: `patch-mission-step --mode remote-live` creates a HIGH-risk read-only remote inspection action that waits for owner approval before running fixed `git ls-remote` queries and optional public GitHub metadata reads. `patch-mission-step --mode remote-live-summary --action-id <remote_live_action>` writes an outbox-only summary. No fetch, branch creation, commit, push, or PR is created.
- Task A push draft link now exists: after a prepared local `wls/...` branch and approved live remote summary, `patch-mission-step --mode push-draft --action-id <remote_live_summary_action>` creates an exact HIGH-risk `git push -u origin <wls/...>` action. It waits for owner approval before pushing and still does not open PRs.
- Task A PR creation draft link now exists: after a verified PR summary, approved GitHub remote evidence, and a successful pushed `wls/...` branch, `patch-mission-step --mode pr-create-draft --action-id <push_action>` creates an exact HIGH-risk `create_github_pull_request` action. It waits for owner approval and requires `GITHUB_TOKEN` before creating a draft PR.
- Git remote metadata idempotency now includes `.git/config`, so WLS does not reuse stale remote evidence after the origin URL changes.
- Task A post-PR status inspection link now exists: `patch-mission-step --mode pr-status --target https://github.com/<owner>/<repo>/pull/<number>` creates a HIGH-risk read-only `inspect_github_pr_status` action that waits for owner approval before reading PR metadata, commit statuses, check runs, workflow runs, and bounded CI failure summaries. It can also continue from a succeeded `pr-create-draft` action via `--action-id`.
- Task A CI fix-plan link now exists: after a succeeded `pr-status` action, `patch-mission-step --mode ci-fix-plan --action-id <pr_status_action>` writes an outbox-only repair plan that extracts repo file/test clues from PR/CI failure summaries and selects the next local Patch Mission step (`test`, `inspect-file`, or `git-metadata`). No repo or GitHub state is changed.
- Task A CI next-action link now exists: after the owner approves/resumes the CI fix-plan outbox write, `patch-mission-step --mode ci-next-action --action-id <ci_fix_plan_action>` parses the selected next local step and creates that real Patch Mission action. For CI test failures this creates the existing owner-gated local pytest `run_command` action.
- Task A CI repair loop link now exists: CI-triggered local pytest actions created by `ci-next-action` are accepted by `from-test-result`, can synthesize/apply a patch through the existing repair loop, and preserve PR/CI source evidence in the patch draft and verified PR summary.
- Task A PR update push link now exists: after a CI-sourced repair is verified, summarized, committed locally, and a PR status action provides a `wls/...` head ref, `patch-mission-step --mode pr-update-push-draft --action-id <pr_status_action>` creates an exact owner-gated `git push origin HEAD:<pr_head_ref>` action. It does not force push, comment, review, merge, or create a new PR.
- Task A post-update PR/CI recheck link now exists: after a successful PR update push, `patch-mission-step --mode pr-update-status --action-id <pr_update_push_action>` prepares a read-only status recheck for the same PR, and `patch-mission-step --mode pr-update-verify --action-id <post_update_status_action>` writes an outbox-only comparison of pre/post head SHA and failure summaries.
- Task A branch-from-verification link now exists: after the owner approves/resumes the post-update verification note, `patch-mission-step --mode pr-update-next --action-id <pr_update_verify_action>` branches from the evidence. If post-update failures remain, it creates the next `ci-fix-plan` from the post-update status action. If no immediate failures are captured, it writes an outbox-only wait/owner-review next-step note.
- Task A compact continuity state now exists on existing Patch Mission records returned by `patch_missions()`: it derives `waiting_approval`, `pr_ci_still_failing`, `pr_branch_updated`, `ready_for_owner_review`, and related next-step hints from real followup actions and action rows. It is not stored as a separate state source and does not add a dashboard.
- Task A resume-next link now exists: `patch-mission-step --mode resume-next` consumes the compact continuity snapshot and turns it into the next existing concrete Patch Mission action for the PR/CI repair loop. It blocks when owner approval is pending, preserves the concrete mode in mission history, and does not bypass write/external approval.
- Task A resume-next publishing link now exists: after a verified `pr-summary`, `resume-next` can continue through git metadata, commit checklist, commit draft, remote summary, branch draft, live remote inspection, live remote summary, push draft, post-push remote evidence refresh, and draft PR creation. It still uses only existing actions and preserves exact owner approval for writes, commands, pushes, and PR creation.
- Task A CI log evidence link now exists: after a failed `pr-status`, `patch-mission-step --mode ci-log-evidence --action-id <pr_status_action>` creates a HIGH-risk read-only `inspect_github_ci_logs` action. After owner approval, it fetches bounded failed GitHub Actions job log excerpts and failure clues; `resume-next` now routes failed `pr-status -> ci-log-evidence -> ci-fix-plan`.
- Task A narrow CI reproduction link now exists: `ci-fix-plan` extracts safe pytest targets such as `tests/test_demo.py::test_demo` from bounded CI log evidence, and `ci-next-action` creates an owner-gated `python -m pytest <target>` action instead of always running the full suite.
- Task A failure-learning link now exists: `from-test-result` records compact cause/fix/regression evidence and a procedural memory from the approved test action plus patch synthesis result. The draft cites the learning evidence and memory; failed synthesis remains a learning note, not a capability claim.
- Task A failure-memory influence link now exists: `ci-fix-plan` and `from-test-result` retrieve recent `patch_mission_failure_learning` procedural memories, cite memory IDs in the outbox evidence, and use them as advisory repair context without bypassing approval or claiming promoted skills.
- Task A repair skill-candidate link now exists: after at least two complete Patch Mission literal-mismatch repairs have failed locally, generated an outbox draft, applied an owner-approved patch, passed verification, and reached PR summary, WLS records successful repair samples and creates a `patch_mission_repair_skill` evolution candidate. The candidate includes trigger, steps, tools, risks, verification, rollback, source memory/evidence/action IDs, and remains `PROPOSED`; no active skill is created or promoted.
- Task A repair candidate replay link now exists: `patch-mission-candidate-review <candidate_id>` and matching APIs replay the retained evidence for a `patch_mission_repair_skill` candidate. The replay verifies proposal completeness, source-bound success evidence, failure-learning memory, failing/passing pytest actions, draft diff target, and approved apply action, then records a replay receipt. Passing replay remains advisory; it does not transition candidate status, create a skill, run commands, push, open PRs, or bypass approval.
- Task A reviewed-candidate influence link now exists: after a repair candidate replay passes, later `ci-fix-plan` and `from-test-result` outputs cite the reviewed candidate as advisory context. Normal Patch Mission policy still requires separate owner approval for commands, writes, pushes, and PR actions.
- Task A repair candidate sandbox link now exists: `patch-mission-candidate-sandbox <candidate_id> --owner-approved` and matching APIs run a disposable fixture repo under `sandbox_path`, reproduce the literal pytest failure, synthesize the repair diff, apply it only inside the sandbox, rerun pytest green, persist manifest/result artifacts, record evidence, and move the evolution candidate to `SANDBOXED`. It does not touch canonical repos, create skills, promote candidates, push, open PRs, or perform external actions.
- Task A repair candidate validation link now exists: `patch-mission-candidate-validate <candidate_id> --human-approved` and matching APIs verify a `SANDBOXED` `patch_mission_repair_skill` candidate by reloading sandbox manifest/result artifacts, checking persisted digests, confirming the sandbox result passed, recording validation evidence, and moving the candidate to `VALIDATED`. Validation is human-approved and still does not create a skill, promote, run commands, touch canonical repos, push, or open PRs.
- Task A repair skill proposal link now exists: `patch-mission-candidate-propose-skill <candidate_id>` and matching APIs convert a `VALIDATED` `patch_mission_repair_skill` candidate into a declarative `skills` row with status `PROPOSED`. The skill preserves trigger terms, steps, tools, risks, verification/rollback context, candidate/evidence source IDs, and remains non-active; it is not returned by `SkillLibrary.active()` and cannot execute or promote itself.
- Task A repair skill sandbox-start link now exists: `patch-mission-skill-sandbox <skill_id>` and matching APIs create a `skill_experiments` RUNNING record for the proposed Patch Mission repair skill, bind it to the validated source candidate and digests, write a manifest under `sandbox_path`, and transition the skill from `PROPOSED` to `SANDBOXED` through the existing skill lifecycle. It still does not execute commands, validate the skill, approve, promote, or activate it.
- Task A repair skill validation link now exists: `patch-mission-skill-validate <skill_id>` and matching APIs complete the skill sandbox by writing `skill_experiments.result_json`, marking the experiment `PASSED` only when source candidate-backed cases pass without regressions, and transitioning the skill from `SANDBOXED` to `VALIDATED` through existing skill lifecycle digest checks. The skill still is not active, approved, promoted, or executable.
- Task A repair skill approval link now exists: `patch-mission-skill-approve <skill_id> --human-approved` and matching APIs move a `VALIDATED` Patch Mission repair skill to `APPROVED` only after explicit owner authorization and validation-result digest checks. The skill becomes matchable through `SkillLibrary.active()` as advisory context, but the approval step does not promote, run commands, write canonical repos, push, open PRs, or perform external actions.
- Task A approved repair skill advisory-use link now exists: later `ci-fix-plan` and `from-test-result` steps match `APPROVED` Patch Mission repair skills through `SkillLibrary.match()` and cite the skill ID, status, match score, and advisory-only authority in the outbox content. This uses the skill for planning context only: it does not call `record_use()`, promote the skill, execute commands, write repos, push, open PRs, or change GitHub state.
- Task A approved repair skill rationale-influence link now exists: when a matching `APPROVED` repair skill is present, `ci-fix-plan` uses it to shape the suggested next-step reason and `from-test-result` writes an explicit approved-skill patch-draft rationale. This influence remains text-only and advisory: it does not execute the skill, increment use count, promote, mutate repos, push, open PRs, or change GitHub state.
- Task A approved repair skill action-candidate link now exists: after an approved-skill-influenced `ci-fix-plan`, `ci-next-action` creates the normal owner-gated local pytest action with `patch_mission_approved_skill_candidate` metadata in the action arguments and `next_action_candidate`. The action remains `WAITING_APPROVAL`, does not set `ActionSpec.skill_id`, does not call `record_use()`, and does not execute, promote, mutate repos, push, open PRs, or change GitHub state without the existing approval chain.
- Task A approved repair skill action-outcome link now exists: when an owner-approved skill-derived Patch Mission action actually succeeds, WLS records `patch_mission_approved_repair_skill_action_outcome_recorded`, increments the approved skill use count once, and returns the bounded outcome on `resume_action`. Pending, rejected, failed, or unapproved candidates still do not count as skill execution; outcome recording remains distinct from promotion and does not mutate repos beyond the approved action itself.
- Task A approved repair skill promotion-review candidate link now exists: after two distinct owner-approved successful skill-derived Patch Mission actions, WLS creates a `patch_mission_repair_skill_promotion_review` evolution candidate with status `PROPOSED`. The candidate summarizes outcome receipts and recommends owner review only; it does not transition the skill to `PROMOTED`, does not approve future actions, and does not grant command/write/GitHub authority.
- Task A approved repair skill promotion-review decision link now exists: `patch-mission-skill-promotion-review-approve <candidate_id> --human-approved` and matching APIs approve a `PROPOSED` promotion-review candidate into `APPROVED` after explicit owner authorization. This decision records evidence and authorizes a later separate promotion request, but it does not call `skills.transition(...PROMOTED)`, execute commands, mutate repos, push, open PRs, or bypass future approval.
- Task A repair skill promotion execution link now exists: `patch-mission-skill-promote <candidate_id> --human-approved` and matching APIs promote a Patch Mission repair skill from `APPROVED` to `PROMOTED` only when the source promotion-review candidate is already `APPROVED`. This calls the existing skill lifecycle transition with explicit human approval and review evidence, but it does not execute commands, mutate repos, push, open PRs, or approve future actions.
- Task A promoted repair skill planning link now exists: later `ci-fix-plan` and `ci-next-action` steps now distinguish `PROMOTED` Patch Mission repair skills from merely `APPROVED` advisory skills. A promoted match is cited as higher-confidence rationale and is copied into `next_action_candidate` metadata as `promoted_repair_skill_action_candidate`, but the generated action still remains `WAITING_APPROVAL`, does not set `ActionSpec.skill_id`, does not increment use count, and does not execute, mutate repos, push, open PRs, or change GitHub state.
- Task A promoted repair skill outcome-learning link now exists: when a `PROMOTED` repair-skill-derived Patch Mission action is separately owner-approved and succeeds, WLS records `patch_mission_promoted_repair_skill_action_outcome_recorded`, writes a separate `patch_mission_promoted_skill_outcomes` runtime history, and increments the promoted skill use count. Pending, unapproved, rejected, or failed promoted-skill candidates do not count as skill execution and do not create outcome receipts, promotion reviews, repo writes, pushes, PRs, or approval bypass.
- Task A promoted repair skill outcome-informed planning link now exists: later `ci-fix-plan` and `ci-next-action` steps summarize promoted outcome history as bounded confidence evidence. Owner-approved successful promoted outcomes raise planning confidence; later failed or rejected promoted-skill candidates reduce it to mixed/withheld confidence. This changes outbox rationale and candidate metadata only; it does not execute actions, auto-approve, mutate repos, push, open PRs, or grant GitHub authority.
- Task A promoted repair skill confidence-based local action selection link now exists: when promoted outcome confidence is `mixed_outcome` or `confidence_withheld`, a CI plan that would otherwise run a direct local pytest step is downgraded to a safer `inspect-file` action against the implicated repo file. The plan preserves the original CI-selected mode/target/reason, and `ci-next-action` creates the existing read-only file limb action instead of a command. No repo write, push, PR, approval bypass, or skill execution count is granted.
- Task A promoted repair skill outcome-supported direct path link now exists: when promoted outcome confidence is `outcome_supported`, WLS preserves the direct narrow pytest next step and explicitly records that the path is preserved by promoted outcome history. The resulting `run_command` action still remains `WAITING_APPROVAL`, carries promoted confidence metadata, does not set `ActionSpec.skill_id`, and does not count as skill execution unless the owner separately approves it and it succeeds.
- Task A promoted confidence continuity link now exists: `patch_missions()` continuity now exposes the promoted confidence decision behind `ci-next-action`, including whether the next step was an `outcome_supported` direct pytest path or a `mixed_outcome`/`confidence_withheld` safety fallback to read-only `inspect-file`. When the fallback inspection has completed, `resume-next` continues to the owner-gated local pytest verification for the inspected target instead of incorrectly treating the read as a test result.
- Task A fallback inspection target-narrowing link now exists: after a promoted-confidence safety fallback reads the implicated test file, `resume-next` compares the original CI-selected pytest nodeid with the inspected file content. If the referenced test function exists in that file, WLS restores the narrower nodeid such as `tests/test_demo.py::test_demo` for the next owner-gated pytest action; otherwise it keeps the safer file-level target. This uses read-only file content and existing CI evidence only.
- Task A fallback context draft/learning link now exists: when the owner-approved fallback-restored pytest action feeds `from-test-result`, the outbox patch draft and failure-learning memory preserve the safety fallback story: fallback inspect action, promoted confidence level/decision, restored verification target, and target-narrowing reason. This makes the next human-reviewed patch draft explain why WLS inspected first and why it trusted the restored nodeid.

### User Entrypoints Added

- CLI: `wls outcome-feedback helped|failed|avoid|neutral [--action-id ...] [--note ...] [--evidence '{...}'] [--goal-progress-delta N]`
- Core API: `POST /outcome-feedback`
- Owner Console API: `POST /api/outcome-feedback`
- `life-state` now includes `outcome_learning` and `self_model_calibration`.
- CLI: `wls patch-mission <repo_path> "<mission>" [--no-execute]`
- Core API: `POST /patch-mission`
- Owner Console API: `POST /api/patch-mission`
- CLI: `wls patch-mission-step [--mission-id ...] --mode resume-next|inspect-file|test|draft-patch|from-test-result|apply-patch|pr-summary|git-metadata|git-prep|commit-draft|remote-summary|branch-draft|remote-live|remote-live-summary|push-draft|pr-create-draft|pr-status|pr-update-push-draft|pr-update-status|pr-update-verify|pr-update-next|ci-log-evidence|ci-fix-plan|ci-next-action [--target ...] [--draft ...] [--action-id ...]`
- Core API: `POST /patch-mission/step`
- Owner Console API: `POST /api/patch-mission/step`
- CLI: `wls patch-mission-candidate-review <candidate_id> [--reason ...]`
- Core API: `POST /patch-mission/repair-candidate/review`
- Owner Console API: `POST /api/patch-mission/repair-candidate/review`
- CLI: `wls patch-mission-candidate-sandbox <candidate_id> --owner-approved [--reason ...]`
- Core API: `POST /patch-mission/repair-candidate/sandbox`
- Owner Console API: `POST /api/patch-mission/repair-candidate/sandbox`
- CLI: `wls patch-mission-candidate-validate <candidate_id> --human-approved [--reason ...]`
- Core API: `POST /patch-mission/repair-candidate/validate`
- Owner Console API: `POST /api/patch-mission/repair-candidate/validate`
- CLI: `wls patch-mission-candidate-propose-skill <candidate_id> [--reason ...]`
- Core API: `POST /patch-mission/repair-candidate/propose-skill`
- Owner Console API: `POST /api/patch-mission/repair-candidate/propose-skill`
- CLI: `wls patch-mission-skill-sandbox <skill_id> [--reason ...]`
- Core API: `POST /patch-mission/repair-skill/sandbox`
- Owner Console API: `POST /api/patch-mission/repair-skill/sandbox`
- CLI: `wls patch-mission-skill-validate <skill_id> [--reason ...]`
- Core API: `POST /patch-mission/repair-skill/validate`
- Owner Console API: `POST /api/patch-mission/repair-skill/validate`
- CLI: `wls patch-mission-skill-approve <skill_id> --human-approved [--reason ...]`
- Core API: `POST /patch-mission/repair-skill/approve`
- Owner Console API: `POST /api/patch-mission/repair-skill/approve`
- CLI: `wls patch-mission-skill-promotion-review-approve <candidate_id> --human-approved [--reason ...]`
- Core API: `POST /patch-mission/repair-skill/promotion-review/approve`
- Owner Console API: `POST /api/patch-mission/repair-skill/promotion-review/approve`
- CLI: `wls patch-mission-skill-promote <candidate_id> --human-approved [--reason ...]`
- Core API: `POST /patch-mission/repair-skill/promote`
- Owner Console API: `POST /api/patch-mission/repair-skill/promote`

### Verification Evidence

- `python -m pytest source/tests/test_outcome_learning.py -q` -> 6 passed.
- `python -m pytest source/tests/test_patch_mission.py -q` -> 3 passed.
- `python -m pytest source/tests/test_patch_mission.py -q` -> 7 passed after follow-up link.
- `python -m pytest source/tests/test_patch_mission.py source/tests/test_server.py -q` -> 11 passed.
- `python -m compileall -q source\src\wls\outcome_learning.py source\src\wls\self_model.py source\src\wls\action_candidate.py source\src\wls\runtime.py source\src\wls\cli.py source\src\wls\server.py source\src\wls\ui_server.py source\tests\test_outcome_learning.py` -> passed.
- `python -m pytest source/tests/test_outcome_learning.py source/tests/test_action_candidate.py source/tests/test_memory_influence.py source/tests/test_goal_pressure.py source/tests/test_perception.py source/tests/test_life_state.py source/tests/test_cli_health.py source/tests/test_ui_server.py source/tests/test_server.py -q` -> 34 passed.
- `python -m pytest source/tests/test_patch_mission.py source/tests/test_outcome_learning.py source/tests/test_action_candidate.py source/tests/test_memory_influence.py source/tests/test_goal_pressure.py source/tests/test_perception.py source/tests/test_life_state.py source/tests/test_cli_health.py source/tests/test_ui_server.py source/tests/test_server.py -q` -> 38 passed.
- `python -m pytest source/tests/test_patch_mission.py source/tests/test_outcome_learning.py source/tests/test_action_candidate.py source/tests/test_memory_influence.py source/tests/test_goal_pressure.py source/tests/test_perception.py source/tests/test_life_state.py source/tests/test_cli_health.py source/tests/test_ui_server.py source/tests/test_server.py -q` -> 43 passed after follow-up link.
- `git diff --check` on touched L6/L7 paths -> no whitespace errors; only existing CRLF warnings.
- `python -m pytest source/tests/test_patch_mission.py source/tests/test_server.py` -> 13 passed after approved-test-result CLI/API link.
- `python -m py_compile source/src/wls/runtime.py source/src/wls/cli.py source/src/wls/server.py source/src/wls/ui_server.py source/tests/test_patch_mission.py source/tests/test_server.py` -> passed.
- `git diff --check -- source/src/wls/runtime.py source/src/wls/cli.py source/src/wls/server.py source/src/wls/ui_server.py source/tests/test_patch_mission.py source/tests/test_server.py` -> no whitespace errors; only existing CRLF warnings.
- `python -m pytest source/tests/test_patch_mission.py -q` -> 9 passed after diff-synthesis link.
- `python -m pytest source/tests/test_server.py -q` -> 4 passed.
- `python -m pytest source/tests/test_patch_mission.py source/tests/test_server.py -q` -> 13 passed.
- `python -m py_compile source/src/wls/runtime.py source/src/wls/cli.py source/src/wls/server.py source/src/wls/ui_server.py source/tests/test_patch_mission.py source/tests/test_server.py` -> passed.
- `git diff --check -- source/src/wls/runtime.py source/src/wls/cli.py source/src/wls/server.py source/src/wls/ui_server.py source/tests/test_patch_mission.py source/tests/test_server.py docs/LIFE_LOOP_RESET_HANDOFF.md` -> no whitespace errors; only existing CRLF warnings.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_apply_patch_waits_for_owner_then_reruns_green_tests -q` -> passed.
- `python -m pytest source/tests/test_patch_mission.py -q` -> 10 passed after apply-patch link.
- `python -m pytest source/tests/test_server.py -q` -> 4 passed.
- `python -m pytest source/tests/test_patch_mission.py source/tests/test_server.py -q` -> 14 passed.
- `python -m py_compile source/src/wls/runtime.py source/src/wls/cli.py source/src/wls/server.py source/src/wls/ui_server.py source/tests/test_patch_mission.py source/tests/test_server.py` -> passed.
- `git diff --check -- source/src/wls/runtime.py source/src/wls/cli.py source/src/wls/server.py source/src/wls/ui_server.py source/tests/test_patch_mission.py source/tests/test_server.py docs/LIFE_LOOP_RESET_HANDOFF.md` -> no whitespace errors; only existing CRLF warnings.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_pr_summary_uses_diff_and_test_evidence -q` -> passed.
- `python -m pytest source/tests/test_patch_mission.py -q` -> 11 passed after PR summary link.
- `python -m pytest source/tests/test_server.py -q` -> 4 passed.
- `python -m pytest source/tests/test_patch_mission.py source/tests/test_server.py -q` -> 15 passed.
- `python -m py_compile source/src/wls/runtime.py source/src/wls/cli.py source/src/wls/server.py source/src/wls/ui_server.py source/tests/test_patch_mission.py source/tests/test_server.py` -> passed.
- `git diff --check -- source/src/wls/runtime.py source/src/wls/cli.py source/src/wls/server.py source/src/wls/ui_server.py source/tests/test_patch_mission.py source/tests/test_server.py docs/LIFE_LOOP_RESET_HANDOFF.md` -> no whitespace errors; only existing CRLF warnings.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_git_prep_reads_metadata_and_writes_outbox_checklist -q` -> passed.
- `python -m pytest source/tests/test_patch_mission.py -q` -> 12 passed after local git prep link.
- `python -m pytest source/tests/test_server.py -q` -> 4 passed.
- `python -m pytest source/tests/test_patch_mission.py source/tests/test_server.py -q` -> 16 passed.
- `python -m py_compile source/src/wls/tools.py source/src/wls/policy.py source/src/wls/runtime.py source/src/wls/cli.py source/src/wls/server.py source/src/wls/ui_server.py source/tests/test_patch_mission.py source/tests/test_server.py` -> passed.
- `git diff --check -- source/src/wls/tools.py source/src/wls/policy.py source/src/wls/runtime.py source/src/wls/cli.py source/src/wls/server.py source/src/wls/ui_server.py source/tests/test_patch_mission.py source/tests/test_server.py docs/LIFE_LOOP_RESET_HANDOFF.md` -> no whitespace errors; only existing CRLF warnings.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_commit_draft_waits_for_owner_then_commits_locally -q` -> passed.
- `python -m pytest source/tests/test_patch_mission.py -q` -> 13 passed after local commit draft link.
- `python -m pytest source/tests/test_server.py -q` -> 4 passed.
- `python -m pytest source/tests/test_patch_mission.py source/tests/test_server.py -q` -> 17 passed.
- `python -m py_compile source/src/wls/tools.py source/src/wls/policy.py source/src/wls/runtime.py source/src/wls/cli.py source/src/wls/server.py source/src/wls/ui_server.py source/tests/test_patch_mission.py source/tests/test_server.py` -> passed.
- `git diff --check -- source/src/wls/tools.py source/src/wls/policy.py source/src/wls/runtime.py source/src/wls/cli.py source/src/wls/server.py source/src/wls/ui_server.py source/tests/test_patch_mission.py source/tests/test_server.py docs/LIFE_LOOP_RESET_HANDOFF.md` -> no whitespace errors; only existing CRLF warnings.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_remote_summary_reads_local_remote_metadata_only -q` -> passed.
- `python -m pytest source/tests/test_patch_mission.py -q` -> 14 passed after remote-readiness link.
- `python -m pytest source/tests/test_server.py -q` -> 4 passed.
- `python -m pytest source/tests/test_patch_mission.py source/tests/test_server.py -q` -> 18 passed.
- `python -m py_compile source/src/wls/tools.py source/src/wls/policy.py source/src/wls/runtime.py source/src/wls/cli.py source/src/wls/server.py source/src/wls/ui_server.py source/tests/test_patch_mission.py source/tests/test_server.py` -> passed.
- `git diff --check -- source/src/wls/tools.py source/src/wls/policy.py source/src/wls/runtime.py source/src/wls/cli.py source/src/wls/server.py source/src/wls/ui_server.py source/tests/test_patch_mission.py source/tests/test_server.py docs/LIFE_LOOP_RESET_HANDOFF.md` -> no whitespace errors; only existing CRLF warnings.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_branch_draft_waits_for_owner_then_creates_local_branch -q` -> passed.
- `python -m pytest source/tests/test_patch_mission.py -q` -> 15 passed after local branch draft link.
- `python -m pytest source/tests/test_server.py -q` -> 4 passed.
- `python -m pytest source/tests/test_patch_mission.py source/tests/test_server.py -q` -> 19 passed.
- `python -m py_compile source/src/wls/tools.py source/src/wls/policy.py source/src/wls/runtime.py source/src/wls/cli.py source/src/wls/server.py source/src/wls/ui_server.py source/tests/test_patch_mission.py source/tests/test_server.py` -> passed.
- `git diff --check -- source/src/wls/tools.py source/src/wls/policy.py source/src/wls/runtime.py source/src/wls/cli.py source/src/wls/server.py source/src/wls/ui_server.py source/tests/test_patch_mission.py source/tests/test_server.py docs/LIFE_LOOP_RESET_HANDOFF.md` -> no whitespace errors; only existing CRLF warnings.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_remote_live_waits_for_owner_and_summarizes_refs -q` -> passed.
- `python -m pytest source/tests/test_patch_mission.py -q` -> 16 passed after live remote inspection link.
- `python -m pytest source/tests/test_server.py -q` -> 4 passed.
- `python -m pytest source/tests/test_patch_mission.py source/tests/test_server.py -q` -> 20 passed.
- `python -m py_compile source/src/wls/tools.py source/src/wls/policy.py source/src/wls/runtime.py source/src/wls/cli.py source/src/wls/server.py source/src/wls/ui_server.py source/tests/test_patch_mission.py source/tests/test_server.py` -> passed.
- `git diff --check -- source/src/wls/tools.py source/src/wls/policy.py source/src/wls/runtime.py source/src/wls/cli.py source/src/wls/server.py source/src/wls/ui_server.py source/tests/test_patch_mission.py source/tests/test_server.py docs/LIFE_LOOP_RESET_HANDOFF.md` -> no whitespace errors; only existing CRLF warnings.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_push_draft_waits_for_owner_then_pushes_wls_branch -q` -> passed.
- `python -m pytest source/tests/test_patch_mission.py -q` -> 17 passed after push draft link.
- `python -m pytest source/tests/test_server.py -q` -> 4 passed.
- `python -m pytest source/tests/test_patch_mission.py source/tests/test_server.py -q` -> 21 passed.
- `python -m py_compile source/src/wls/tools.py source/src/wls/policy.py source/src/wls/runtime.py source/src/wls/cli.py source/src/wls/server.py source/src/wls/ui_server.py source/tests/test_patch_mission.py source/tests/test_server.py` -> passed.
- `git diff --check -- source/src/wls/tools.py source/src/wls/policy.py source/src/wls/runtime.py source/src/wls/cli.py source/src/wls/server.py source/src/wls/ui_server.py source/tests/test_patch_mission.py source/tests/test_server.py docs/LIFE_LOOP_RESET_HANDOFF.md` -> no whitespace errors; only existing CRLF warnings.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_pr_create_draft_waits_for_owner_after_pushed_branch -q` -> passed.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_remote_summary_reads_local_remote_metadata_only source/tests/test_patch_mission.py::test_patch_mission_push_draft_waits_for_owner_then_pushes_wls_branch source/tests/test_patch_mission.py::test_patch_mission_pr_create_draft_waits_for_owner_after_pushed_branch -q` -> 3 passed.
- `python -m pytest source/tests/test_server.py -q` -> 4 passed.
- `python -m py_compile source/src/wls/tools.py source/src/wls/policy.py source/src/wls/runtime.py source/src/wls/cli.py source/tests/test_patch_mission.py source/tests/test_server.py` -> passed.
- `git diff --check -- source/src/wls/tools.py source/src/wls/policy.py source/src/wls/runtime.py source/src/wls/cli.py source/tests/test_patch_mission.py docs/LIFE_LOOP_RESET_HANDOFF.md` -> no whitespace errors; only existing CRLF warnings.
- `python -m pytest source/tests/test_patch_mission.py -q` -> timed out after 360 seconds in this run; no assertion failure output was produced. The three directly affected Patch Mission tests above passed.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_pr_status_from_url_waits_for_owner_before_github_read -q` -> passed.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_pr_create_draft_waits_for_owner_after_pushed_branch source/tests/test_patch_mission.py::test_patch_mission_pr_status_from_url_waits_for_owner_before_github_read source/tests/test_patch_mission.py::test_github_pr_status_tool_extracts_ci_failure_summary -q` -> 3 passed.
- `python -m pytest source/tests/test_server.py -q` -> 4 passed.
- `python -m py_compile source/src/wls/tools.py source/src/wls/policy.py source/src/wls/runtime.py source/src/wls/cli.py source/tests/test_patch_mission.py` -> passed.
- `git diff --check -- source/src/wls/tools.py source/src/wls/policy.py source/src/wls/runtime.py source/src/wls/cli.py source/tests/test_patch_mission.py docs/LIFE_LOOP_RESET_HANDOFF.md` -> no whitespace errors; only existing CRLF warnings.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_ci_fix_plan_consumes_pr_status_failure_evidence -q` -> passed.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_pr_status_from_url_waits_for_owner_before_github_read source/tests/test_patch_mission.py::test_patch_mission_ci_fix_plan_consumes_pr_status_failure_evidence source/tests/test_patch_mission.py::test_github_pr_status_tool_extracts_ci_failure_summary -q` -> 3 passed.
- `python -m pytest source/tests/test_server.py -q` -> 4 passed.
- `python -m py_compile source/src/wls/runtime.py source/src/wls/cli.py source/tests/test_patch_mission.py` -> passed.
- `git diff --check -- source/src/wls/runtime.py source/src/wls/cli.py source/tests/test_patch_mission.py docs/LIFE_LOOP_RESET_HANDOFF.md` -> no whitespace errors; only existing CRLF warnings.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_pr_status_from_url_waits_for_owner_before_github_read source/tests/test_patch_mission.py::test_patch_mission_pr_update_push_draft_updates_existing_pr_branch source/tests/test_patch_mission.py::test_patch_mission_pr_update_next_reopens_ci_fix_plan_when_failures_remain -q` -> 3 passed.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_pr_status_from_url_waits_for_owner_before_github_read source/tests/test_patch_mission.py::test_patch_mission_pr_update_push_draft_updates_existing_pr_branch source/tests/test_patch_mission.py::test_patch_mission_pr_update_next_reopens_ci_fix_plan_when_failures_remain source/tests/test_patch_mission.py::test_github_pr_status_tool_extracts_ci_failure_summary -q` -> 4 passed.
- `python -m pytest source/tests/test_server.py -q` -> 4 passed.
- `python -m py_compile source/src/wls/runtime.py source/tests/test_patch_mission.py` -> passed.
- `git diff --check -- source/src/wls/runtime.py source/tests/test_patch_mission.py docs/LIFE_LOOP_RESET_HANDOFF.md` -> no whitespace errors; only existing CRLF warnings.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_pr_update_push_draft_updates_existing_pr_branch source/tests/test_patch_mission.py::test_patch_mission_pr_update_next_reopens_ci_fix_plan_when_failures_remain -q` -> 2 passed.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_pr_update_push_draft_updates_existing_pr_branch source/tests/test_patch_mission.py::test_patch_mission_pr_update_next_reopens_ci_fix_plan_when_failures_remain -q` -> 2 passed after `resume-next` link.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_pr_update_push_draft_updates_existing_pr_branch source/tests/test_patch_mission.py::test_patch_mission_pr_update_next_reopens_ci_fix_plan_when_failures_remain source/tests/test_patch_mission.py::test_github_pr_status_tool_extracts_ci_failure_summary -q` -> 3 passed.
- `python -m pytest source/tests/test_server.py -q` -> 4 passed.
- `python -m py_compile source/src/wls/runtime.py source/src/wls/cli.py source/tests/test_patch_mission.py` -> passed.
- `git diff --check -- source/src/wls/runtime.py source/src/wls/cli.py source/tests/test_patch_mission.py docs/LIFE_LOOP_RESET_HANDOFF.md` -> no whitespace errors; only existing CRLF warnings.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_pr_update_push_draft_updates_existing_pr_branch source/tests/test_patch_mission.py::test_patch_mission_pr_update_next_reopens_ci_fix_plan_when_failures_remain source/tests/test_patch_mission.py::test_github_pr_status_tool_extracts_ci_failure_summary -q` -> 3 passed after final `resume-next` regression.
- `python -m pytest source/tests/test_server.py -q` -> 4 passed after final `resume-next` regression.
- `git diff --check -- source/src/wls/runtime.py source/src/wls/cli.py source/tests/test_patch_mission.py docs/LIFE_LOOP_RESET_HANDOFF.md` -> no whitespace errors; only existing CRLF warnings for `source/src/wls/cli.py` and `source/src/wls/runtime.py`.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_resume_next_continues_local_git_publish_prep source/tests/test_patch_mission.py::test_patch_mission_resume_next_prepares_pr_draft_after_push -q` -> 2 passed after publishing-chain `resume-next` link.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_pr_update_push_draft_updates_existing_pr_branch source/tests/test_patch_mission.py::test_patch_mission_pr_update_next_reopens_ci_fix_plan_when_failures_remain source/tests/test_patch_mission.py::test_patch_mission_resume_next_continues_local_git_publish_prep source/tests/test_patch_mission.py::test_patch_mission_resume_next_prepares_pr_draft_after_push source/tests/test_patch_mission.py::test_github_pr_status_tool_extracts_ci_failure_summary -q` -> 5 passed after publishing-chain `resume-next` link.
- `python -m pytest source/tests/test_server.py -q` -> 4 passed after publishing-chain `resume-next` link.
- `python -m py_compile source/src/wls/runtime.py source/src/wls/cli.py source/tests/test_patch_mission.py` -> passed after publishing-chain `resume-next` link.
- `git diff --check -- source/src/wls/runtime.py source/src/wls/cli.py source/tests/test_patch_mission.py docs/LIFE_LOOP_RESET_HANDOFF.md` -> no whitespace errors; only existing CRLF warnings for `source/src/wls/cli.py` and `source/src/wls/runtime.py`.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_resume_next_captures_ci_logs_before_fix_plan source/tests/test_patch_mission.py::test_github_pr_status_tool_extracts_ci_failure_summary -q` -> 2 passed after CI log evidence link.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_ci_fix_plan_consumes_pr_status_failure_evidence source/tests/test_patch_mission.py::test_patch_mission_resume_next_captures_ci_logs_before_fix_plan source/tests/test_patch_mission.py::test_patch_mission_ci_next_action_creates_selected_local_test_action source/tests/test_patch_mission.py::test_patch_mission_pr_update_push_draft_updates_existing_pr_branch source/tests/test_patch_mission.py::test_patch_mission_pr_update_next_reopens_ci_fix_plan_when_failures_remain source/tests/test_patch_mission.py::test_patch_mission_resume_next_continues_local_git_publish_prep source/tests/test_patch_mission.py::test_patch_mission_resume_next_prepares_pr_draft_after_push source/tests/test_patch_mission.py::test_github_pr_status_tool_extracts_ci_failure_summary -q` -> 8 passed after CI log evidence link.
- `python -m pytest source/tests/test_server.py -q` -> 4 passed after CI log evidence link.
- `python -m py_compile source/src/wls/tools.py source/src/wls/policy.py source/src/wls/runtime.py source/src/wls/cli.py source/tests/test_patch_mission.py` -> passed after CI log evidence link.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_resume_next_captures_ci_logs_before_fix_plan source/tests/test_patch_mission.py::test_patch_mission_ci_next_action_creates_selected_local_test_action source/tests/test_patch_mission.py::test_patch_mission_pr_update_next_reopens_ci_fix_plan_when_failures_remain -q` -> 3 passed after narrow CI reproduction link.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_ci_fix_plan_consumes_pr_status_failure_evidence source/tests/test_patch_mission.py::test_patch_mission_resume_next_captures_ci_logs_before_fix_plan source/tests/test_patch_mission.py::test_patch_mission_ci_next_action_creates_selected_local_test_action source/tests/test_patch_mission.py::test_patch_mission_pr_update_push_draft_updates_existing_pr_branch source/tests/test_patch_mission.py::test_patch_mission_pr_update_next_reopens_ci_fix_plan_when_failures_remain source/tests/test_patch_mission.py::test_patch_mission_resume_next_continues_local_git_publish_prep source/tests/test_patch_mission.py::test_patch_mission_resume_next_prepares_pr_draft_after_push source/tests/test_patch_mission.py::test_github_pr_status_tool_extracts_ci_failure_summary -q` -> 8 passed after narrow CI reproduction link.
- `python -m pytest source/tests/test_server.py -q` -> 4 passed after narrow CI reproduction link.
- `python -m py_compile source/src/wls/runtime.py source/src/wls/tools.py source/src/wls/policy.py source/src/wls/cli.py source/tests/test_patch_mission.py` -> passed after narrow CI reproduction link.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_ci_next_action_creates_selected_local_test_action -q` -> passed after failure-learning link; verifies cause/fix/regression evidence, procedural memory, and draft citation.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_resume_next_captures_ci_logs_before_fix_plan source/tests/test_patch_mission.py::test_patch_mission_ci_next_action_creates_selected_local_test_action source/tests/test_patch_mission.py::test_patch_mission_pr_update_next_reopens_ci_fix_plan_when_failures_remain source/tests/test_patch_mission.py::test_patch_mission_pr_update_push_draft_updates_existing_pr_branch source/tests/test_patch_mission.py::test_github_pr_status_tool_extracts_ci_failure_summary -q` -> 5 passed after failure-learning link.
- `python -m pytest source/tests/test_server.py -q` -> 4 passed after failure-learning link.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_ci_next_action_creates_selected_local_test_action -q` -> passed after failure-memory influence link; verifies the next similar CI plan and draft cite the prior learning memory ID.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_resume_next_captures_ci_logs_before_fix_plan source/tests/test_patch_mission.py::test_patch_mission_ci_next_action_creates_selected_local_test_action source/tests/test_patch_mission.py::test_patch_mission_pr_update_next_reopens_ci_fix_plan_when_failures_remain source/tests/test_patch_mission.py::test_patch_mission_pr_update_push_draft_updates_existing_pr_branch source/tests/test_patch_mission.py::test_github_pr_status_tool_extracts_ci_failure_summary -q` -> 5 passed after failure-memory influence link.
- `python -m pytest source/tests/test_server.py -q` -> 4 passed after failure-memory influence link.
- `python -m py_compile source/src/wls/runtime.py source/tests/test_patch_mission.py` -> passed after failure-memory influence link.
- `python -m py_compile source/src/wls/runtime.py source/tests/test_patch_mission.py` -> passed after repair skill-candidate link.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_repeated_success_creates_repair_skill_candidate -q` -> passed; verifies two complete CI-sourced literal repairs create one `PROPOSED` `patch_mission_repair_skill` candidate and no `skills` rows.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_ci_next_action_creates_selected_local_test_action source/tests/test_patch_mission.py::test_patch_mission_repeated_success_creates_repair_skill_candidate source/tests/test_patch_mission.py::test_patch_mission_resume_next_captures_ci_logs_before_fix_plan source/tests/test_patch_mission.py::test_patch_mission_pr_update_next_reopens_ci_fix_plan_when_failures_remain source/tests/test_patch_mission.py::test_github_pr_status_tool_extracts_ci_failure_summary -q` -> 5 passed after repair skill-candidate link.
- `python -m pytest source/tests/test_server.py -q` -> 4 passed after repair skill-candidate link.
- `python -m py_compile source/src/wls/runtime.py source/src/wls/cli.py source/src/wls/server.py source/src/wls/ui_server.py source/tests/test_patch_mission.py source/tests/test_server.py` -> passed after repair candidate replay link.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_repeated_success_creates_repair_skill_candidate -q` -> passed after repair candidate replay link; verifies replay receipt, no candidate status transition, no active skill creation, no command/external execution, and later plan/draft advisory citation.
- `python -m pytest source/tests/test_server.py -q` -> 5 passed after repair candidate replay API link.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_ci_next_action_creates_selected_local_test_action source/tests/test_patch_mission.py::test_patch_mission_repeated_success_creates_repair_skill_candidate source/tests/test_patch_mission.py::test_patch_mission_resume_next_captures_ci_logs_before_fix_plan source/tests/test_patch_mission.py::test_patch_mission_pr_update_next_reopens_ci_fix_plan_when_failures_remain source/tests/test_patch_mission.py::test_github_pr_status_tool_extracts_ci_failure_summary -q` -> 5 passed after repair candidate replay link.
- `git diff --check -- source/src/wls/runtime.py source/src/wls/cli.py source/src/wls/server.py source/src/wls/ui_server.py source/tests/test_patch_mission.py source/tests/test_server.py docs/LIFE_LOOP_RESET_HANDOFF.md` -> no whitespace errors; only existing CRLF warnings.
- `python -m py_compile source/src/wls/runtime.py source/src/wls/cli.py source/src/wls/server.py source/src/wls/ui_server.py source/tests/test_patch_mission.py source/tests/test_server.py` -> passed after repair candidate sandbox link.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_repeated_success_creates_repair_skill_candidate -q` -> passed after repair candidate sandbox link; verifies missing owner approval is rejected, approved sandbox runs red/green pytest in disposable repo, candidate moves to `SANDBOXED`, experiment_json is persisted, artifacts exist under sandbox, and no skill is created.
- `python -m pytest source/tests/test_server.py -q` -> 6 passed after repair candidate sandbox API link.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_ci_next_action_creates_selected_local_test_action source/tests/test_patch_mission.py::test_patch_mission_repeated_success_creates_repair_skill_candidate source/tests/test_patch_mission.py::test_patch_mission_resume_next_captures_ci_logs_before_fix_plan source/tests/test_patch_mission.py::test_patch_mission_pr_update_next_reopens_ci_fix_plan_when_failures_remain source/tests/test_patch_mission.py::test_github_pr_status_tool_extracts_ci_failure_summary -q` -> 5 passed after repair candidate sandbox link.
- `git diff --check -- source/src/wls/runtime.py source/src/wls/cli.py source/src/wls/server.py source/src/wls/ui_server.py source/tests/test_patch_mission.py source/tests/test_server.py docs/LIFE_LOOP_RESET_HANDOFF.md` -> no whitespace errors; only existing CRLF warnings.
- `python -m py_compile source/src/wls/runtime.py source/src/wls/cli.py source/src/wls/server.py source/src/wls/ui_server.py source/tests/test_patch_mission.py source/tests/test_server.py` -> passed after repair candidate validation link.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_repeated_success_creates_repair_skill_candidate -q` -> passed after repair candidate validation link; verifies missing human approval is rejected, manifest/result digests match sandbox evidence, candidate moves from `SANDBOXED` to `VALIDATED`, result_json is persisted, and no skill is created.
- `python -m pytest source/tests/test_server.py -q` -> 7 passed after repair candidate validation API link.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_ci_next_action_creates_selected_local_test_action source/tests/test_patch_mission.py::test_patch_mission_repeated_success_creates_repair_skill_candidate source/tests/test_patch_mission.py::test_patch_mission_resume_next_captures_ci_logs_before_fix_plan source/tests/test_patch_mission.py::test_patch_mission_pr_update_next_reopens_ci_fix_plan_when_failures_remain source/tests/test_patch_mission.py::test_github_pr_status_tool_extracts_ci_failure_summary -q` -> 5 passed after repair candidate validation link.
- `git diff --check -- source/src/wls/runtime.py source/src/wls/cli.py source/src/wls/server.py source/src/wls/ui_server.py source/tests/test_patch_mission.py source/tests/test_server.py docs/LIFE_LOOP_RESET_HANDOFF.md` -> no whitespace errors; only existing CRLF warnings.
- `python -m py_compile source/src/wls/runtime.py source/src/wls/cli.py source/src/wls/server.py source/src/wls/ui_server.py source/tests/test_patch_mission.py source/tests/test_server.py` -> passed after repair skill proposal link.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_repeated_success_creates_repair_skill_candidate -q` -> passed after repair skill proposal link; verifies validated candidate creates one `PROPOSED` declarative skill, repeated proposal requests dedupe, source IDs include candidate/validation evidence, and `runtime.skills.active()` remains empty.
- `python -m pytest source/tests/test_server.py -q` -> 8 passed after repair skill proposal API link.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_ci_next_action_creates_selected_local_test_action source/tests/test_patch_mission.py::test_patch_mission_repeated_success_creates_repair_skill_candidate source/tests/test_patch_mission.py::test_patch_mission_resume_next_captures_ci_logs_before_fix_plan source/tests/test_patch_mission.py::test_patch_mission_pr_update_next_reopens_ci_fix_plan_when_failures_remain source/tests/test_patch_mission.py::test_github_pr_status_tool_extracts_ci_failure_summary -q` -> 5 passed after repair skill proposal link.
- `git diff --check -- source/src/wls/runtime.py source/src/wls/cli.py source/src/wls/server.py source/src/wls/ui_server.py source/tests/test_patch_mission.py source/tests/test_server.py docs/LIFE_LOOP_RESET_HANDOFF.md` -> no whitespace errors; only existing CRLF warnings.
- `python -m py_compile source/src/wls/runtime.py source/src/wls/cli.py source/src/wls/server.py source/src/wls/ui_server.py source/tests/test_patch_mission.py source/tests/test_server.py` -> passed after repair skill sandbox-start link.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_repeated_success_creates_repair_skill_candidate -q` -> passed after repair skill sandbox-start link; verifies the proposed skill moves to `SANDBOXED`, `skill_experiments` has a RUNNING manifest bound to the validated source candidate, and `runtime.skills.active()` remains empty.
- `python -m pytest source/tests/test_server.py -q` -> 9 passed after repair skill sandbox-start API link.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_ci_next_action_creates_selected_local_test_action source/tests/test_patch_mission.py::test_patch_mission_repeated_success_creates_repair_skill_candidate source/tests/test_patch_mission.py::test_patch_mission_resume_next_captures_ci_logs_before_fix_plan source/tests/test_patch_mission.py::test_patch_mission_pr_update_next_reopens_ci_fix_plan_when_failures_remain source/tests/test_patch_mission.py::test_github_pr_status_tool_extracts_ci_failure_summary -q` -> 5 passed after repair skill sandbox-start link.
- `git diff --check -- source/src/wls/runtime.py source/src/wls/cli.py source/src/wls/server.py source/src/wls/ui_server.py source/tests/test_patch_mission.py source/tests/test_server.py docs/LIFE_LOOP_RESET_HANDOFF.md` -> no whitespace errors; only existing CRLF warnings.
- `python -m py_compile source/src/wls/runtime.py source/src/wls/cli.py source/src/wls/server.py source/src/wls/ui_server.py source/tests/test_patch_mission.py source/tests/test_server.py` -> passed after repair skill validation link.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_repeated_success_creates_repair_skill_candidate -q` -> passed after repair skill validation link; verifies `skill_experiments.result_json` is persisted, the experiment becomes `PASSED`, skill moves from `SANDBOXED` to `VALIDATED`, and `runtime.skills.active()` remains empty.
- `python -m pytest source/tests/test_server.py -q` -> 10 passed after repair skill validation API link.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_ci_next_action_creates_selected_local_test_action source/tests/test_patch_mission.py::test_patch_mission_repeated_success_creates_repair_skill_candidate source/tests/test_patch_mission.py::test_patch_mission_resume_next_captures_ci_logs_before_fix_plan source/tests/test_patch_mission.py::test_patch_mission_pr_update_next_reopens_ci_fix_plan_when_failures_remain source/tests/test_patch_mission.py::test_github_pr_status_tool_extracts_ci_failure_summary -q` -> 5 passed after repair skill validation link.
- `git diff --check -- source/src/wls/runtime.py source/src/wls/cli.py source/src/wls/server.py source/src/wls/ui_server.py source/tests/test_patch_mission.py source/tests/test_server.py docs/LIFE_LOOP_RESET_HANDOFF.md` -> no whitespace errors; only existing CRLF warnings.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_pr_update_push_draft_updates_existing_pr_branch -q` -> passed, including post-update PR status recheck and outbox comparison.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_ci_next_action_creates_selected_local_test_action source/tests/test_patch_mission.py::test_patch_mission_pr_update_push_draft_updates_existing_pr_branch source/tests/test_patch_mission.py::test_github_pr_status_tool_extracts_ci_failure_summary -q` -> 3 passed.
- `python -m pytest source/tests/test_server.py -q` -> 4 passed.
- `python -m py_compile source/src/wls/runtime.py source/src/wls/cli.py source/tests/test_patch_mission.py` -> passed.
- `git diff --check -- source/src/wls/runtime.py source/src/wls/cli.py source/tests/test_patch_mission.py docs/LIFE_LOOP_RESET_HANDOFF.md` -> no whitespace errors; only existing CRLF warnings.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_ci_next_action_creates_selected_local_test_action -q` -> passed, including CI-triggered local pytest -> from-test-result -> apply-patch -> passing test -> pr-summary provenance.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_pr_status_from_url_waits_for_owner_before_github_read source/tests/test_patch_mission.py::test_patch_mission_ci_fix_plan_consumes_pr_status_failure_evidence source/tests/test_patch_mission.py::test_patch_mission_ci_next_action_creates_selected_local_test_action source/tests/test_patch_mission.py::test_github_pr_status_tool_extracts_ci_failure_summary -q` -> 4 passed.
- `python -m pytest source/tests/test_server.py -q` -> 4 passed.
- `python -m py_compile source/src/wls/runtime.py source/tests/test_patch_mission.py` -> passed.
- `git diff --check -- source/src/wls/runtime.py source/tests/test_patch_mission.py docs/LIFE_LOOP_RESET_HANDOFF.md` -> no whitespace errors; only existing CRLF warnings.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_pr_update_push_draft_updates_existing_pr_branch -q` -> passed.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_ci_next_action_creates_selected_local_test_action source/tests/test_patch_mission.py::test_patch_mission_pr_update_push_draft_updates_existing_pr_branch source/tests/test_patch_mission.py::test_github_pr_status_tool_extracts_ci_failure_summary -q` -> 3 passed.
- `python -m pytest source/tests/test_server.py -q` -> 4 passed.
- `python -m py_compile source/src/wls/runtime.py source/src/wls/cli.py source/tests/test_patch_mission.py` -> passed.
- `git diff --check -- source/src/wls/runtime.py source/src/wls/cli.py source/tests/test_patch_mission.py docs/LIFE_LOOP_RESET_HANDOFF.md` -> no whitespace errors; only existing CRLF warnings.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_ci_next_action_creates_selected_local_test_action -q` -> passed.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_pr_status_from_url_waits_for_owner_before_github_read source/tests/test_patch_mission.py::test_patch_mission_ci_fix_plan_consumes_pr_status_failure_evidence source/tests/test_patch_mission.py::test_patch_mission_ci_next_action_creates_selected_local_test_action source/tests/test_patch_mission.py::test_github_pr_status_tool_extracts_ci_failure_summary -q` -> 4 passed.
- `python -m pytest source/tests/test_server.py -q` -> 4 passed.
- `python -m py_compile source/src/wls/runtime.py source/src/wls/cli.py source/tests/test_patch_mission.py` -> passed.
- `git diff --check -- source/src/wls/runtime.py source/src/wls/cli.py source/tests/test_patch_mission.py docs/LIFE_LOOP_RESET_HANDOFF.md` -> no whitespace errors; only existing CRLF warnings.
- `python -m pytest source/tests/test_life_state.py -vv` -> 2 passed.
- `python -m pytest source/tests/test_perception.py -vv` -> 2 passed.
- `python -m pytest source/tests/test_goal_pressure.py -vv` -> 4 passed.
- `python -m pytest source/tests/test_memory_influence.py -vv` -> 2 passed.
- `python -m pytest source/tests/test_action_candidate.py -vv` -> 3 passed.
- `python -m pytest source/tests/test_outcome_learning.py -vv -s --setup-show` -> timed out after 120 seconds in this run; no failure output was produced.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_repeated_success_creates_repair_skill_candidate -q` -> passed, including missing-approval rejection and owner-approved skill transition to `APPROVED` without actions, commands, promotion, repo writes, pushes, or PRs.
- `python -m pytest source/tests/test_server.py -q` -> 11 passed after repair skill approval endpoint.
- `python -m py_compile source/src/wls/runtime.py source/src/wls/cli.py source/src/wls/server.py source/src/wls/ui_server.py source/tests/test_patch_mission.py source/tests/test_server.py` -> passed.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_ci_next_action_creates_selected_local_test_action source/tests/test_patch_mission.py::test_patch_mission_repeated_success_creates_repair_skill_candidate source/tests/test_patch_mission.py::test_patch_mission_resume_next_captures_ci_logs_before_fix_plan source/tests/test_patch_mission.py::test_patch_mission_pr_update_next_reopens_ci_fix_plan_when_failures_remain source/tests/test_patch_mission.py::test_github_pr_status_tool_extracts_ci_failure_summary -q` -> first run timed out at 184 seconds; rerun with a 420 second timeout passed, 5 passed.
- `git diff --check -- source/src/wls/runtime.py source/src/wls/cli.py source/src/wls/server.py source/src/wls/ui_server.py source/tests/test_patch_mission.py source/tests/test_server.py docs/LIFE_LOOP_RESET_HANDOFF.md` -> no whitespace errors; only existing CRLF warnings.
- `python -m py_compile source/src/wls/runtime.py source/tests/test_patch_mission.py` -> passed.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_repeated_success_creates_repair_skill_candidate -q` -> passed, including `APPROVED` repair skill matching into `ci-fix-plan` and `from-test-result` as advisory context while keeping skill status `APPROVED`, `use_count` unchanged, and no push/PR action created.
- `python -m py_compile source/src/wls/runtime.py source/tests/test_patch_mission.py` -> passed.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_repeated_success_creates_repair_skill_candidate -q` -> passed, including matched `APPROVED` repair skill influence on `ci-fix-plan` next-step reason and `from-test-result` patch-draft rationale while keeping skill status/use count unchanged and creating no push/PR action.
- `python -m py_compile source/src/wls/runtime.py source/tests/test_patch_mission.py` -> passed.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_repeated_success_creates_repair_skill_candidate -q` -> passed, including matched `APPROVED` repair skill conversion into an owner-gated `ci-next-action` pytest action candidate with `WAITING_APPROVAL` status, candidate metadata, no `ActionSpec.skill_id`, unchanged skill use count, and no repo mutation, push, or PR.
- `python -m py_compile source/src/wls/runtime.py source/tests/test_patch_mission.py` -> passed.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_repeated_success_creates_repair_skill_candidate -q` -> passed, including no skill use count change while the skill-derived action is pending, one bounded approved-skill outcome after owner-approved successful pytest execution, `use_count` increment from 0 to 1, and no promotion, repo write, push, or PR.
- `python -m py_compile source/src/wls/runtime.py source/tests/test_patch_mission.py` -> passed.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_repeated_success_creates_repair_skill_candidate -q` -> passed, including two owner-approved successful skill-derived actions creating one `PROPOSED` `patch_mission_repair_skill_promotion_review` candidate while the skill remains `APPROVED` with no promotion, approval bypass, repo write, push, or PR.
- `python -m py_compile source/src/wls/runtime.py source/src/wls/cli.py source/src/wls/server.py source/src/wls/ui_server.py source/tests/test_patch_mission.py source/tests/test_server.py` -> passed.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_repeated_success_creates_repair_skill_candidate -q` -> passed, including missing human approval rejection and owner-approved promotion-review decision moving the review candidate to `APPROVED` while the skill remains `APPROVED` with no promotion, command execution, repo mutation, push, or PR.
- `python -m pytest source/tests/test_server.py -q` -> 12 passed after promotion-review approval endpoint.
- `python -m py_compile source/src/wls/runtime.py source/src/wls/cli.py source/src/wls/server.py source/src/wls/ui_server.py source/tests/test_patch_mission.py source/tests/test_server.py` -> passed.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_repeated_success_creates_repair_skill_candidate -q` -> passed, including missing human approval rejection and owner-approved promotion execution moving skill and review candidate to `PROMOTED` while creating no commands, repo writes, pushes, or PRs.
- `python -m pytest source/tests/test_server.py -q` -> 13 passed after repair skill promotion endpoint.
- `python -m py_compile source/src/wls/runtime.py source/tests/test_patch_mission.py` -> passed after promoted repair skill planning metadata link.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_repeated_success_creates_repair_skill_candidate -q` -> passed, including a post-promotion Patch Mission where `ci-fix-plan` cites the `PROMOTED` skill as higher-confidence advisory context and `ci-next-action` creates a `WAITING_APPROVAL` pytest action with promoted-skill metadata but no skill execution, use-count change, repo mutation, push, or PR.
- `python -m pytest source/tests/test_server.py -q` -> 13 passed after promoted repair skill planning metadata link.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_ci_next_action_creates_selected_local_test_action source/tests/test_patch_mission.py::test_patch_mission_repeated_success_creates_repair_skill_candidate source/tests/test_patch_mission.py::test_patch_mission_resume_next_captures_ci_logs_before_fix_plan source/tests/test_patch_mission.py::test_patch_mission_pr_update_next_reopens_ci_fix_plan_when_failures_remain source/tests/test_patch_mission.py::test_github_pr_status_tool_extracts_ci_failure_summary -q` -> 5 passed after promoted repair skill planning metadata link.
- `python -m py_compile source/src/wls/runtime.py source/tests/test_patch_mission.py` -> passed after promoted repair skill outcome-learning link.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_repeated_success_creates_repair_skill_candidate -q` -> passed, including pending/unapproved, rejected, and failed promoted-skill candidates not counting as execution; only the separately owner-approved successful promoted-skill action records one promoted outcome and increments use count.
- `python -m pytest source/tests/test_server.py -q` -> 13 passed after promoted repair skill outcome-learning link.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_ci_next_action_creates_selected_local_test_action source/tests/test_patch_mission.py::test_patch_mission_repeated_success_creates_repair_skill_candidate source/tests/test_patch_mission.py::test_patch_mission_resume_next_captures_ci_logs_before_fix_plan source/tests/test_patch_mission.py::test_patch_mission_pr_update_next_reopens_ci_fix_plan_when_failures_remain source/tests/test_patch_mission.py::test_github_pr_status_tool_extracts_ci_failure_summary -q` -> 5 passed after promoted repair skill outcome-learning link.
- `python -m py_compile source/src/wls/runtime.py source/tests/test_patch_mission.py` -> passed after promoted outcome-informed planning link.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_repeated_success_creates_repair_skill_candidate -q` -> passed, including a later Patch Mission plan/candidate showing `mixed_outcome` confidence after one owner-approved promoted success plus one rejected and one failed promoted-skill candidate, while the next action remains `WAITING_APPROVAL` with no `ActionSpec.skill_id`.
- `python -m pytest source/tests/test_server.py -q` -> 13 passed after promoted outcome-informed planning link.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_ci_next_action_creates_selected_local_test_action source/tests/test_patch_mission.py::test_patch_mission_repeated_success_creates_repair_skill_candidate source/tests/test_patch_mission.py::test_patch_mission_resume_next_captures_ci_logs_before_fix_plan source/tests/test_patch_mission.py::test_patch_mission_pr_update_next_reopens_ci_fix_plan_when_failures_remain source/tests/test_patch_mission.py::test_github_pr_status_tool_extracts_ci_failure_summary -q` -> 5 passed after promoted outcome-informed planning link.
- `python -m py_compile source/src/wls/runtime.py source/tests/test_patch_mission.py` -> passed after promoted confidence-based local action selection link.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_repeated_success_creates_repair_skill_candidate -q` -> passed, including `mixed_outcome` changing the next selected local action from `test tests/test_demo.py::test_demo` to read-only `inspect-file tests/test_demo.py`, then executing the existing `read_file` action without incrementing promoted skill use count.
- `python -m pytest source/tests/test_server.py -q` -> 13 passed after promoted confidence-based local action selection link.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_ci_next_action_creates_selected_local_test_action source/tests/test_patch_mission.py::test_patch_mission_repeated_success_creates_repair_skill_candidate source/tests/test_patch_mission.py::test_patch_mission_resume_next_captures_ci_logs_before_fix_plan source/tests/test_patch_mission.py::test_patch_mission_pr_update_next_reopens_ci_fix_plan_when_failures_remain source/tests/test_patch_mission.py::test_github_pr_status_tool_extracts_ci_failure_summary -q` -> 5 passed after promoted confidence-based local action selection link.
- `python -m py_compile source/src/wls/runtime.py source/tests/test_patch_mission.py` -> passed after outcome-supported direct path link.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_repeated_success_creates_repair_skill_candidate -q` -> passed, including `outcome_supported` preserving the direct narrow pytest action as `WAITING_APPROVAL`, with promoted confidence metadata and no use-count increment before owner-approved execution.
- `python -m pytest source/tests/test_server.py -q` -> 13 passed after outcome-supported direct path link.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_ci_next_action_creates_selected_local_test_action source/tests/test_patch_mission.py::test_patch_mission_repeated_success_creates_repair_skill_candidate source/tests/test_patch_mission.py::test_patch_mission_resume_next_captures_ci_logs_before_fix_plan source/tests/test_patch_mission.py::test_patch_mission_pr_update_next_reopens_ci_fix_plan_when_failures_remain source/tests/test_patch_mission.py::test_github_pr_status_tool_extracts_ci_failure_summary -q` -> 5 passed after outcome-supported direct path link.
- `python -m py_compile source/src/wls/runtime.py source/tests/test_patch_mission.py` -> passed after promoted confidence continuity link.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_repeated_success_creates_repair_skill_candidate -q` -> passed, including continuity showing `outcome_supported_direct_test` while a direct pytest action waits for approval, continuity showing `safety_fallback_inspect_file` after read-only fallback inspection, and `resume-next` routing that fallback to owner-gated `python -m pytest tests/test_demo.py`.
- `python -m pytest source/tests/test_server.py -q` -> 13 passed after promoted confidence continuity link.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_ci_next_action_creates_selected_local_test_action source/tests/test_patch_mission.py::test_patch_mission_repeated_success_creates_repair_skill_candidate source/tests/test_patch_mission.py::test_patch_mission_resume_next_captures_ci_logs_before_fix_plan source/tests/test_patch_mission.py::test_patch_mission_pr_update_next_reopens_ci_fix_plan_when_failures_remain source/tests/test_patch_mission.py::test_github_pr_status_tool_extracts_ci_failure_summary -q` -> 5 passed after promoted confidence continuity link.
- `python -m py_compile source/src/wls/runtime.py source/tests/test_patch_mission.py` -> passed after fallback inspection target-narrowing link.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_repeated_success_creates_repair_skill_candidate -q` -> passed, including fallback `inspect-file tests/test_demo.py` reading the test file and `resume-next` restoring the safer narrow pytest target `tests/test_demo.py::test_demo` only after confirming the test function exists in the inspected text.
- `python -m pytest source/tests/test_server.py -q` -> 13 passed after fallback inspection target-narrowing link.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_ci_next_action_creates_selected_local_test_action source/tests/test_patch_mission.py::test_patch_mission_repeated_success_creates_repair_skill_candidate source/tests/test_patch_mission.py::test_patch_mission_resume_next_captures_ci_logs_before_fix_plan source/tests/test_patch_mission.py::test_patch_mission_pr_update_next_reopens_ci_fix_plan_when_failures_remain source/tests/test_patch_mission.py::test_github_pr_status_tool_extracts_ci_failure_summary -q` -> 5 passed after fallback inspection target-narrowing link.
- `python -m py_compile source/src/wls/runtime.py source/tests/test_patch_mission.py` -> passed after fallback context draft/learning link.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_repeated_success_creates_repair_skill_candidate -q` -> passed, including fallback-restored `from-test-result` draft and failure-learning memory citing the fallback inspect action, `mixed_outcome` promoted confidence, `safety_fallback_inspect_file`, and `nodeid_restored_from_ci_and_inspection`.
- `python -m pytest source/tests/test_server.py -q` -> 13 passed after fallback context draft/learning link.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_ci_next_action_creates_selected_local_test_action source/tests/test_patch_mission.py::test_patch_mission_repeated_success_creates_repair_skill_candidate source/tests/test_patch_mission.py::test_patch_mission_resume_next_captures_ci_logs_before_fix_plan source/tests/test_patch_mission.py::test_patch_mission_pr_update_next_reopens_ci_fix_plan_when_failures_remain source/tests/test_patch_mission.py::test_github_pr_status_tool_extracts_ci_failure_summary -q` -> 5 passed after fallback context draft/learning link.
- `python -m py_compile source/src/wls/runtime.py source/tests/test_patch_mission.py` -> passed after promoted confidence recovery link.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_repeated_success_creates_repair_skill_candidate -q` -> passed, including an owner-approved fallback-restored repair completing through draft/apply/verify/summary, recording one promoted skill recovery outcome, preserving two failed/rejected promoted candidates, and making the next mission plan with `recovering_mixed_outcome`, `recovery_successes` 1, and read-only `inspect-file`.
- `python -m pytest source/tests/test_server.py -q` -> 13 passed after promoted confidence recovery link.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_from_approved_test_result_creates_outbox_draft_action source/tests/test_patch_mission.py::test_patch_mission_apply_patch_waits_for_owner_then_reruns_green_tests source/tests/test_patch_mission.py::test_patch_mission_pr_summary_uses_diff_and_test_evidence source/tests/test_patch_mission.py::test_patch_mission_pr_update_push_draft_updates_existing_pr_branch source/tests/test_patch_mission.py::test_patch_mission_resume_next_captures_ci_logs_before_fix_plan -q` -> 5 passed after promoted confidence recovery link.
- `python -m py_compile source/src/wls/runtime.py source/tests/test_patch_mission.py` -> passed after recovered confidence direct-path proof.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_repeated_success_creates_repair_skill_candidate -q` -> passed, including two owner-approved fallback-restored successful repairs offsetting two failed/rejected promoted candidates, shifting later planning to `recovered_outcome_supported`, preserving the failed/rejected counts, and creating a direct narrow pytest action that remains `WAITING_APPROVAL` with continuity explaining that recovered confidence restored the direct path.
- `python -m pytest source/tests/test_server.py -q` -> 13 passed after recovered confidence direct-path proof.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_from_approved_test_result_creates_outbox_draft_action source/tests/test_patch_mission.py::test_patch_mission_apply_patch_waits_for_owner_then_reruns_green_tests source/tests/test_patch_mission.py::test_patch_mission_pr_summary_uses_diff_and_test_evidence source/tests/test_patch_mission.py::test_patch_mission_pr_update_push_draft_updates_existing_pr_branch source/tests/test_patch_mission.py::test_patch_mission_resume_next_captures_ci_logs_before_fix_plan -q` -> 5 passed after recovered confidence direct-path proof.
- `python -m py_compile source/src/wls/runtime.py source/tests/test_patch_mission.py` -> passed after recovered direct pytest execution/draft link.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_repeated_success_creates_repair_skill_candidate -q` -> passed, including recovered direct pytest remaining uncounted while pending, incrementing promoted skill use count only after owner approval and successful execution, and carrying `recovered_outcome_supported`, `outcome_supported_direct_test`, `recovery_successes` 2, and unrecovered failed/rejected 0 into `from-test-result` and failure-learning memory.
- `python -m pytest source/tests/test_server.py -q` -> 13 passed after recovered direct pytest execution/draft link.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_from_approved_test_result_creates_outbox_draft_action source/tests/test_patch_mission.py::test_patch_mission_apply_patch_waits_for_owner_then_reruns_green_tests source/tests/test_patch_mission.py::test_patch_mission_pr_summary_uses_diff_and_test_evidence source/tests/test_patch_mission.py::test_patch_mission_pr_update_push_draft_updates_existing_pr_branch source/tests/test_patch_mission.py::test_patch_mission_resume_next_captures_ci_logs_before_fix_plan -q` -> 5 passed after recovered direct pytest execution/draft link.
- `python -m py_compile source/src/wls/runtime.py source/tests/test_patch_mission.py` -> passed after recovered direct repair-loop completion proof.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_repeated_success_creates_repair_skill_candidate -q` -> passed, including recovered direct `from-test-result` draft -> owner-approved draft write -> owner-approved apply-patch -> passing verification -> PR summary, with the summary and successful repair learning preserving `recovered_outcome_supported`, `outcome_supported_direct_test`, `recovery_successes` 2, and unrecovered failed/rejected 0.
- `python -m pytest source/tests/test_server.py -q` -> 13 passed after recovered direct repair-loop completion proof.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_from_approved_test_result_creates_outbox_draft_action source/tests/test_patch_mission.py::test_patch_mission_apply_patch_waits_for_owner_then_reruns_green_tests source/tests/test_patch_mission.py::test_patch_mission_pr_summary_uses_diff_and_test_evidence source/tests/test_patch_mission.py::test_patch_mission_pr_update_push_draft_updates_existing_pr_branch source/tests/test_patch_mission.py::test_patch_mission_resume_next_captures_ci_logs_before_fix_plan -q` -> 5 passed after recovered direct repair-loop completion proof.
- `python -m py_compile source/src/wls/runtime.py source/tests/test_patch_mission.py` -> passed after recovered summary resume-next git-prep proof.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_repeated_success_creates_repair_skill_candidate -q` -> passed, including owner-approved recovered PR summary -> `resume-next` git metadata read -> `resume-next` git-prep outbox checklist, with git metadata reading the patched repo diff and git-prep remaining `WAITING_APPROVAL`.
- `python -m pytest source/tests/test_server.py -q` -> 13 passed after recovered summary resume-next git-prep proof.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_from_approved_test_result_creates_outbox_draft_action source/tests/test_patch_mission.py::test_patch_mission_apply_patch_waits_for_owner_then_reruns_green_tests source/tests/test_patch_mission.py::test_patch_mission_pr_summary_uses_diff_and_test_evidence source/tests/test_patch_mission.py::test_patch_mission_pr_update_push_draft_updates_existing_pr_branch source/tests/test_patch_mission.py::test_patch_mission_resume_next_captures_ci_logs_before_fix_plan source/tests/test_patch_mission.py::test_patch_mission_resume_next_continues_local_git_publish_prep -q` -> 6 passed after recovered summary resume-next git-prep proof.
- `python -m py_compile source/src/wls/runtime.py source/tests/test_patch_mission.py` -> passed after recovered commit-draft preparation proof.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_repeated_success_creates_repair_skill_candidate -q` -> passed, including owner-approved recovered git-prep checklist -> `resume-next` commit-draft action, with the commit action remaining `WAITING_APPROVAL`, using the recovered summary/checklist commit message evidence, and leaving the local commit count unchanged before approval.
- `python -m pytest source/tests/test_server.py -q` -> 13 passed after recovered commit-draft preparation proof.
- `python -m pytest source/tests/test_patch_mission.py::test_patch_mission_from_approved_test_result_creates_outbox_draft_action source/tests/test_patch_mission.py::test_patch_mission_apply_patch_waits_for_owner_then_reruns_green_tests source/tests/test_patch_mission.py::test_patch_mission_pr_summary_uses_diff_and_test_evidence source/tests/test_patch_mission.py::test_patch_mission_pr_update_push_draft_updates_existing_pr_branch source/tests/test_patch_mission.py::test_patch_mission_resume_next_captures_ci_logs_before_fix_plan source/tests/test_patch_mission.py::test_patch_mission_resume_next_continues_local_git_publish_prep -q` -> 6 passed after recovered commit-draft preparation proof.

### Limitations

- This is not yet a full GitHub Patch Mission; it performs the first read-only repo inspection link only.
- No autonomous write, delete, push, publish, PR creation, browser login, or external submission was added.
- Owner feedback changes future local candidate execution, but UI controls for that feedback are not yet wired into the visible frontend.
- Skill promotion remains gated; feedback only creates heuristic memory unless existing gated skill workflows are invoked.
- Patch Mission can now choose/read a target file, create a test probe action, and consume an approved test result to choose a likely failing file.
- The approved-test-result continuation can synthesize a minimal unified diff for a simple Python literal mismatch. More complex failures still degrade to a human-reviewable outbox draft.
- WLS can now generate an owner-gated canonical repo write from an approved outbox unified diff and rerun pytest against the changed repo state.
- The apply-patch link supports a bounded single-file unified diff; multi-file, binary, deletion, rename, and merge-conflict patches are not supported yet.
- WLS can now generate an outbox-only PR summary from verified diff and test evidence.
- WLS can now inspect local git branch/status/diff metadata through a fixed read-only tool and write an outbox commit checklist.
- WLS can now prepare and, after exact owner approval, execute a local tracked-file git commit from verified Patch Mission evidence.
- Patch draft mode still requires caller-supplied diff text when the owner wants to provide the diff directly.
- Commit draft uses `git commit -am`, so it only commits tracked file modifications; untracked files, new files, deletes, renames, and multi-file staging policy still need explicit support.
- Remote readiness is local-only git metadata; it does not fetch, call GitHub APIs, inspect live CI, or read issues/PRs yet.
- WLS can now prepare and, after exact owner approval, create/switch to a local Patch Mission branch.
- WLS can now perform owner-approved read-only live remote inspection through fixed `git ls-remote` commands and can attempt public GitHub metadata reads for GitHub remotes.
- WLS can now prepare and, after exact owner approval, push a prepared local `wls/...` branch to `origin`.
- WLS can now prepare a draft GitHub PR creation action from verified summary and pushed branch evidence.
- Actual PR creation still requires exact owner approval and `GITHUB_TOKEN`; without both, it remains blocked at the immune boundary.
- WLS can now prepare owner-gated read-only PR/CI status inspection from an explicit PR URL or succeeded PR creation action.
- PR status inspection captures API summaries and bounded failure summaries; it does not download full raw CI logs yet.
- WLS can now turn failing PR/CI evidence into an outbox-only CI fix plan with a selected next local Patch Mission step.
- WLS can now turn an approved CI fix plan into the selected local Patch Mission action.
- WLS can now consume CI-triggered local pytest results through the existing patch synthesis/apply/verify/summary flow while preserving PR/CI provenance.
- WLS can now prepare and, after exact owner approval, push a CI-sourced repair commit to the existing `wls/...` PR branch without force pushing.
- WLS can now re-inspect PR/CI status after the update push and write an outbox-only pre/post comparison.
- WLS can now branch from a post-update verification result: remaining failures reopen `ci-fix-plan`; no immediate failures produces an outbox wait/owner-review note.
- WLS can now derive a compact Patch Mission continuity snapshot from real actions and expose it through existing mission state.
- WLS can now run `resume-next` for the PR/CI repair loop, including `pr-update-verify -> pr-update-next` and approved post-update CI fix plan -> `ci-next-action`.
- `resume-next` now covers the local git publishing chain after `pr-summary`, including post-push GitHub remote evidence refresh before draft PR creation.
- Patch Mission can now fetch bounded failed GitHub Actions job log excerpts after explicit owner approval; it still does not fetch unlimited logs or arbitrary external artifacts.
- CI-triggered local pytest reproduction can now target a specific safe pytest file/nodeid from CI evidence; non-pytest or ambiguous failures still fall back to inspect-file, full test, or git metadata.
- Patch Mission now records cause/fix/regression memory from approved test evidence.
- Patch Mission now cites relevant prior failure-learning memories in later CI plans and drafts; the influence remains advisory and evidence-bound.
- Patch Mission can now create a bounded `patch_mission_repair_skill` evolution candidate after repeated verified literal-mismatch repairs. This is still only a candidate: no sandbox validation, owner approval transition, promotion, active skill use, commit, push, PR, or external action is implied.
- Patch Mission can now replay/review a `patch_mission_repair_skill` candidate against retained evidence and cite a replay-passed candidate in later plans/drafts. Replay is evidence-only: it does not transition candidate status, run commands, create skills, or grant execution authority.
- Patch Mission can now run an owner-approved disposable sandbox validation for a replay-passed repair candidate and persist the candidate as `SANDBOXED`. This still does not validate for arbitrary repos, create a declarative `skills` row, promote a skill, or authorize live repo/GitHub actions.
- Patch Mission can now human-validate a sandboxed repair candidate into `VALIDATED` by checking persisted manifest/result digests. This still does not create a skill row, approve, promote, execute commands, or grant live authority.
- Patch Mission can now create a non-active declarative skill proposal from a `VALIDATED` repair candidate. The skill remains `PROPOSED`, is not active, is deduplicated by name, and still requires the existing skill sandbox/validation/approval/promotion lifecycle before any use.
- Patch Mission can now start the proposed repair skill's own sandbox lifecycle and persist a `skill_experiments` RUNNING manifest, moving the skill to `SANDBOXED`. This still does not validate, approve, promote, activate, or execute the skill.
- Patch Mission can now complete the repair skill sandbox result and move the skill to `VALIDATED` through existing skill lifecycle checks. This still does not approve, promote, activate, execute commands, or grant live repo/GitHub authority.
- Patch Mission can now approve a `VALIDATED` repair skill for advisory matching after explicit owner approval. This still does not promote the skill, autonomously execute it, run commands, mutate live repos, push, open PRs, or grant GitHub authority.
- Patch Mission can now cite an `APPROVED` repair skill during later `ci-fix-plan` and `from-test-result` planning. This remains advisory-only: no skill execution is recorded, `use_count` is unchanged, and all command/write/GitHub steps still require separate existing approvals.
- Patch Mission can now let a matched `APPROVED` repair skill influence the `ci-fix-plan` next-step reason and `from-test-result` patch-draft rationale. This still does not turn the skill into an executing actor, increment `use_count`, promote, mutate repos, push, open PRs, or grant GitHub authority.
- Patch Mission can now turn a matched `APPROVED` repair skill into metadata on the existing owner-gated `ci-next-action` candidate. This still does not count as skill execution, does not increment `use_count`, and does not execute until the normal owner approval path approves the generated action.
- Patch Mission can now record a bounded outcome when an owner-approved skill-derived action succeeds, incrementing the approved skill use count only after real approved execution. This still does not promote the skill, auto-approve future actions, mutate repos outside the approved action, push, open PRs, or grant GitHub authority.
- Patch Mission can now aggregate repeated approved skill-derived action outcomes into a `PROPOSED` promotion-review candidate. This still does not promote the skill, auto-approve future actions, mutate repos, push, open PRs, or grant GitHub authority.
- Patch Mission can now record an explicit owner-approved promotion-review decision for a promotion-review candidate. This still does not promote the skill, auto-approve future actions, mutate repos, push, open PRs, or grant GitHub authority.
- Patch Mission can now promote an approved repair skill after explicit owner approval and an approved promotion-review candidate. This still does not auto-approve future actions, mutate repos, push, open PRs, or grant GitHub authority.
- Patch Mission can now use a `PROMOTED` repair skill as higher-confidence planning context. This still does not execute the skill, increment use count, auto-approve future actions, mutate repos, push, open PRs, or grant GitHub authority.
- Patch Mission can now record a bounded outcome for a `PROMOTED` repair skill after a separately owner-approved action succeeds. This still does not auto-approve future actions, mutate repos outside the approved action, push, open PRs, grant GitHub authority, or count pending/rejected/failed candidates as skill execution.
- Patch Mission can now use promoted outcome history to calibrate later planning confidence. This still only changes rationale and candidate metadata; no execution, approval bypass, repo mutation, push, PR, or GitHub authority is granted.
- Patch Mission can now use mixed/withheld promoted confidence to choose a safer read-only local verification action before attempting another command-driven repair step. This still does not write repos, push, open PRs, grant GitHub authority, or count read-only inspection as skill execution.
- Patch Mission can now preserve the direct narrow pytest path when promoted confidence is outcome-supported. This still does not execute the command without owner approval and does not count as skill execution until the approved command succeeds.
- Patch Mission continuity now preserves and exposes promoted confidence action decisions, and `resume-next` can continue from a safety fallback inspection to the next owner-gated local test. This still does not bypass approval or treat read-only inspection as a test result.
- Patch Mission can now use fallback inspection content to restore a narrow pytest nodeid for the next verification action when the inspected file proves that nodeid exists. This still does not execute the command without owner approval and falls back to file-level verification when the nodeid cannot be proven.
- Patch Mission can now carry fallback inspection and restored-nodeid context into the patch draft and failure-learning memory after the owner-approved test result. This still does not write repos, push, open PRs, or bypass approval.
- Patch Mission can now record a promoted-skill recovery outcome after a fallback-restored repair completes through owner-approved draft, apply, and passing verification, and can shift later planning from `mixed_outcome` to `recovering_mixed_outcome`. This still does not delete failed/rejected evidence, bypass approval, count read-only inspection as skill execution, write repos outside approved actions, push, open PRs, or grant GitHub authority.
- Patch Mission can now shift fully offset promoted confidence from `recovering_mixed_outcome` to `recovered_outcome_supported`, restore the direct narrow pytest path, and expose that recovery reason in mission continuity. This still preserves failed/rejected evidence, leaves the action `WAITING_APPROVAL`, and does not execute, increment skill use count, push, open a PR, or grant GitHub authority before approval.
- Patch Mission can now execute a recovered direct pytest action only after owner approval and carry the recovered-confidence source context into the next patch draft and failure-learning memory. Pending recovered direct actions still do not count as skill execution, and the approved execution still does not write repos, push, open a PR, or grant GitHub authority.
- Patch Mission can now complete the recovered direct repair loop through owner-approved draft write, apply-patch, passing verification, and PR summary while preserving recovered-confidence source context in both the summary and successful repair learning. This still does not push, open a PR, or grant GitHub authority.
- Patch Mission can now continue from an owner-approved recovered direct PR summary through `resume-next` into real local git metadata inspection and an owner-gated git-prep checklist. This still does not commit, push, open a PR, or grant GitHub authority.
- Patch Mission can now continue from an owner-approved recovered git-prep checklist to a local commit-draft action that remains `WAITING_APPROVAL` and does not create a commit before explicit owner approval. This still does not push, open a PR, or grant GitHub authority.

### Next Single Action Gap

Continue Task A: GitHub Patch Mission, with the next real action chain:

1. Approve the recovered `commit-draft`, create the local commit, then use `resume-next` to refresh post-commit git metadata and prepare the remote-readiness summary.
2. Prove the commit uses recovered summary/checklist evidence and that push/PR creation still remain separate owner-approved actions.
3. Keep GitHub push and PR creation outside this chain unless separately approved by existing boundaries.

Do not add more dashboards, labels, readiness gates, or pure reports before this action chain exists.
