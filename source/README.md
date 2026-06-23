# Workstation Living System

Version: `0.7.0.dev1`

The canonical implementation is `src/wls/runtime.py::LivingSystem`. It provides durable state, configured sensing, evidence-tagged world facts, bounded attention, memory retrieval, governed tools, outcome evaluation, consolidation, and an evidence-bound growth cycle.

## Growth cycle

```text
repeated action failures
  -> recovery experiment against a frozen baseline
  -> versioned skill proposal
  -> isolated validation
  -> explicit human approval and promotion
  -> later task reuse through LivingSystem.run_cycle
  -> measured RETAIN or ROLLBACK_REQUIRED decision
  -> human-authorized rollback when required
```

Machine transitions require persisted experiment records, matching digests, predetermined thresholds, complete case success, and zero regressions. Approval, promotion, and rollback retain explicit human gates.

## Verify

```bash
python -m pip install -e ".[dev]"
python -m pytest tests -q
python scripts/verify_evolution_target_001.py
python -m ruff check src tests scripts
python -m mypy src/wls tests scripts --ignore-missing-imports
python -m bandit -q -r src/wls scripts
python -m build
```

Verification record: `verification/EVOLUTION_TARGET_001_LOCAL_20260624.json`.

## Run

```bash
wls init --home /path/to/wls-home
wls --config /path/to/wls-home/config.json self-check
wls --config /path/to/wls-home/config.json once
wls --config /path/to/wls-home/config.json status
```

Growth commands:

```bash
wls --config CONFIG failure-candidates
wls --config CONFIG growth-recover CANDIDATE_ID --strategy contract_recovery
wls --config CONFIG growth-propose GROWTH_CYCLE_ID --name recovered-skill
wls --config CONFIG growth-validate GROWTH_CYCLE_ID
wls --config CONFIG growth-promote GROWTH_CYCLE_ID --actor OWNER --authorization-reference REF --human-approved
wls --config CONFIG growth-reuse GROWTH_CYCLE_ID --goal "later task" --criterion "measurable success"
wls --config CONFIG growth-rollback GROWTH_CYCLE_ID --actor OWNER --authorization-reference REF --human-approved
wls --config CONFIG growth-status
```

The implementation and retain/rollback paths are verified in isolated temporary runtimes and CI. Long-term benefit on the intended Windows workstation remains unverified. Claims remain limited to observable software behavior.
