# SURVIVAL-TARGET-001 · Bounded Long-Run Runtime Survival

## Task packet

- **Object:** canonical `source/src/wls/runtime.py::LivingSystem`
- **Goal:** contain transient daemon faults, enforce bounded resource budgets, persist operational evidence, and recover interrupted runtime state without creating another runtime authority.
- **Excluded:** connection to the owner's Workstation/five-system environment; Windows service installation; scheduled tasks; external publication; unrestricted autonomous repair.
- **Input baseline:** EVOLUTION-TARGET-001 merged into `main`; KU-002 remains unknown.
- **Acceptance:**
  - transient cycle failures are recorded and retried with bounded backoff;
  - consecutive failure exhaustion pauses the runtime rather than spinning indefinitely;
  - event backlog and database-size limits stop new daemon cycles before further growth;
  - heartbeats, incidents, peaks, termination reasons, and run reports are persisted;
  - interrupted `RUNNING` cycles and survival runs are reconciled on restart;
  - bounded real-runtime soak preserves database and evidence-chain integrity;
  - Linux/Windows Python 3.11/3.13 tests, lint, typing, security scan, wheel build, and clean-install self-check pass.
- **Permission boundary:** pause and evidence writes are allowed; deletion, publication, host installation, and owner-system integration are not.
- **Failure condition:** any bypass of policy, evidence, approval, kill switch, or canonical runtime authority.
- **Stop condition:** stop after CI evidence is persisted and the draft PR is ready for owner review; do not merge without explicit authorization.

## Claim ceiling

A passing implementation may claim a bounded, CI-verified daemon survival workflow under synthetic fault, resource-budget, restart, and short soak scenarios. It may not claim 24/7 production maturity, deadlock freedom, indefinite autonomy, external host effectiveness, consciousness, or unrestricted self-preservation.

## Reproduction

```bash
cd source
python -m compileall -q src
python -m pytest -q tests/test_survival.py
python -m pytest -q
python -m ruff check src tests
python -m mypy src/wls tests --ignore-missing-imports
python -m bandit -q -r src/wls
python -m build --wheel
```
