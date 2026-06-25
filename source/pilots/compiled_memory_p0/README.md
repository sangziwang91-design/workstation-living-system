# WLS Compiled Memory P0

This is a read-only memory-organ pilot aligned with EXP-082. It is not a new system, does not replace WLS MemoryStore, and never writes Notion.

## Budget gate

- Time: maximum 4 hours per week
- Incremental API/token: maximum USD 20 per month
- P0 corpus: exactly 30 sources
- R-loop: 2 counterexamples per promoted claim
- Scope expansion: disabled

## Run

```powershell
python source/pilots/compiled_memory_p0/WLS_COMPILED_MEMORY_P0.py --self-test
python source/pilots/compiled_memory_p0/WLS_COMPILED_MEMORY_P0.py --query "What is the canonical WLS runtime?"
```

The script writes a disposable generated projection under `source/pilots/compiled_memory_p0/generated/`. Use `--output-root PATH` to place it in a local git/Obsidian vault.

## Implemented P0 checks

- `check_sources()` enforces T3: every VERIFIED claim resolves to registered GitHub/Notion sources.
- `check_stale()` enforces T2: known stale and superseded assertions are linted.
- `check_claim_ceiling()` enforces T4: unsourced VERIFIED upgrades and overclaim phrases fail.
- `check_canonical_conflict()` enforces T5: historical or superseded plans cannot enter current state.
- `check_state_recovery()` enforces T1: ten cross-source state questions return the expected value and clickable sources.

## Hard boundaries

- GitHub/runtime evidence remains the engineering truth.
- Notion is read-only semantic and contradiction context.
- Generated CURRENT_STATE.md is disposable and lower authority than canonical anchors.
- WLS MemoryStore, CURRENT_STATE.yaml, .evolution/CURRENT_CHAIN.json, and Notion are not modified.
- No network fetch, model API, vector database, service process, or automatic writeback.
- P0 pass is sandbox evidence only; it does not prove real-host utility or production maturity.
