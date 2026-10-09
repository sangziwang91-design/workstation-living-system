# GPT / Nuomi WLS execution protocol v1

This is a read/execute/verify convention for the existing **one** WLS
`LivingSystem` and repository. It does not create a second controller,
database, scheduler or source of truth.

## At the start of a session

1. Fetch exact GitHub `main` commit and the latest successful **WLS CI**
   run at that SHA. Read its `wls-feedback-<run_id>` artifact:
   `FEEDBACK.json` and the generated `CURRENT_STATE.yaml`.
2. Treat `CI_OBSERVATION_NON_PROMOTABLE` as an *observation*, not authority.
   Prefer observed failing test cases, and then genuine owner-task gaps.
   `UNMEASURED` / `INVALID_STANDARDS_CHANGED` is NOT a gain.
3. Verify target branch, PR state and CI of the exact HEAD. Reuse/close old
   branches rather than creating a second architectural mainline.
4. Change one measurable cause per PR. An evaluator/scoring change and a
   worker/implementation change must go through **separate PRs**. Run the
   trusted scoring/code isolation check; a green check isn't a holdout score.
5. Write code and failure/positive controls, create PR, run GitHub-hosted
   Linux and Windows CI. Fix errors using the hosted run logs. Record exact
   SHA, test totals (if observed) and artifact links. Never infer completion
   from a source commit or earlier SHA.
6. If the same hypothesis produces three candidate revisions without
   measured gain, close it as `NO_GAIN` and select a different cause rather
   than claiming more "generations". Stop if scoring standards move mid-test.

## Hand-offs and independence

**GPT / Codex:** implement and test an authorized GitHub PR, do not directly
edit a scored candidate's evaluator, external holdout set or Nuomi materials.

**Nuomi:** owns *all* holdout contents, case labels and evaluator manifest.
Execute the G4 external-holdout scorer from an exact trusted checkout with
independently frozen SHA-256 digests and private case file outside the repo.
Report only aggregate scores (n, Wilson bounds, paired gains/regressions,
scorer version), verdict and SHA; no raw case identifiers, prompts or labels.
A qualified score is still `candidate_only`, never automatic deployment.
More than five peeks at the same sealed set require a fresh holdout version.

**WLS:** canonical runtime for goals/actions/learning. Candidate versions and
skills are non-authoritative until a separate owner-approved promotion.
Signed same-process records prove integrity but not independent truth.

**Owner:** approves live deployment, irreversible actions and promotion.

## Per-round evidence fields (journal contract)

Store in an issue/PR comment or a human-approved journal append; do not make
a CI actor silently push provenance documents into `main`.

```json
{
  "source_type": "github_ci_observed",
  "base_sha": "<SHA>",
  "candidate_sha": "<SHA>",
  "hypothesis": "<single testable change>",
  "pr": 0,
  "feedback_run_id": "<GitHub Actions run>",
  "evaluator_sha256": "<frozen or UNMEASURED>",
  "case_sha256": "<Nuomi-held or UNMEASURED>",
  "result": "PASS|NO_GAIN|UNMEASURED|STANDARD_MOVED",
  "claim_ceiling": "candidate_only",
  "next_action": "<evidence-linked task>"
}
```

Only checked, independently evaluated outcomes may inform future scorer or
improver selection. Current engineering tasks do not establish RSI-C/D.

## Exit boundary

The user-specified B/C/D progression remains under
`docs/RSI_PRODUCTION_PATH_V1.md`. For an explicit *self-improvement* claim,
require identical model/task/time/call budgets, preserved control (fixed
improver), candidate evaluator isolation, Nuomi-held unrevealed holdout,
repeated seeds, no safety regression and measurable positive paired advantage.
Without these, report `ITERATIVE_ENGINEERING_RSI_UNPROVEN`.
