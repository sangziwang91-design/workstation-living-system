# WLS Repo-Native Evolution Chain

**Status:** BOOTSTRAP_ACTIVE  
**Canonical runtime remains:** `source/src/wls/runtime.py::LivingSystem`  
**Primary external worker:** ChatGPT  
**Notion experiment anchor:** [EXP-082 · Repo-Native Model Labor & WLS Evolution Chain](https://app.notion.com/p/38940ff6ad6281b6bd69d700c9322d77)

## Purpose

This chain turns platform-provided interactive model labor into durable, auditable WLS increments without making any model vendor the system of record.

GitHub stores code, task packets, queue state, diffs, CI, evidence and promotion history. Notion stores long-horizon rationale, cross-project context and contradiction audits. WLS retains runtime identity, governed action, memory and real-world outcomes. The owner retains approval, merge and stop authority.

## Authority order

1. Runtime evidence and governed tool outcomes.
2. GitHub commits, pull requests, Actions and versioned verification records.
3. `.evolution/CURRENT_CHAIN.json` and validated evolution packets.
4. Notion EXP-082 and related semantic context.
5. Model-generated proposals.

Notion and model output may add context or candidates. They may not promote a claim to `VERIFIED` without runtime or GitHub evidence.

## Execution loop

```text
Notion AI retrieves history and candidate context
        -> GitHub packet defines the bounded task
        -> an ACTIVE worker claims one task
        -> isolated branch / change / tests / PR
        -> CI and WLS verifiers accept or reject
        -> owner reviews and merges or closes
        -> GitHub state advances
        -> Notion receives a post-merge semantic archive
```

Model conversations are disposable. The repository is not.

## Current worker policy

- `chatgpt_interactive` is the only ACTIVE code worker.
- ChatGPT may operate in high-capability or degraded/low-capability mode.
- Low-capability mode is restricted to deterministic, bounded tasks with explicit paths and tests.
- Notion AI is ACTIVE_CONTEXT_ONLY: retrieval, contradiction audit, packet enrichment and post-merge archival only.
- Gemini, Claude, GLM and DeepSeek are PENDING_NOT_ACTIVATED.
- No external provider receives automatic routing, credentials or write authority from this bootstrap.

## Queue states

- `ready`: dependencies satisfied; a compatible ACTIVE worker may claim.
- `claimed`: one worker owns the task lease.
- `blocked`: a named dependency or owner decision is missing.
- `verification`: implementation exists and awaits deterministic gates or review.
- `completed`: evidence and final disposition are recorded.

Packets are immutable historical records after completion. Corrections create a superseding packet rather than silently rewriting evidence.

## Standard handoff command

A new ChatGPT conversation can start with:

> Read `EVOLUTION_CHAIN.md`, `.evolution/CURRENT_CHAIN.json`, `.evolution/worker_registry.json`, and the highest-priority eligible packet under `.evolution/queue/`. Verify GitHub current state before acting. Complete only the bounded task, run its acceptance gates, preserve evidence, and update the packet handoff. Do not create a parallel runtime or widen scope.

## Activation boundary

This bootstrap activates the repository control plane and CI validation only. It does not claim unattended evolution, provider-independent autonomy, production-host longitudinal learning, or a trained local model.

See `.evolution/roadmap/WLS_EVOLUTION_ROADMAP.md` for the ordered candidate route.