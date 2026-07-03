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
  voice, and workbench paths are organs fitted to existing authorities;
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
