# Workstation Living System (WLS)

**Current development version:** `0.9.0.dev2`

WLS is a persistent, bounded and corrigible workstation runtime. It observes explicitly configured environments, maintains durable state and evidence-tagged world facts, allocates finite attention, retrieves memory, plans, acts through governed tools, evaluates outcomes, and consolidates experience.

WLS does **not** claim subjective consciousness, genuine emotion, AGI, personhood, or unrestricted self-modification. Claims are limited to observable software behavior and retained evidence.

## Canonical authorities

```text
project root:      .
build metadata:    pyproject.toml
package root:      source/src/wls
runtime authority: source/src/wls/runtime.py::LivingSystem
version authority: source/src/wls/_version.py::__version__
tests:             source/tests
verification:      source/scripts
```

Parallel `v2`, `final`, `brain`, replacement runtimes, second `pyproject.toml` files, and duplicate `wls` package trees are not canonical. Mission and runtime invariants are defined in [`LIVING_SYSTEM_GENOME.md`](LIVING_SYSTEM_GENOME.md). Current machine-readable state is in [`CURRENT_STATE.yaml`](CURRENT_STATE.yaml). Repository-layout and version rules are defined in [`docs/REPOSITORY_LAYOUT_AND_VERSION_POLICY.md`](docs/REPOSITORY_LAYOUT_AND_VERSION_POLICY.md).

## Install

Run all project installation and build commands from the repository root:

```bash
python -m pip install -e ".[dev]"
wls --help
python -m wls --help
```

`source/` contains source code, tests, verification scripts, configuration examples, and verification records. It is not a second installable project root.

## Implemented growth path

`EVOLUTION-TARGET-001` is implemented as one evidence-bound lifecycle:

```text
repeated real action failures
  -> failure candidate
  -> frozen baseline recovery experiment
  -> versioned skill proposal
  -> isolated skill validation
  -> explicit human approval and promotion
  -> later task execution through the canonical runtime
  -> post-promotion measurement
  -> RETAIN or ROLLBACK_REQUIRED
  -> tested human-authorized rollback
```

Machine stages require persisted experiment rows, matching evidence digests, predetermined thresholds, zero regressions, and complete case success. Approval, promotion, and rollback retain explicit human gates.

The repository includes two verified regression scenarios:

- a recovered skill improves a later canonical-runtime task and is retained;
- a sandbox-valid skill fails the later real task and is removed through rollback.

This verifies the engineering path in isolated temporary runtimes. It does **not** yet prove long-term benefit on the owner's Windows workstation.

## Development verification

```bash
python -m pip install -e ".[dev]"
python source/scripts/verify_packaging_layout.py
python -m pytest source/tests -q
python source/scripts/verify_evolution_target_001.py
python -m ruff check source/src source/tests source/scripts
python -m mypy source/src/wls source/tests source/scripts --ignore-missing-imports
python -m bandit -q --ini .bandit -r source/src/wls source/scripts
python -m build .
```

The ET001 verification record is stored at:

```text
source/verification/EVOLUTION_TARGET_001_LOCAL_20260624.json
```

## Initialize and inspect

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
wls --config CONFIG growth-reuse GROWTH_CYCLE_ID --goal "later real task" --criterion "measurable success"
wls --config CONFIG growth-rollback GROWTH_CYCLE_ID --actor OWNER --authorization-reference REF --human-approved
wls --config CONFIG growth-status
```

## Remaining proof boundary

The next accepted proof is a real owner-authorized run on the intended Windows host: reproduce a genuine failure, approve and promote the resulting skill, reuse it on a later non-synthetic task, record benefit and regressions, and retain or roll back from evidence. Until that run exists, WLS may claim a **verified bounded growth-cycle implementation**, not verified long-term self-improvement or a complete software life-form.

## EVOLUTION-TARGET-002: bounded local cognition

The default standalone planner is now `cognitive`. It performs local evidence-bound hypothesis competition, records alternatives and a memory-free counterfactual, creates explicit action predictions, resolves them from real tool outcomes, and calibrates causal confidence in the existing temporal world model. External OpenAI-compatible planners are optional and fall back to this local cognition path when unavailable.

```bash
wls --config CONFIG cognition --limit 20
```

This is a bounded engineering cognition layer, not a claim of AGI, subjective consciousness, or unrestricted autonomous reasoning.
