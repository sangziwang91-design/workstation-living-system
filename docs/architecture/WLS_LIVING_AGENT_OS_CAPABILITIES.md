# WLS Living Agent OS Capability Foundation

This branch adds repository-level capability organs for
`WLS-LIVING-AGENT-OS-CAPABILITIES-001`.

## Claim Ceiling

The implementation proves contracts and local tests only. It does not prove live
browser, desktop, WeChat, paid model, production deployment, Skill promotion, or
24-hour minimum-life completion.

## Authority Rule

The canonical WLS owners remain unchanged:

- subject: `LivingSystem`
- events: `EventStore`
- goals: `GoalStore`
- memory: `MemoryStore`
- planning: `Planner`
- policy: `PolicyEngine`
- approval: `ApprovalManager`
- tools: `ToolRegistry`
- evidence: `EvidenceLedger`
- skills: `SkillLibrary`
- evolution: `GrowthCycleManager`

New capability modules are adapters, projections, event sources, workbenches, or
candidate-artifact contracts. They do not create a second runtime, planner,
memory, goal store, policy engine, evidence ledger, or skill system.

## Product Shape

WLS is being fitted as a private, local-first Living Agent Operating System:

- private owner-operated software, not a public SaaS requirement;
- commercial-quality reliability, evidence, rollback, and recovery expectations;
- replaceable model and worker organs under one WLS subject;
- a local runtime that can do real work, not only heartbeat and self-maintenance;
- long-lived state where every capability can enter the life history and
  evolution loop.

Mainstream Agent capabilities are required organs, not decorative add-ons:

- conversation and instruction ingress becomes canonical Events, not direct
  Actions;
- planning remains `Planner` owned and evidence-bound;
- tool use remains `Policy -> Approval -> Executor -> Receipt`;
- file, browser, desktop, code, scheduler, MCP, A2A, multimodal, UI, WeChat,
  voice, screen, and workbench paths are organs fitted to existing authorities;
- external models and workers may propose or execute bounded work, but they do
  not own WLS identity, truth, memory, goal completion, or Skill promotion.

The product rule is:

```text
many organs, one subject
many views, one canonical history
many workers, one integration authority
many candidates, one evidence-bound promotion path
```

The first usable product surface is an Owner Console, not a chat shell. It must
show life state, attention, goals, actions/approvals, memory/world projections,
and the evolution lab. WeChat or any similar daily-life channel is an ingress,
notification, query, and approval organ only. It never becomes the WLS brain.

## Three Growth Lines

The 30-round campaign should be read as three interlocked growth lines:

- survival: heartbeat, recovery, backup, resource limits, permissions, Owner
  control, and long-running continuity;
- agent capability: planning, browser, file, desktop, coding, multi-agent,
  MCP/A2A, scheduler, multimodal, UI, and WeChat organs;
- evolution: trajectory, failure candidates, Skill candidates, world-model
  projections, experiments, canary, rollback, lineage, and niche selection.

These lines should bite into each other. Agent capability is not postponed until
after the life loop; it is admitted as organs whose use is captured by the life
loop.

`LivingSystem` owns a rebuildable capability registry projection via
`self.capabilities`. The registry appears in `status()` as a summary only; it
does not create canonical tables or replace existing stores. Channel and
scheduler helpers submit only canonical Events through `EventStore`.

`LivingSystem.ingest_channel_message(...)` and
`LivingSystem.emit_scheduled_event(...)` are the current fitted runtime seams for
channel and scheduler organs. They return EventStore insertion results and do
not create Actions, Goals, Memories, Skills, or tool executions directly.

Provider routing is fitted inside `Planner`. `Planner.route_summary()` records
the selected provider, fallback chain, privacy/cost evidence, and rationale while
keeping `Planner` as the only planning authority. `LivingSystem.status()` exposes
that route summary for audit. Remote or paid provider routes are blocked unless
configuration explicitly satisfies the route policy.

Task route classification is fitted as a Planner-owned workbench organ.
`TaskClassifier` consumes explicit task facts and returns a route candidate only:
kind, orchestration mode, roles, worker ceiling, owner-gate requirement, risk,
and rationale. It is monotonic on risk and cannot lower a Planner or Policy risk
floor. It does not create Plans, Actions, leases, tool calls, Goals, Memories,
Skills, or evidence, and it cannot authorize execution.

Execution tracing is fitted as an EvidenceLedger-owned projection candidate.
`TraceEmitter` can produce stable payload and trace digests for tests, local
harness receipts, and future replay review, but emitted trace events are
explicitly `trace_candidate_only`. They are not canonical evidence until the
existing `EvidenceLedger` records them.
Agentic acceptance checks now include bounded metric ranges in addition to
status, evidence count, JSON key, regex, and artifact checks; failures remain
visible deterministic acceptance failures, never implicit passes.

Loop control is fitted as a Planner-owned decision candidate. `LoopController`
turns explicit iteration budgets, observed gain, failures, cost, elapsed time,
and Owner stop requests into a machine-checkable stop reason. It does not pause
the runtime, reserve budget, mutate leases, create checkpoints, or resume work.
Those effects remain owned by the existing runtime, agentic harness, offspring,
lease, and evidence gates.

Experiment decisions are fitted as an Evolution-owned candidate workbench.
`decide_experiment` compares baseline and candidate metric results against a
frozen policy digest, evaluator digest, hard gates, minimum gain, crash state,
and budget counters. A `KEEP` verdict is still only a candidate decision; it
does not promote a Skill, absorb a candidate, mutate holdouts, or deploy code.

Approval and tool receipts are fitted through the existing `ApprovalManager`,
`PolicyEngine`, `ToolRegistry`, and `EvidenceLedger`. Runtime validation prepares
a reversible sandbox write, issues a signed approval envelope with nonce and
expiry, consumes it once, rejects replay and expired approval, and verifies the
action receipt in evidence.

Browser capability is fitted as a loopback read-only fixture. Validation fetches
an allowlisted local page, records a text hash receipt, blocks redirects, and
rejects non-allowlisted external hosts. Computer-use remains blocked unless a
verified disposable sandbox, focus hash, and rollback contract are present.

Coding capability is fitted as a disposable worktree candidate path. Validation
creates a temporary changed file, records a SHA-256 candidate receipt, requires
focused tests and rollback instructions, and rejects changed paths that escape
the worktree. The output remains `CANDIDATE_ONLY` and cannot mark goals,
memories, skills, deployments, or campaign rounds complete.

MCP/A2A capability is fitted as a local candidate validation path. Validation
rejects unpinned MCP identities, admits only reviewed/pinned MCP candidates,
accepts A2A artifacts only with matching payload hashes, and rejects any
artifact that claims canonical truth.

Workbench capability is fitted as an authority-bound template path. Validation
admits templates only when they bind to existing planning/evolution/skills
owners, require evidence, and avoid direct database writes, Skill promotion,
deployment, merge, or goal-completion claims.

External memory capability is fitted as a shadow projection under the existing
`MemoryStore` authority. Validation requires a pinned source digest and evidence
hashes, records only a candidate receipt, and rejects canonical-memory claims or
fields such as `memory_id`/`active`.

## Current Admissions

- P01 is admitted as a registry and authority guard.
- P02/P09 are shadow-level runtime admissions for EventStore ingress.
- P03 is a shadow-level runtime admission for approval, replay/expiry rejection,
  and tool receipt evidence.
- P04 is a shadow-level fixture admission for read-only browser receipts, while
  computer-use remains sandbox-blocked.
- P05 is a shadow-level fixture admission for coding worktree candidate
  receipts.
- P06 is a shadow-level fixture admission for pinned, evidence-hashed external
  memory candidate projections under `MemoryStore`.
- P07 is a shadow-level fixture admission for pinned MCP candidates and hashed
  A2A candidate artifacts.
- P08 is a shadow-level runtime admission for Planner-owned provider routing.
- P10 is a shadow-level fixture admission for authority-bound workbench
  templates.
- Computer use remains blocked without a verified disposable sandbox.
- MCP discovery is rejected unless identity is pinned and reviewed.
- A2A, MCP, and coding outputs remain candidate-only.
- Owner Console and WeChat W0/W1 are read-only projection/Event paths.
- P11 is a shadow-level fixture admission for productized Owner Console panels
  and WeChat W0/W1 digest notifications. They remain read-only projections and
  cannot write canonical state or execute tools.
- P12 is a shadow-level runtime admission for read-only real-task organs. They
  submit canonical Events and planning templates only; they do not create
  Actions, Goals, Memories, Skills, deployments, or direct tool executions.
- P13 is a shadow-level fixture admission for typed read-only organ profiles
  across research, browser, file, coding, content, social-research, and
  multimodal work. Each profile produces a Planner-owned plan candidate with
  read-only tool hints and evidence requirements, while forbidden write,
  deployment, payment, and Skill-promotion paths remain rejected.
- P14 is a shadow-level runtime admission for Owner-visible read-only task
  previews. `LivingSystem.intake_read_only_task(...)` records the canonical
  Event and evidence-bound preview in runtime state, exposes it through the
  Owner Console `task_previews` panel, and deliberately creates no `plans` or
  `actions` rows until a later Planner admission gate exists.
- P15 is a shadow-level runtime admission for guarded Planner intake.
  `LivingSystem.admit_read_only_plan_preview(...)` can turn an Owner-visible
  preview into a formal `PLANNED` Plan with only registered READ/none Actions,
  records evidence, rejects unknown or non-read tool hints, and executes
  nothing during admission.
- P16 is a shadow-level runtime admission for execution preflight.
  `LivingSystem.preflight_read_only_plan(...)` reuses `PolicyEngine` against
  the admitted READ/none Actions, records a preflight evidence receipt, exposes
  the result through the Owner Console `execution_preflight` panel, and still
  performs no tool execution.
- P17 is a shadow-level runtime admission for preflighted read-only execution.
  `LivingSystem.execute_preflighted_read_only_plan(...)` runs only actions that
  have a recorded READY preflight, still through the existing executor and
  policy/evidence path, records action receipts plus a plan-level receipt, and
  exposes them in the Owner Console `execution_receipts` panel.
- P18 is a shadow-level runtime admission for result projection. Successful
  read-only execution receipts can be projected into candidate MemoryStore and
  inferred WorldModel entries, with explicit candidate-only claim ceilings and
  no goal completion or Skill promotion.
- P19 is a shadow-level runtime admission for projection review and rollback.
  Candidate read-only projections can be reviewed or rolled back; rollback
  deactivates the candidate memory/world entries and records evidence without
  promoting Skills or completing Goals.
- P20 is a shadow-level runtime admission for browser read-only execution.
  Browser, research, and social-research organs now emit registered `http_get`
  read actions instead of adapter-only hints. Validation executes only an
  allowlisted loopback fixture through Planner admission, Policy preflight,
  ToolRegistry receipts, evidence, and Owner Console projection; it does not
  prove live web browsing or desktop control.
- P21 is a shadow-level runtime admission for composite research execution.
  A research organ can now combine local file, local directory, and allowlisted
  loopback HTTP sources in one Planner-owned Plan and read-only receipt, then
  project/review that result as candidate Memory/World state without completing
  a Goal or promoting a Skill.
- P22 is a shadow-level runtime admission for multimodal asset inspection.
  A multimodal organ can inspect a local media asset with a path-scoped
  `inspect_asset` read-only tool that records size, MIME guess, sha256, and a
  bounded header sample, then projects candidate evidence without parsing,
  generation, Skill promotion, or Goal completion.
- P23 is a shadow-level runtime admission for coding candidate inspection.
  A coding organ can inspect a disposable worktree candidate with
  `inspect_coding_candidate`, preserving changed-file hashes, base SHA, test
  commands, and rollback metadata as a read-only receipt and candidate
  Memory/World projection. It does not run commands, merge, deploy, or promote
  Skills.
- P24 is a shadow-level runtime admission for scheduler due-event intake.
  Scheduled items can enter the runtime as canonical Events with evidence and
  Owner Console receipts, while explicitly creating no Goals, Actions, standing
  tasks, direct tool execution, or autonomous long-running daemon.
- P25 is a shadow-level runtime admission for external handoff receipts.
  Reviewed MCP candidates and hashed A2A artifacts can be recorded as
  candidate-only runtime receipts with evidence and Owner Console visibility,
  without creating Actions, Goals, canonical Memory, or authority transfer.
- P26 is a shadow-level runtime admission for WeChat approval channels.
  WeChat W2 can draft approval requests and queue Owner decision Events with
  evidence and Owner Console visibility, while `ApprovalManager` remains the
  only approval authority and no Action executes from the channel.
- P27 is a shadow-level runtime admission for provider route receipts.
  Planner-owned model/provider routing can be recorded as evidence and Owner
  Console projection, preserving local/free/privacy policy evidence without
  making a model call, creating a Plan, or executing a tool.
- P28 is a shadow-level runtime admission for Skill candidate extraction.
  Repeated successful read-only action receipts can propose Skill candidates
  with evidence and Owner Console visibility, while remaining `PROPOSED` and
  never running sandbox validation, approval, promotion, rollback, or live
  deployment.
- P29 is a shadow-level runtime admission for learning epoch review. The
  runtime can record a frozen-learning baseline and an Owner-authorized
  candidate-only extraction pass over the same successful read-only receipts,
  preserving evidence and Owner Console visibility without approval, sandbox,
  Skill promotion, or live deployment.
- P30 is a shadow-level runtime admission for capability epoch audit. The
  runtime records the P01-P29 capability evidence state as
  `ADMIT_LOW_RISK_PREPARATION_ONLY` with `FUNCTIONAL_RUNTIME_ONLY` as the
  allowed conclusion, preserving no second authority, no Skill promotion, no
  live deployment, and no external writes.
- P31 is a shadow-level runtime admission for voice transcript ingress. The
  runtime can queue an already-transcribed local utterance as a canonical
  `channel:voice` Event with receipt evidence and Owner Console visibility,
  while explicitly performing no audio capture, speech-to-text, Goal creation,
  Action creation, planning, or tool execution.
- P32 is a shadow-level runtime admission for local notification drafts. The
  runtime can write an Owner-visible outbox draft for text or speech-script
  notification with receipt evidence and Owner Console visibility, while
  explicitly performing no external send, TTS, audio playback, Goal creation,
  planning, or Action execution.
- P33 is a shadow-level runtime admission for screen snapshot ingress. The
  runtime can queue an existing local screenshot asset as a canonical
  `channel:screen` Event with hashes, receipt evidence, and Owner Console
  visibility, while explicitly performing no OCR, UI control, external upload,
  Goal creation, planning, or Action execution.
- P34 is a shadow-level runtime admission for browser form drafts. The runtime
  can record a policy-bounded form intent with field names and a field digest,
  evidence, and Owner Console visibility, while explicitly performing no
  browser open, form submit, POST, download, Goal creation, planning execution,
  or Action execution.
- P35 is a shadow-level runtime admission for download quarantine drafts. The
  runtime can create a sandbox manifest for a policy-bounded download intent
  with evidence and Owner Console visibility, while explicitly performing no
  network fetch, file materialization, external write, Goal creation, planning
  execution, or Action execution.
- P36 is a shadow-level runtime admission for local document ingress. The
  runtime can queue an existing local document or PDF asset as a canonical
  `channel:document` Event with hash evidence and Owner Console visibility,
  while explicitly performing no text extraction, OCR, vector indexing,
  external upload, Goal creation, planning execution, or Action execution.
- P37 is a shadow-level runtime admission for document retrieval previews. A
  local document ingress receipt can become a Planner-owned read-only plan
  candidate visible in Owner Console task previews, while explicitly creating
  no Plan rows, Action rows, parsing, OCR, vector index, or tool execution.
- P38 is a shadow-level runtime admission for document read-only execution. A
  local document preview can pass through Planner admission, Policy preflight,
  ToolRegistry execution, receipts, and evidence using only `inspect_asset` and
  `read_file`, while explicitly performing no external writes, OCR, vector
  indexing, Skill promotion, or live deployment.
- P39 is a shadow-level runtime admission for document result projection and
  rollback. Document read-only execution receipts can enter candidate
  Memory/World projections, remain `INFERENCE` and `candidate_only`, and be
  rolled back without completing Goals, promoting Skills, or claiming final
  truth.
- P40 is a shadow-level runtime admission for document Skill candidates.
  Repeated successful document read-only trajectories may propose `PROPOSED`
  Skill candidates from `inspect_asset`/`read_file` sequences, while explicitly
  performing no sandbox validation, approval, promotion, active Skill enablement,
  Goal completion, or deployment.
- P41 is a shadow-level runtime admission for Skill sandbox starts. A
  `PROPOSED` document Skill candidate may enter a persisted `RUNNING`
  `skill_experiments` sandbox and transition to `SANDBOXED`, while explicitly
  performing no validation pass, approval, promotion, active Skill enablement,
  Goal completion, or deployment.
- P42 is a shadow-level runtime admission for the agentic task harness. Complex
  read-only tasks can be admitted, compiled into bounded DAGs, persisted,
  reloaded, leased, and projected through canonical `LivingSystem` state without
  creating a second authority or executing external workers.
- P43 is a shadow-level runtime admission for agentic acceptance traces. A
  leased node can be completed only after deterministic local acceptance checks
  pass, with trace digests and EvidenceLedger receipts visible in Owner Console;
  failed acceptance blocks the node instead of claiming completion.
- P44 is a shadow-level runtime admission for agentic file mailbox handoff.
  Local task/result envelopes can cross a filesystem mailbox as transport
  artifacts, but payload digests, acceptance checks, node completion, and Owner
  Console visibility remain under `LivingSystem.AgenticHarness` and
  `EvidenceLedger`.
- P45 is a shadow-level runtime admission for agentic repair candidates. Failed
  or blocked task nodes can produce bounded repair-candidate receipts with
  failure provenance, proposed non-executing steps, and Owner Console
  visibility, while preserving node state and avoiding a second planner or
  autonomous repair authority.
- P46 is a shadow-level runtime admission for agentic budget gates. A leased
  node can reserve or be blocked by bounded cost, token, time, and call limits
  with EvidenceLedger and Owner Console receipts, while performing no provider
  call, tool execution, retry, approval, or node completion.
- P47 is a shadow-level runtime admission for agentic benchmark scorecards.
  Existing acceptance, failure, repair-candidate, and budget receipts can be
  summarized into task-success, acceptance-coverage, evidence-coverage,
  hidden-failure, cost, and latency metrics visible in Owner Console, while
  executing no external benchmark suite and proving no product readiness.
- P48 is a shadow-level runtime admission for agentic checkpoint/resume.
  Graph checkpoint receipts preserve node and lease state; expired active
  leases can be resumed back to READY and reacquired by a restarted canonical
  harness, while inferring no worker result, retry execution, repair success,
  or task completion.
- P49 is a shadow-level runtime admission for agentic retry gates. A failed
  node with a repair-candidate receipt and remaining attempt budget can be
  returned to READY and leased again, while executing no retry, unblocking no
  downstream nodes, and inferring no repair success or task completion.
- P50 is a shadow-level runtime admission for agentic replan candidates.
  Failed or blocked graph state can produce a bounded graph-revision proposal
  with revision-budget evidence and Owner Console visibility, while mutating no
  graph, executing no retry, and inferring no downstream unblock or task
  completion.
- P51 is a shadow-level runtime admission for agentic worker lifecycle. Worker
  profiles can be registered, heartbeat receipts recorded, and stale workers
  marked so they cannot receive new leases, while no worker execution,
  delegated authority, or external memory/goal ownership is inferred.
- P52 is a shadow-level runtime admission for agentic result replay quarantine.
  Duplicate result envelopes are rejected before any second completion attempt,
  preserving mailbox evidence without granting external workers completion
  authority or claiming distributed execution maturity.
- P53 is a shadow-level runtime admission for worker lease heartbeat and stale
  worker recovery. Active leases can record heartbeat evidence and stale-worker
  leases can return to READY for canonical reacquisition, while no retry,
  worker success, or task completion is inferred.
- P54 is a shadow-level runtime admission for Worker Registry capability cards
  and candidate arbitration. Active compatible workers can be recommended with
  card digests and rejection reasons, while no lease, worker execution, or
  delegated completion authority is created.
- P55 is a shadow-level runtime admission for lease fencing reconciliation.
  Task envelopes carry a lease fencing token, and late stale-worker results are
  quarantined before completion after lease recovery or re-dispatch, preserving
  canonical DAG ownership.
- P56 is a shadow-level runtime admission for finalized mailbox artifacts.
  Artifact chunks are assembled only when the final digest matches, and canonical
  node completion still requires the normal result import and acceptance checks.
- P57 is a shadow-level runtime admission for evidence-derived worker trust.
  Quarantined stale results can disable future leases for a worker through a
  trust review receipt, without using worker self-report or promoting trust.
- P58 is a shadow-level runtime admission for the SandboxAdapter contract.
  A local fixture sandbox records environment and contract digests, denies
  network and secret access by default, verifies destroy, and does not imply
  remote execution or canonical task completion.
- P59 is a shadow-level runtime admission for offspring birth contracts.
  An isolated candidate home can hold a read-only birth contract, inheritance
  manifest, termination conditions, and identity boundary receipt, while no
  child runtime start, parent write, merge, deployment, Skill promotion,
  external access, or second LivingSystem authority is inferred.
- P60 is a shadow-level runtime admission for offspring isolated state and
  budget ledgers. The parent LivingSystem can initialize a child candidate
  `state/` directory with a state manifest, budget ledger, and non-running
  checkpoint, while no child runtime, parent database mount, task execution,
  absorption, or second authority is inferred.
- P61 is a shadow-level runtime admission for offspring retirement tombstones.
  A candidate can be frozen as `RETIRED_CANDIDATE` with an outcome summary
  before any future absorption gate, while no capability import, promotion,
  merge, deployment, child runtime execution, or second authority is inferred.
- P62 is a shadow-level runtime admission for offspring aggregate budget gates
  and no-gain hard stops. Parent-owned budget receipts reserve bounded
  dimensions, block over-grant requests before provider/tool calls, and record
  no-gain stops without task completion, promotion, absorption, or second
  authority.
- P63 is a shadow-level runtime admission for offspring checkpoint and fork
  receipts. The parent LivingSystem can record a minimal complete child
  checkpoint, verify tamper detection, and draft multiple fork candidates with
  lineage edges and independent budget ledgers, while no old lease replay,
  child runtime execution, promotion, merge, deployment, absorption, or second
  authority is inferred.
- P64 is a shadow-level runtime admission for offspring mailbox envelopes.
  Parent-child candidate communication uses a versioned
  `offspring-mailbox-v1` envelope with sender, recipient, task, attempt, parts,
  artifact references, child evidence, and digests. Unknown schema versions and
  damaged digests are quarantined, artifacts remain traceable to child
  evidence, and the parent retains all validation, approval, Goal, Skill,
  completion, merge, deployment, and absorption authority.
- P65 is a shadow-level runtime admission for offspring retirement cleanup.
  Retired candidates can produce a retention manifest and evidence bundle that
  preserves minimum lineage, budget, results, rejections, and tombstone data
  while reclaiming disposable child resources such as secrets, leases, sandbox
  mounts, and temporary credentials. A retired candidate cannot reserve more
  budget or receive task authority, and cleanup never imports capability,
  promotes Skill, merges code, deploys, or creates a second authority.
- P66 is a shadow-level runtime admission for paired baseline-candidate
  experiments. A preregistration locks model, harness, environment, budget,
  evaluator, baseline digest, candidate diff, and expected effect before paired
  fixture cases are scored. Condition drift makes the experiment invalid,
  repeated cases produce a stability summary, negative and failure samples are
  retained, and `CANDIDATE_VALIDATED` still does not imply promotion,
  absorption, approval, Skill advancement, holdout mutation, or threshold
  mutation.
- P67 is a shadow-level runtime admission for immutable holdout epochs.
  Evaluator, holdout manifest, and thresholds are frozen with digests and an
  epoch id before candidate scoring. Same-epoch reruns can be compared, but
  holdout or threshold drift invalidates the run and requires rebaseline.
  Candidate workspaces receive no holdout write permission, and failed epoch
  checks do not change approval, promotion, Skill, holdout, evaluator, or
  threshold authority.
- P68 is a shadow-level runtime admission for promotion bundles and rollback
  gates. A bundle can name selected capabilities, patch evidence, Skill refs,
  epoch, budget, limits, and rollback assets. Owner approval is bound to the
  exact bundle digest, actor, time, and approved scope; canary preparation is
  blocked before approval and cannot exceed that scope. Rollback assets must
  cover code, database, config, and Skill state before any future promotion,
  and all P68 receipts leave canonical state unchanged.
- P69 is a shadow-level runtime admission for transfer, regression, and
  efficiency audits. Candidate capability evidence is checked across same-domain
  holdout, cross-domain transfer, model/environment variants, organ regression
  rows, and success-per-cost thresholds. Best-only reporting is flagged when
  any transfer row fails, zero key regression is required for advancement, and
  efficiency degradation can reject an otherwise successful candidate. The
  audit produces a decision receipt only; no partial promotion, Owner exception,
  approval, or canonical mutation is inferred.
- P70 is a shadow-level runtime admission for final delivery audit receipts.
  Owner Console result rows must trace back to input, tool, receipt, approval,
  and artifact references; installer/recovery evidence must cover preflight,
  backup, apply, verify, rollback, and uninstall phases; and the claim ledger
  separates `CODED`, `TESTED`, `CAMPAIGN`, and `EXTERNAL` evidence levels.
  Claims above their evidence level are blocked, UI `UNKNOWN` coverage blocks
  delivery, and the audit mutates no live installation or public release state.
- P74 is a shadow-level runtime admission for candidate delivery readiness.
  The audit binds candidate branch and commit, exact Owner campaign commands,
  required R01-R30/R31-R40 campaign assets, rollback steps, and no-live-mutation
  boundaries into a receipt. It can mark a candidate ready for Owner review or
  blocked by missing commands/assets/boundaries, but it performs no merge,
  deployment, package installation, Skill promotion, or live configuration write.
- P75 is a shadow-level runtime admission for packaging layout authority. It
  records the result of the repository packaging layout contract as a receipt,
  requiring one project manifest, one package root, and one version file. A
  failed check blocks packaging readiness without building a wheel, installing
  a package, mutating the live instance, or creating a second source authority.
- P76 is a shadow-level runtime admission for installed-package tail checks.
  It records disposable installed-instance compatibility reports, required
  check outcomes, live config/database hashes before and after, and optional
  status-smoke results. Hash drift, failed required checks, or smoke failure
  block the receipt without installing, upgrading, deploying, merging, or
  mutating the live instance.
- P77 is a shadow-level runtime admission for operational preflight. It binds
  runtime status, completed-cycle evidence, pending-action state, integrity
  report, startup-resume state, and runtime/daemon lease probes into a receipt.
  A paused/killed runtime, pending approval/reconciliation, failed integrity, or
  unavailable lease blocks the receipt without starting a daemon or modifying
  live installation, config, or database state.
- P78 is a shadow-level runtime admission for the Owner Console readiness view.
  `/api/product` exposes a top-level read-only delivery readiness summary over
  P74-P77 receipts, and the static product panel renders that summary as compact
  candidate evidence cards. The view performs no direct tool execution and never
  writes canonical runtime state.
- P79 is a shadow-level runtime admission for delivery handoff packages. A
  handoff binds readiness summary, candidate branch and commit, recent test
  results, exact Owner campaign commands, rollback steps, and no-live-mutation
  boundaries. The repository script can export the JSON package, but it does
  not run campaigns, merge, deploy, install packages, or promote Skills.
- P80 is a shadow-level runtime admission for release state audit. The audit
  summarizes current repository candidate evidence across final delivery,
  readiness, packaging, installed-tail, operational-preflight, and handoff
  receipts; required source assets; regression results; and no-live-mutation
  boundaries. Missing evidence blocks the release state without changing live
  installation, starting daemons, merging, deploying, or promoting Skills.
- P81 is a shadow-level runtime admission for release handoff summary. The
  Owner Console and delivery handoff JSON expose P79/P80 release-state
  readiness as read-only candidate evidence, so the Owner-facing delivery layer
  no longer depends only on earlier P74-P77 readiness receipts.
- P82 is a shadow-level runtime admission for Owner Console goal metadata
  persistence. UI-created Projects and Tasks bind origin, rationale, task_spec,
  and risk into the canonical GoalStore, preserving one Goal authority instead
  of letting the UI keep its own task semantics.
- P83 is a shadow-level runtime admission for UI hardening audit receipts. The
  audit records D01-D18 local defect coverage, loopback/security/no-token/no-UI
  authority boundaries, local regression results, and explicitly keeps
  real-browser Owner-host E2E as a separate gate.
- P84 is a shadow-level runtime admission for offspring ecology audits. It
  compares candidate population productivity, niche diversity, budget cost, and
  failure status while preserving no archive-search execution, no absorption,
  no promotion, and no second authority.
- P85 is a shadow-level runtime admission for delivery gap audit receipts. It
  records D:\WLS-Dev package absorption, P/R coverage, repository checks, and
  unresolved Owner-host gates without converting owner gates into repository
  readiness or mutating live state.
- P86 is a shadow-level runtime admission for UI package absorption receipts. It
  maps the v1/v1.1 UI runtime work packages, hardened D01-D18 defect ledger,
  package hash, payload inventory, and current-source compatibility checks into
  canonical runtime evidence without overwriting newer UI code, installing the
  package, adding dependencies, migrating the database, or creating a second UI
  authority.

## Projection Rule

Future graph, markdown, vector, CRDT, Notion-like, Obsidian-like, or UI views
are projections over the same history. They may help the Owner read, navigate,
and annotate WLS, but they cannot override canonical SQLite state or evidence.

The preferred first implementation remains local SQLite with canonical tables,
FTS, graph-shaped projection tables, temporal validity fields, and optional
vector indices after measured need. A separate graph database or knowledge
system is not admitted as a second authority.

Reality, cognition, and possibility stay separated:

- reality: Events, Actions, tool receipts, commits, test output, Owner
  decisions, costs, failures, and artifacts that actually happened;
- cognition: derived memories, facts, skills, relationships, causes, and
  interpretations with provenance and revision history;
- possibility: plans, hypotheses, predictions, simulations, and counterfactuals
  that must not pollute fact state.

Skill and security learning follow the same path: signature, quarantine rule,
test, future detection, false-positive review, and rollback.

Run:

```powershell
.\.venv\Scripts\python.exe source\scripts\run_architecture_validation.py --output outputs\architecture_validation.json
```
