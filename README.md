# Workstation Living System (WLS)

> **Status:** `0.1.0a1` experimental alpha. This repository is a research and engineering baseline, not a completed software lifeform, conscious system, AGI, production autonomous operator, or demonstrated general self-improving agent.

WLS is a local-first Python runtime for testing a bounded loop:

`configured observation → evidence-grounded state → bounded decision → governed action → external result → recorded learning`

The project is intentionally evidence-gated. A class name, prompt, database row, or passing unit test does not establish that a human-like capability exists.

## What Round 001 verifies

- durable ingestion and worker-bound event acknowledgement;
- bounded attention that reserves capacity for real external events;
- SQLite persistence and an append-only evidence chain;
- action approval bound to the complete action semantics;
- crash states that do not automatically replay uncertain side effects;
- bounded episodic memory without recursive payload growth;
- one narrow, schema-validated path by which a trusted procedural/failure memory can alter a later deterministic plan;
- packaging, clean-environment tests, static checks, and release verification from the exported tree.

See [`CLAIM_CEILING.md`](CLAIM_CEILING.md) for the exact claim boundary and [`docs/ROUND_001_BASELINE.md`](docs/ROUND_001_BASELINE.md) for measured evidence.

## What is not yet established

- reliable long-running operation on the real Windows Workstation;
- integration with SZ Hub, Workstation, GBSF, GBDS, or APF;
- broad environmental understanding;
- human-like emotion or subjective experience;
- general continual-learning advantage;
- autonomous code repair, unrestricted self-modification, or production safety;
- external social, scientific, or commercial value.

## Repository layout

```text
source/                  Python package and committed tests
dist/                    bundled alpha wheel
docs/                    architecture and Round 001 evidence
benchmarks/              deterministic baseline benchmark
.github/workflows/       clean CI verification
INSTALL.ps1              offline Windows installer
VERIFY_RELEASE.py        exported-tree release verifier
AGENTS.md                execution contract for coding agents
CURRENT_STATE.yaml       evidence-bounded project state
```

## Developer verification

Python 3.11 or newer is required.

```bash
cd source
python -m pip install -e ".[dev]"
python -m pytest --cov=wls --cov-branch --cov-report=term-missing
python -m ruff check src tests
python -m mypy src
python -m bandit -q -r src
```

Verify the bundled release without relying on `.git`:

```bash
python VERIFY_RELEASE.py
```

The verifier checks the wheel hash, installs the bundled wheel into a temporary virtual environment with `--no-index`, initializes an isolated home, and runs package self-check and integrity verification.

## Windows installation

The installer creates a read-only standalone installation under `D:\Workstation\.wls`. It does not connect the five existing systems, register a service, create a scheduled task, publish data, or enable general write access.

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\INSTALL.ps1 -WorkstationRoot "D:\Workstation"
.\VERIFY.ps1 -WorkstationRoot "D:\Workstation"
```

## Engineering contract

Every development round must contain a reproducible failing baseline, acceptance criteria, committed tests, an adversarial case, benchmark or behavioral delta, claim-ceiling update, and pull request. Valid round outcomes are only `PASS`, `PARTIAL`, `FAIL`, or `INVALIDATED`.

Real credentials, patient data, private Notion content, and Workstation databases are prohibited from this repository.
