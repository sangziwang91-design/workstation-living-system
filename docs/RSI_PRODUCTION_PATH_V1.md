# WLS RSI Production Path v1 — owner-started, provider-portable, evidence-gated

Status: **engineering plan plus a partially implemented pilot** (2026-10).
Canonical owner: WLS `LivingSystem`. Branch: PR #36, candidate only.
The target is **recursive improvement of an agent and its improver**, not
modification of inaccessible vendor model weights.

## Mission and definition of "any model"

The legitimate final behavior is: the owner registers a model backend, a
bounded task objective and a frozen evaluator. WLS obtains model proposals,
writes immutable candidate artifacts, executes them only in an isolated
environment, evaluates with an independent scorer, retains improvements,
learns reusable improvement strategies from evidence, and repeats until
a budget/stop gate fires. A model can be replaced or upgraded without
rewriting the governor, task suite, evidence schema or lifecycle machinery.

**It is not possible to guarantee that every arbitrary model will improve
itself**. Models vary in text/code competence, context limits, inference
protocol, tool use and whether credentials/terms permit automation. The
portable promise is interface compatibility for backends that implement
`RsiModelPort` and meet admission tests, not universal model performance.

### Trust and execution boundaries

```
OWNER: objective, permission, budget, stop/approval policy
   |
   v
WLS LivingSystem (single runtime/identity)
   |
   +-- immutable mission, evaluator digest, holdout partition, usage ledger
   +-- provider-neutral RsiModelPort -> candidate proposal only
   +-- RsiArtifactGate -> immutable allowlisted candidate with HMAC receipt
   +-- isolated execution worker [MISSING; must be a real OS sandbox]
   +-- independent evaluator [MISSING for model-authored executable code]
   +-- RsiEvolutionPilot -> champion / revert / blocked, SQLite + HMAC evidence
   +-- higher-level improver adaptation [MISSING; test against frozen control]
   |
   v
OWNER: signed report, optional live release promotion
```

No model owns credentials, owner approval, holdout cases, evaluator source,
WLS policy, promotion decision, HMAC key or a GitHub write token. Proposal
generation and scoring have separate trust domains.

## Critical-path engineering gates

Each gate requires committed source, regression/failure tests, exact-commit
CI on Linux and hosted Windows, and a verifiable evidence artifact.
Never mark a gate PASS merely because a file was added.

| Gate | Increment | Hard acceptance | State at plan creation |
|---|---|---|---|
| G0 | Repository baseline and governance | Stable WLS mainline, PR head, stop/rollback permissions, CI authority distinguished; no private→public action | Partial; self-hosted full CI queued |
| G1 | Model-independent proposal boundary | Uniform request/response, 3 distinct stub providers, strict file/identity/size scope, no secret logging, end-to-end 3-generation scripted integration | Implemented in PR #36; CI in progress |
| G2 | Live model admission | One local OpenAI-compatible endpoint (Ollama/vLLM) and one permitted remote model actually return **model-authored** candidates; record version, token/latency/cost and auth errors | Not run |
| G3 | Executable candidate containment | Disposable VM/container with read-only evaluator, no keys/network, process/memory/time/IO limits, hostile-code escape tests; run twice, record environment hashes | Not built |
| G4 | Independent coding benchmark | 12 development + 12 validation + 12 held-out tasks; fixed grader images and exact task hash; no selection on final held-out | Not built |
| G5 | Durable unattended controller | Lease/heartbeat, capped backoff, idempotent crash recovery, unknown-side-effect state, owner stop, spending gates, candidate cleanup and signed report | Partial protocol only |
| G6 | Real agent optimization | ≥3 actual model-authored generations ×2 candidates, held-out before/after with no hard regression; failure logs preserved | Not run |
| G7 | Recursive *improver* study | Two matched arms: fixed improver vs evolvable improver, same model/task/call/cost budgets, multiple seeds, independent holdout; report effect and confidence | Not run |
| G8 | Multi-provider interoperability | GPT/Codex, Claude, Qwen/GLM/Gemini/Kimi or supported local models through their own adapters; one shared evaluator and schema, feature-negotiation/fallback | Not run |
| G9 | Product qualification | Owner-machine Windows installer, upgrade rollback, 24–72h soak, power loss/network failure, signed trial ledger, safety & quota stop; owner acceptance | Not run |

**Current production blocker:** G3 and G4, not the number of generations.
G1 alone cannot safely execute model-produced code. Work must never skip
to G6/G7 by running unknown model code directly on a trusted Windows runner.

## Concrete next increments (ordered, no duplication of canonical WLS)

1. Connect `RsiModelExperiment` to WLS runtime as an opt-in API. Existing
   `RsiEvolutionPilot` remains the *only* generation state authority.
   Explicit `run_id`, `policy_digest`, `model_id`, `parent_artifact_id`.
2. Add a bounded invocation ledger: model/provider version and request digest,
   outcome, prompt/response byte caps, token counts, latency, estimated vs
   measured cost, cap-before-next-call; fail closed when usage is unknowable.
3. Implement an `IsolationRunner` protocol and a pinned container/VM backend.
   The source is mounted read-only; execution has no owner tokens, no host
   home directories, and no network by default. Pure mock runs do not qualify.
4. Add an immutable `TaskPack` split into development, selection and sealed
   final tasks. Evaluator provenance must be independent of candidates.
5. Add experiment reconciliation: on abrupt death during a model call, keep
   `IN_FLIGHT`/unknown effects, discover receipts by idempotency ID, never
   silently replay paid or side-effectful calls.
6. Add real provider adapters without provider-specific assumptions in core:
   OpenAI-compatible HTTP, Codex CLI (authenticated on owner host), Anthropic,
   Gemini and local Ollama via tested adapters. Missing features trigger an
   explicit capability error, not invented tool behavior.
7. Add evolutionary memory: store *hypothesis*, candidate change, measured
   result, failure reason, ancestry, model version, task mix, usage. Only
   measured findings affect next-generation selection. Do not treat chain-of-
   thought or model self-assessment as evidence.
8. Add fixed-versus-evolving improver controls, stopping rules and final
   statistical report. Freeze test sets and model versions within each study.
9. Add owner UI/CLI: start, status, pause, resume, reconcile, view score and
   cost curves, inspect/reject candidate, approve deployment.
10. Run Windows installation, hostile-candidate fault injection and soak
    against an exact release commit. Nothing deploys without owner sign-off.

## Minimal contract for a model plug-in

`RsiModelPort` specifies `model_id`, `capabilities` and
`propose(instruction, max_output_bytes) -> RsiProposalResponse`.
The pilot requires at least bounded **text generation**. Tool calling and
structured-output modes are optional optimizations, not universal
requirements. Backend-specific dialect and login stay behind adapters.
The first concrete adapter uses optional HTTPS/loopback OpenAI-compatible
`/v1/chat/completions`; other protocols need their own adapters.

`RsiModelCandidateBuilder` enforces a strict `{"files": {"relative": "source"}}`
response, converts only to immutable WLS candidate artifacts and binds
the artifact to frozen policy and evaluator digests. A model's "PASS"/score/
promotion claim is ignored. The actual `RsiModelExperiment` connects
this proposal path to `RsiEvolutionPilot`; its evaluator is an independent,
trusted injected dependency, not a method on the proposer.

Production admission must additionally cap **real** calls, wall-time, token
usage and cumulative dollars. Current G1 alone does not enforce a monetary
budget. An unsigned model or unknown inference route must not be treated
as free merely because a chat subscription exists.

## Longitudinal measurements

Record three separate series by generation (and by model):

- **Agent capability:** independent unseen-task pass rate; regressions.
- **Improver capability:** expected *new* agent improvement achieved per equal
  inference budget, compared with an immutable baseline improver.
- **Reliability/expense:** API success rate, crash/replay count, token spend,
  time-to-valid-candidate, evaluator failure/unknown result rate.

Do not compare raw benchmark totals when model versions, task difficulty
or inference budgets differ. Do not call scripted 50-fault/100-generation
harness tests recursive self-improvement. If G7 shows no controlled advantage,
the correct result is "iterative optimization, RSI unproven".

## Engineering reference decisions

- [Darwin Gödel Machine](https://arxiv.org/abs/2505.22954):
  archive and verify actual self-modifications and their fitness.
- [OpenHands SDK](https://github.com/OpenHands/software-agent-sdk):
  separate the agent from a disposable execution workspace.
- [SWE-bench harness](https://www.swebench.com/SWE-bench/reference/harness/):
  use frozen, containerized independent task evaluation.
- [LangGraph durable interrupts](https://github.com/langchain-ai/langgraph):
  persist pre-side-effect checkpoints and reconcile replay semantics.
- [LiteLLM provider matrix](https://docs.litellm.ai/docs/providers):
  a unified API is a useful adapter behind WLS, not a replacement for WLS
  identity, owner approval, independent evaluation or runtime memory.

## Current evidence ceiling

Previous WLS candidate pilot: 100 scripted generations + 50 fault injection
cases, Windows/Linux specialized CI passed. This iteration adds only the
portable **model proposal side** and a three-generation scripted end-to-end
contract test. It does NOT show real remote/local model access, code execution
sandbox strength, hidden-set improvement, cross-provider real usage or
recursive improvement of the improver. Those claims require gates G2–G9.


## First usable GitHub-native path (candidate implementation, 2026-10-08)

The first working application of the existing WLS RSI code is **task risk
admission improvement**, not arbitrary software execution. It is intentionally
data-only: a provider proposes `agent/strategy.json` containing literal
`risk_terms` for `REVERSIBLE_WRITE`, `HIGH`, and `IRREVERSIBLE`. An
independent WLS function scores 17 frozen Chinese/English task scenarios.
The proposal cannot lower deterministic risk, change the evaluator, modify
approval logic, execute Python, push code, or deploy a candidate. Winning
candidate source is archived; no live admission rule changes automatically.

After installing the candidate branch with `python -m pip install -e .`,
you can inspect an untouched run without credentials:

```sh
wls --config /path/to/wls/config.json rsi-risk --run-id trial-001 --mode status
```

For an **owner-consented** model experiment (the model provider may charge
for two calls), configure `WLS_RSI_API_KEY` in the current environment
(or run a loopback server that needs no API key), and invoke:

```sh
wls --config /path/to/wls/config.json rsi-risk \
  --run-id trial-001 --mode run \
  --model-id YOUR_COMPATIBLE_MODEL --base-url https://YOUR_TRUSTED_ENDPOINT \
  --generations 2 --branches 1 --confirm-model-usage
```

The command emits measured champion score, artifact ID, generation and
candidate count. Re-run `--mode status` from another process to inspect
the persisted WLS SQLite result. To apply the *measured winning strategy*
to a real request as a **read-only preview**, without changing live WLS
policy or making another model call:

```sh
wls --config /path/to/wls/config.json rsi-risk \
  --run-id trial-001 --mode classify --request "发布到公开网页"
```

The output compares canonical baseline risk with the candidate's risk,
and reports whether an owner gate would be required. The candidate is
never automatically promoted into global admission policy. Candidate files
are stored under WLS home `sandbox/rsi_candidate_artifacts`.
The word "sandbox" here is a **folder name**, not an OS isolation claim.

The same private GitHub repository now contains an optional
`workflow_dispatch` job in `.github/workflows/rsi-pilot.yml`. This job
requires **manual `approve_model_usage`**, and it must run against trusted
**main**, never a PR branch or unreviewed fork. It requires:

- Repository **secret** `WLS_RSI_API_KEY` (provider credential).
- Repository **variable** `WLS_RSI_BASE_URL` (provider HTTPS base URL).
- Repository **variable** `WLS_RSI_MODEL_ID` (selected compatible model).

It makes at most two model proposal calls, records the measured candidate
score, and uploads the candidate data files plus a result JSON artifact to
the private GitHub Actions run. The workflow only becomes clickable from
GitHub UI when the file exists on the default branch after a separately
reviewed/approved merge. **Do not merge solely to launch this trial**.

The test suite also exercises the command and WLS state/ledger using an
offline fake port to avoid billing or credential usage in pull-request CI.
This confirms the routing loop, not actual remote inference.

**Evidence ceiling:** The first usable path improves a concrete WLS task
risk-classifier strategy under frozen checks. It does not prove general
coding improvement, autonomous code execution, multi-model interoperability,
long-term cost-safe operation, or recursion in the improver's capability.
Those remain explicit separately measurable milestones. GitHub Models
retired in July 2026; do not design new clients against the retired API.
GitHub Copilot CLI is a possible later adapter with its own billing policy.
