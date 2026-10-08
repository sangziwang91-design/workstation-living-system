# WLS RSI | Comparative architecture and 50-round adversarial proof

Status: **research-backed engineering review, bounded implementation**.
Owner-started local RSI is the design objective, not a verified capability.

## 1. External architecture comparisons

| Reference | Verified useful design | Absorbed into WLS | Evidence/limitations |
|---|---|---|---|
| [Darwin Gödel Machine](https://github.com/jennyzzt/dgm) | Parent and mutation archive, model-authored agent revisions, scoring on coding tasks; separate self-improve baseline | Artifact lineage and a mandatory future fixed-improver control | Its reported benchmark improvements are on its evaluation protocol, not transferable performance figures for WLS |
| [OpenHands software-agent-sdk](https://github.com/OpenHands/software-agent-sdk) | Separation of agent, execution backend, ephemeral workspace and lifecycle | Candidate artifacts cannot be considered trusted executor environments; require an actual sandbox before running generated source | A GitHub-hosted job or an allowed path is not itself an OS-grade isolation boundary |
| [SWE-agent](https://github.com/SWE-agent/SWE-agent) | Explicit agent-computer interface, observable code/test feedback | Keep feedback structured and test output external to the model | Model-reported "PASS" is a hint, not a score |
| [SWE-bench](https://github.com/SWE-bench/SWE-bench) | Repository-level regression tasks, setup, patches and test outcomes | Future transfer test set must use multiple unrelated repair tasks, not scripted answers | Benchmarks can be contaminated; frozen task splits and locked prompts required |
| [AIDE / open-ended search](https://github.com/WecoAI/aideml) | Candidate search with repeated externally measured performance | Separate candidate search budget from final sealed-task adjudication | Searching the same benchmark does not establish transferable recursive capability |

No second WLS runtime, database, living subject or version authority is allowed.

## 2. Canonical authority mapping

| Responsibility | Canonical WLS organ | RSI-specific addition |
|---|---|---|
| Identity, policy, owner approval | `runtime.LivingSystem`, `PolicyEngine`, `ApprovalManager` | RSI remains candidate-only; no automatic promotion |
| Candidate generation | `coding_workers.CodexWorker` and configured provider adapters | Requires actual authenticated model on an approved machine |
| Mutable source/parent selection | `offspring`, `coding_adapter`, WLS evolution schema | `RsiArtifactGate` adds immutable source receipts and allowlist |
| Bounded generation loop | `RsiEvolutionPilot`, `loop_control` | Policy SHA pin, distinct candidate IDs, generation checkpoints |
| Independent scoring | `experiment_decision` + external trusted evaluator | A future isolated test runner must supply real `MetricResult`; model text is never enough |
| Evidence | `Database`, `EvidenceLedger` | HMAC receipts for each measurement/artifact with strict JSON |
| Owner result communication | Existing WLS CLI/UI and evidence reporting | Chat interface may review reports; it is not an always-on model caller |

## 3. Real defects addressed in this review

1. Model-supplied numerical metrics including NaN and Infinity were sanitized in
   state but not in the HMAC receipt; fixed both.
2. Candidate source bytes had no sealed archive identity and the policy/evaluator
   digest alone was insufficient to establish which code was scored. Added an
   allowlisted, content-addressed artifact gate bound to the canonical HMAC ledger.
3. An attacker able to edit the archive can recompute SHA-256 manifest checksums;
   simply checking file hashes is insufficient. A valid candidate now requires
   a matching unaltered signed WLS receipt, and re-sealed fake manifests fail.
4. `CodingWorker.execute` previously lacked `CodingTaskContract.validate()`
   preflight. Path traversal/symlink escape could reach a CLI or file receipt.
   Preflight and post-execution containment checks now use the same path validator.
5. RSI protocol faults had no systematic cross-platform trace: added a 50-case
   matrix (10 fault families × 5 distinct baselines).

## 4. Exactly what the 50-round suite proves

Command:
`python source/scripts/verify_rsi_50_faults.py --report rsi-50-proof.json`

| Family | 5 distinct runs each | Expected fail-closed property |
|---|---|---|
| GAIN | Numeric gain with immutable policy | Better candidate promoted *within candidate archive only* |
| NO_GAIN | Insufficient score | Parent retained |
| HARD_GATE | Scored better but regression exceeds fixed ceiling | Rejected |
| NAN_PRIMARY | Invalid floating score | Rejected; no non-standard JSON receipt |
| INF_GATE | Invalid non-finite risk gate | Rejected; no non-standard JSON receipt |
| DIGEST_MISMATCH | Candidate claims wrong evaluator | Rejected |
| DUPLICATE_ID | Proposer reuses candidate identity | Generation blocked |
| CHANGED_POLICY | Different policy during run | No external callbacks invoked |
| PROPOSER_EXCEPTION | Proposer fails partway through generation | Blocked with already gathered evidence |
| EVALUATOR_EXCEPTION | Independent scorer fails | Blocked, not replayed |

Each run goes through the actual `RsiEvolutionPilot`, SQLite transactions and
WLS HMAC ledger. After all 50, the suite reads every raw ledger JSON object,
verifies its signature/hash chain, reopens the database and verifies persisted
candidate states. The CI exports each distinct case ID, status and SHA-256 of
the result state. The artifact guard has separate adversarial regression tests
for file tampering, manifest tampering, re-sealing, parent tampering and
non-allowlisted evaluator modifications.

These 50 cases are **fault-injection experiments**, NOT 50 attempts by a
large language model to improve its own coding ability. Passing them is
necessary infrastructure evidence, not sufficient evidence of RSI.

## 5. The additional conditions for real recursive improvement

These must all hold; do not award an RSI claim from generic test counts:

1. **Model-authored changes**. The actual local Codex/OpenCode/Claude worker
   should propose edits to allowed coding-agent source, with a recorded model
   ID, version, exact prompt, CLI exit status and resource consumption.
2. **Sandbox isolation**. Execute generated code only in an ephemeral
   disposable VM/container, with denied network/credentials, explicit process,
   file, CPU, memory and wall-time limits. GitHub-hosted Windows alone is a
   disposable runner, not automatically an isolation boundary for malicious
   code. The current WLS sandbox adapter is explicitly a local fixture.
3. **Independent frozen tests**. Use one development set for searching, one
   frozen selection set and one sealed post-selection set. Candidate must not
   be able to read or modify the evaluator, holdout cases or grading process.
4. **Fixed-improver control**. Match a stationary initial improver and the
   self-modifying improver by model version, candidate attempts, token/time
   budget, task order and independent trials. Improvement in solving tasks
   is not the same as improvement in the ability to generate improvements.
5. **Transfer and reliability**. The selected version must improve on unseen
   tasks without violating hard gates and pass a replay after restart. Keep
   the unsuccessful branches and their reasons.
6. **Owner sign-off for deployment**. Candidate selection, provenance and
   test passing may be automatic. Changes to canonical WLS runtime, credentials,
   repo visibility and deployment are separate owner-approved actions.

## 6. Operational boundaries

* Do not enable a model on GitHub-hosted CI with an unrestricted repository
  token or owner secrets as an untrusted candidate subprocess.
* Never count fixture improvements in RSI statistics.
* No automatic release, merge, branch visibility change, mainline overwrite or
  uncontrolled provider spending.
* CI on public standard runners has different billing from private runners;
  changing visibility is an explicit owner decision, not a tool workaround.
* Stop on unknown side effects rather than blindly resuming candidates.
* Treat self-hosted Windows workflows that remain queued as **not run**,
  even when hosted Linux and Windows candidate checks pass.

## 7. Next legitimate experiment

Using a disposable local/remote sandbox and owner-approved model access,
run a modest paired trial (e.g. three generations × two candidates) on
real coding-agent modifications against 12 development tasks, 12 selection
tasks and 12 held-out tasks. Gate on task success, regression count, cost,
provenance and independent test replay. Compare against the frozen improver
under an equal budget. If this fails, keep the negative evidence and do not
expand to 50 model-driven generations just to reach a round count.
