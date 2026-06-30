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

## Current Admissions

- P01 is admitted as a registry and authority guard.
- P02-P10 are design-level admissions backed by contract tests.
- Computer use remains blocked without a verified disposable sandbox.
- MCP discovery is rejected unless identity is pinned and reviewed.
- A2A and coding outputs remain candidate-only.
- Owner Console and WeChat W0/W1 are read-only projection/Event paths.

Run:

```powershell
.\.venv\Scripts\python.exe source\scripts\run_architecture_validation.py --output outputs\architecture_validation.json
```
