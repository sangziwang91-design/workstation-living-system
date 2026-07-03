# WLS Agentic Harness V1

## Evidence Baseline

This branch absorbed the local `D:\WLS-Dev` deep-leap package family as
repository-level source material only. The packages were not installed into the
live WLS instance and do not become runtime authorities.

Verified local package hashes:

- `WLS_AGENTIC_DEEP_LEAP_v1.0.zip`:
  `0943912ae20a2bc13a8734abd42b6a9a11bb690be1fd90f0f953f31e71167e8c`
- `WLS_DEEP_LEAP_PACK_2026-07-03.zip`:
  `a9ca7d482670da8158844a1bed36cf22b52b3bccd8e2691a949e155eafce34b4`
- `WLS_DEEP_LEAP_20260703.zip`:
  `302901fbcc8a3a9129b5268fc26c090272b0c33d772ade87e0653684bf46a3f9`
- `WLS-OFFSPRING-EVOLUTION-001.zip`:
  `0bce1172f619e1c7eeab15c4ffccde01badb6751cbea767cf95ce7e5b4ee56f7`
- `wls_offspring_evolution-0.1.0-py3-none-any.whl`:
  `206b9f1e36b9379305df5482a869c9fea1ca1cde897db47949ac11cd51d8d997`
- `WLS_LIVING_AGENT_OS_CAPABILITY_EVOLUTION_v1.0.zip`:
  `a57417d578a16a167e4dc3608cf0617457a339027abc2e0d67a8a65602074433`

The Wheel hash was confirmed from the local screenshot in `D:\WLS-Dev` rather
than an adjacent `.sha256` file.

## Deduplication Decision

The older Living Agent OS capability package is mostly covered by the existing
capability branch: canonical authority registry, browser/computer/coding/MCP/A2A
organ contracts, Owner Console projection, provider routing, scheduler, document
ingress, Skill candidate extraction, and Skill sandbox starts.

The non-duplicate gap from the deep-leap packages is the Agentic Execution
Harness foundation:

- deterministic task admission and risk floor;
- explicit task contract fields;
- bounded DAG compilation;
- durable graph state in the existing SQLite database;
- one active writer lease per graph conflict domain;
- Owner Console visibility.

The offspring package is not integrated in this batch. It describes future
non-authoritative experimental descendants and remains downstream of the
canonical task harness.

## Authority Rule

`LivingSystem` remains the only runtime subject. The harness stores candidate
task graph state in canonical WLS SQLite tables and records receipts through the
existing `EvidenceLedger`.

The harness does not create or replace:

- GoalStore;
- MemoryStore;
- Planner;
- PolicyEngine;
- ApprovalManager;
- ToolRegistry;
- SkillLibrary;
- EvidenceLedger;
- live deployment state.

## Batch 1 Coverage

This branch implements the safe foundation slice of the deep-leap taskset:

- L00: canonical no-second-authority boundary is documented and tested;
- L01: deterministic task admission classifier;
- L02: task intent contract with acceptance, evidence, and rollback fields;
- L03: task graph compiler with dependency and cycle validation;
- L04: durable task graph state in `agentic_task_intents`,
  `agentic_task_graphs`, and `agentic_node_leases`;
- L05: conflict-domain lease scheduling for ready task nodes.

## Claim Ceiling

The current implementation proves repository-level admission, graph persistence,
restart reconstruction, and lease conflict behavior. It does not prove external
worker execution, model routing, browser automation, code repair throughput,
offspring evolution, vendor parity, deployment readiness, or live-host behavior.

## Batch 2 Coverage

This branch now adds the next bounded substrate without leaving the canonical
runtime:

- L06: Context Manifest V1 selects task intent, task graph, and canonical active
  memory records deterministically;
- L07: Worker Registry V1 persists local shadow worker profiles;
- L08: node leases require an active worker profile and record profile assignment
  receipts before any node is marked leased.
- L09: leased nodes can be bound into canonical `Plan`/`Action` rows after
  `PolicyEngine` decision, without executing tools, consuming approvals, or
  completing the node.
- L10: failed node attempts receive deterministic first-pass attribution as
  LOCAL, UPSTREAM, STRUCTURAL, POLICY, or ENVIRONMENT and are persisted as
  evidence-linked receipts.
- L11: bound READ/no-side-effect node actions can execute through the canonical
  action path and feed success/failure back into the task graph without external
  worker delegation, writes, approval consumption, or live deployment.

## Next Batches

Recommended next increments:

1. Offspring birth contract: only after the parent harness can evaluate bounded
   task graphs without second authority drift.
2. Harness epoch audit: prove no-second-authority, no-live-write, owner-gate,
   and evidence-retention invariants across P42-P45 receipts.
