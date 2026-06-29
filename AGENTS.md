# WLS Engineering Agent Contract

## Canonical authority

1. Executable code and discriminating tests.
2. `CURRENT_STATE.yaml`.
3. `LIVING_SYSTEM_GENOME.md`.
4. `.evolution` contracts and append-only evidence.
5. Prose reports, which may never outrank the exact tested head.

The only canonical runtime is `source/src/wls/runtime.py::LivingSystem`. Do not create a second brain, planner, event store, goal authority, memory authority, skill authority, policy engine, or canonical state database.

## Evidence discipline

Use `VERIFIED`, `INFERENCE`, `USER_REPORTED`, and `UNKNOWN` accurately. A local pass is not a GitHub pass; a GitHub pass is not owner-host proof; a candidate implementation is not merge authorization. Every final report must bind its commands, Wheel, soak, and test results to one exact head SHA.

A verifier must contain negative controls and exit nonzero on failure. Generated databases, keys, Wheels, virtual environments, caches, and timestamped intermediate reports are not source artifacts.

## Execution roles

- **Jules:** candidate implementation and focused tests only.
- **Codex:** cross-module repair and adversarial review.
- **GPT:** architecture, Git integration, evidence review, and acceptance control.
- **Local Windows runner:** authoritative command execution and measurement when GitHub-hosted capacity is unavailable.
- **Owner:** merge, enablement, deployment, and irreversible authorization.

No agent may infer permission to merge, deploy, enable a provider, start Task20, or modify the live owner host.

## Required engineering loop

`READ -> REPRODUCE -> MODIFY -> FOCUSED TEST -> AFFECTED REGRESSION -> FULL LOCAL GATE -> REVIEW -> OWNER DECISION`

Keep pull requests Draft until exact-head evidence is available. Stop when the requested capability would require a parallel authority, weaken policy or approval gates, expose secrets, alter live owner data, or exceed the stated claim ceiling.

## Current Task19 boundary

Task19 is a main-based rebuild. Historical PR #22 is source material, not the integration authority. The Provider Hub remains candidate-only and does not attach itself to canonical planning. Task20 and the causal shadow remain blocked until Task19 receives owner acceptance after local Windows verification.
