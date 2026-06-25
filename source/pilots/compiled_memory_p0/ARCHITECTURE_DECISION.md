# P0 Architecture Decision - WLS Compiled Memory

## Budget gate

- Time: maximum 4 hours per week
- Incremental API/token: maximum USD 20 per month
- Existing subscriptions are excluded from incremental budget
- Corpus: exactly 30 sources
- R-loop depth: 2 counterexamples per promoted claim
- Scope expansion is prohibited in P0

## Candidate A - Direct WLS database projection

Read GitHub/Notion snapshots and write compiled items into existing WLS SQLite memory tables.

Pros:
- Immediate reuse by LivingSystem.
- Existing memory attribution and retrieval can consume the data.

Cons:
- Violates P0 read-only isolation.
- A compiler error contaminates the sole MemoryStore authority.
- Rollback becomes a database migration problem.
- Notion-derived semantics could leak into current-state authority.

R-loop rejection:
1. Assume the compiler is wrong: the error enters canonical memory rather than a disposable projection.
2. Assume a stale Notion page is newer: direct database write creates silent current-state drift.

Decision: rejected for P0.

## Candidate B - Separate local service or vector database

Create a new local memory service with a vector database and model API.

Pros:
- Flexible semantic retrieval.
- Independent scaling and API surface.

Cons:
- Functionally becomes a sixth system.
- Adds process lifecycle, secrets, networking, migration and backup burden.
- Semantic similarity does not enforce authority, freshness or Claim Ceiling.
- Exceeds the P0 budget and introduces unnecessary provider dependence.

R-loop rejection:
1. Assume embeddings retrieve the wrong historical page: similarity cannot resolve authority.
2. Assume the service is unavailable: context recovery becomes dependent on a new runtime.

Decision: rejected.

## Candidate C - Git-native manifest plus generated Markdown projection

Keep a fixed source manifest, evidence-bound claims, conflict fixtures and generated Markdown in a local git repository or Obsidian vault. Validators run before every generated state projection. WLS and Notion remain untouched.

Pros:
- No new runtime or sixth system.
- Human-readable, diffable and rebuildable.
- Obsidian is only a view over Markdown.
- Historical material remains searchable without becoming current.
- Easy rollback: delete and regenerate the projection.
- Works without model API after claims have been compiled.

Cons:
- P0 retrieval is intentionally narrow.
- Full-text and scale performance are not tested.
- Local corpus scanning still requires owner-host execution.
- Git does not itself solve semantic deduplication.

R-loop:
1. Assume generated Markdown is wrong: validators and source links expose the error, and the projection is disposable.
2. Assume an obsolete source matches a query: lifecycle and canonical-conflict checks prevent promotion into CURRENT_STATE.
3. Assume Notion is newer than GitHub: authority rank prevents semantic recency from overriding engineering truth.
4. Assume the strong model disappears: deterministic source and claim records plus validators still run.

Decision: selected for P0.

## Authority rule

Executable code and GitHub current-state anchors outrank GitHub PR evidence, which outranks WLS identity documents, which outrank Notion semantic context and historical archives for engineering-current-state questions.

This ordering applies only within the same scope. Notion can remain authoritative for semantic interpretation while still being unable to change engineering state.

## P0 stop condition

Stop after T1-T5 pass on the fixed 30-source corpus. Do not begin P1, connect a vector database, modify WLS MemoryStore, write Notion, or expand the corpus.
