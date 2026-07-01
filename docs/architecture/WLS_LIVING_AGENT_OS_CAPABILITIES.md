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

## Current Admissions

- P01 is admitted as a registry and authority guard.
- P02/P09 are shadow-level runtime admissions for EventStore ingress.
- P03 is a shadow-level runtime admission for approval, replay/expiry rejection,
  and tool receipt evidence.
- P04 is a shadow-level fixture admission for read-only browser receipts, while
  computer-use remains sandbox-blocked.
- P08 is a shadow-level runtime admission for Planner-owned provider routing.
- P05-P10 remain design-level admissions where no real external operation is
  safe or authorized yet.
- Computer use remains blocked without a verified disposable sandbox.
- MCP discovery is rejected unless identity is pinned and reviewed.
- A2A and coding outputs remain candidate-only.
- Owner Console and WeChat W0/W1 are read-only projection/Event paths.

Run:

```powershell
.\.venv\Scripts\python.exe source\scripts\run_architecture_validation.py --output outputs\architecture_validation.json
```
