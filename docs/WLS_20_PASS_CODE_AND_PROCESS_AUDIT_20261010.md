# WLS twenty-pass workflow and repository execution audit · 2026-10-10

Baseline: `main@dac79f373b29464fd119a512e23909f3abc8329a`.
Scope inventoried via full Git tree: 348 tracked entries, 108 `source/src/wls/*.py` modules, 30 `source/scripts/*.py` scripts, 83 `source/tests/*.py` suites, and all 20 GitHub Actions workflows.

**What this run demonstrates.** Twenty *distinct workflow contract checks* are executed as separate pytest parameter cases (`test_twenty_workflow_trust_checks`); four further negative controls check promotion/job separation, secure scorer events, A5 issue-write segregation, and scheduling concurrency. The existing CI independently runs the complete Python test suite on hosted Linux and Windows 3.11/3.13, plus type/security checks. This is not twenty full semantic audits of every function, twenty autonomous RSI generations, or a proof that WLS has no further bugs.

## Audit checkpoints

| Pass | Reviewed workflow | Trust requirement |
| --- | --- | --- |
| 01 | apply-evolution-target-001 | upload receipt cannot fetch a mutable action version |
| 02 | audit-public-history | secret-history scan remains read-only and uses pinned dependencies |
| 03 | build-evolution-target-002 | staged build preserves read-only token |
| 04 | ci | Linux/Windows reports and feedback artifacts use immutable action commits |
| 05 | evolution-chain | validated evolution chain has explicit permissions and timeout |
| 06 | hosted-rsi-autorepair | grader candidate and write-token publisher remain separate |
| 07 | hosted-rsi-local-model | real model candidate cannot inherit publisher write scope |
| 08 | manual-ubuntu-cross-platform | manual tests keep read-only repository authority |
| 09 | mypy-baseline | type-contract workflow does not gain repository write authority |
| 10 | packaging-layout | packaging invariants preserve pinned actions |
| 11 | patch-mission-resume-regression | recovery regression remains bounded |
| 12 | patch-mission-status | status verification remains read-only |
| 13 | rsi-c1-isolation | sandbox evidence collection uses a pinned uploader |
| 14 | rsi-pilot | pilot artifact writes use pinned repository actions |
| 15 | rsi-scoring-change-boundary | scorer runs with unprivileged PR event, checks trusted main |
| 16 | verify-evolution-target-003 | causal-memory validation cannot inherit mutable artifact code |
| 17 | wls-a5-gap-scan | scheduled issue-write token segregated from candidate test execution |
| 18 | wls-c2-known-regression-replay | historical replay has no write-capable token |
| 19 | wls-c2-taskpack | practice-task data artifacts use immutable upstream commit |
| 20 | wls-hosted-life-loop | PR runs cannot displace scheduled canonical life probes |

## Confirmed corrections

1. Pin upstream `actions/upload-artifact` to `ea165f8d65b6e75b540449e92b4886f43607fa02` and `actions/download-artifact` to `d3f86a106a0bac45b974a628896c90dbdf5c8093`, after retrieving those exact SHA targets from the official upstream `v4` Git tag refs. No external marketplace action or input contract changes.
2. Replace `pull_request_target` for the trusted scorer gate with `pull_request`. The gate still checks out only the default branch and uses read-only `contents`/`pull-requests` metadata. GitHub's public-repository restrictions on `pull_request_target` make unnecessary use an avoidable reliability and elevated-event risk.
3. Namespace the WLS hosted life concurrency group by event and ref; otherwise a PR test can overwrite a pending scheduled run under the original repository-global static group. Keep `cancel-in-progress: false`.
4. Add executable regression gates for all twenty workflows and for the privileged-candidate split, scorer checkout scope, A5 privilege split, and life scheduler.

## Exclusions and remaining questions

- Static pattern hits were triaged rather than blindly called vulnerabilities. The constant `audit-public-history` group is intentional latest-history rescan, not the same scheduled continuity requirement. Same-repository candidate checkouts with write permission are constrained to isolated publisher jobs; they warrant future runtime adversarial tests, but are **not** declared exploited.
- Full source semantic completeness is **UNMEASURED**. The 693K-character canonical `runtime.py` is a maintenance and audit-coverage issue, not sufficient evidence of a runtime defect by size alone.
- Continuous personal-host runtime, cross-scheduled-run durable identity, live model-authored skill gains, and Nuomi's sealed benchmark remain **UNMEASURED**. This patch makes no RSI advancement claim.
- Accept only the exact PR HEAD with hosted tests and scorer policy checks; re-check latest `main` after merge. The twenty checks establish their listed invariants, not absence of all novel defects.

Sources: GitHub Docs, *Securely using pull_request_target*, *Secure use reference*, *Troubleshooting workflows*, *Events that trigger workflows* (GitHub Actions schedule and event-policy semantics).
