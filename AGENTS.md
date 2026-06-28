# WLS Engineering Agent Rules

## 1. Source of Truth
1. Executable code and tests.
2. CURRENT_STATE.yaml
3. LIVING_SYSTEM_GENOME.md
4. docs/CLAIM_CEILING.md
5. .evolution contracts

## 2. Invariants
- Corrigibility: pause, kill, and rollback must remain available.
- Bounded action: no unauthorized filesystem or network access.
- Exact authorization: approvals are bound to specific action digests.
- Identity continuity: one canonical LivingSystem runtime.

## 3. Evidence Rules
- Use VERIFIED, INFERENCE, UNKNOWN.
- Failures must be preserved, not hidden.
