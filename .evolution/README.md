# `.evolution/` Control Plane

This directory is the versioned control plane for repo-native WLS evolution. It does not replace `LivingSystem`, `CURRENT_STATE.yaml`, the evidence chain, policy gates or owner authority.

## Files

- `CURRENT_CHAIN.json` — current control-plane state and active GitHub/Notion anchors.
- `EVOLUTION_PACKET.schema.json` — machine-readable task contract.
- `worker_registry.json` — admitted workers and their permissions.
- `notion_sync_contract.json` — bidirectional GitHub/Notion boundary.
- `roadmap/WLS_EVOLUTION_ROADMAP.md` — ordered future route; later stages are candidates, not automatically authorized work.
- `queue/*/*.json` — task packets grouped by lifecycle state.

## Invariants

1. GitHub and runtime evidence are authoritative for engineering state.
2. Notion contributes semantic history and candidate context, never unverified implementation claims.
3. Exactly one code worker is ACTIVE during bootstrap: `chatgpt_interactive`.
4. Other model providers remain `PENDING_NOT_ACTIVATED` until an explicit admission target is verified.
5. Every task names allowed paths, forbidden changes, deterministic acceptance conditions and stop conditions.
6. No packet may authorize a parallel `brain`, `core`, `v2`, `final` or replacement runtime.
7. High-risk actions, skill promotion, rollback and claim elevation retain owner gates.
8. A failed or blocked task remains evidence. It is not rewritten as success.
9. Completed packets are append-only historical records; a correction points to the superseded packet.
10. CI validates the control-plane structure but does not prove real-host or longitudinal benefit.

## Claim classes

- `VERIFIED` — supported by runtime output, GitHub commit/PR/Actions, deterministic test or owner-confirmed external evidence.
- `INFERENCE` — reasoned interpretation with named supporting evidence.
- `NOTION_CONTEXT` — semantic history retrieved from Notion; must be rechecked against GitHub before engineering use.
- `USER_REPORTED` — owner report not independently verified by the current worker.
- `UNKNOWN` — not established.

## Worker degradation

When the primary model loses high-capability access, the chain does not stop. The worker may only claim packets whose `minimum_worker_capability` is `bounded_deterministic` and whose acceptance is executable without architectural invention.

Allowed degraded-mode work includes focused tests, schema validation, fixtures, documentation alignment, lint/type repair and a small path-bounded implementation. Architecture changes, claim elevation, security policy changes and cross-runtime migration wait for `architecture_and_review` capability.

## Notion anchor

- Page ID: `38940ff6ad6281b6bd69d700c9322d77`
- Page URL: <https://app.notion.com/p/38940ff6ad6281b6bd69d700c9322d77>
- Title: `EXP-082 · Repo-Native Model Labor & WLS Evolution Chain · GitHub × Notion AI × ChatGPT · 2026-06-24`

The validator checks that this anchor matches `CURRENT_CHAIN.json` and `notion_sync_contract.json`.