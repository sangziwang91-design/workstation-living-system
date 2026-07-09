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

WLS v0.9.0.dev2 has 86 source modules, 41 test files, and P01-P89 architecture validation all passing. The next accepted proof is a real owner-authorized longitudinal run on the intended Windows host.

## Module inventory (86 modules)

**Core runtime:** `runtime.py`, `config.py`, `schemas.py`, `db.py`, `stores.py`, `tools.py`, `planner.py`, `policy.py`, `approval.py`, `evidence.py`, `skills.py`, `growth_cycle.py`, `learning.py`, `cognition.py`, `world.py`, `temporal_world.py`, `drives.py`, `attention.py`, `autonomy.py`, `goal_runtime.py`, `self_model.py`, `relationships.py`, `sleep.py`, `cli.py`, `_version.py`

**Agentic execution:** `agentic_harness.py`, `agentic_mailbox.py`, `task_admission.py`, `task_classifier.py`, `task_graph.py`, `context_manifest.py`, `execution_trace.py`, `loop_control.py`, `worker_registry.py`, `failure_attribution.py`, `experiment_decision.py`, `experiments.py`, `scheduler.py`

**Security & governance:** `security.py`, `acceptance.py`, `evidence_gates.py`, `architecture_validation.py`, `lease.py`, `bounded_recovery.py`, `read_only_organs.py`

**Memory & learning:** `memory_projection.py`, `memory_index.py`, `memory_attribution.py`, `memory_ablation.py`, `external_memory.py`, `decision_memory.py`, `compaction.py`, `anti_repeat.py`

**Review & quality:** `reviewer.py`, `benchmark.py`, `evaluator.py`, `qos_router.py`, `fault_injection.py`, `graph_recovery.py`, `longitudinal.py`, `skill_compiler.py`

**Adapters:** `coding_adapter.py`, `coding_workers.py`, `browser_adapter.py`, `computer_adapter.py`, `mcp_adapter.py`, `a2a_adapter.py`, `channel_gateway.py`, `wechat_adapter.py`, `sandbox_adapter.py`, `multimodal.py`, `provider_router.py`

**UI:** `ui_projection.py`, `ui_server.py`, `ui_static/`

**Evolution:** `offspring.py`, `merge_node.py`, `result_promotion.py`, `repo_explorer.py`, `workbench.py`, `capabilities.py`, `adaptive_growth.py`

**Tests:** 41 test files covering unit + 6 integration pipelines

## Architecture validation

P01-P89 all pass on installed instance (v0.9.0.dev2). See `source/verification/` for records.

```bash
python source/scripts/run_architecture_validation.py --output acceptance/p01_p89.json
```

## EVOLUTION-TARGET-002: bounded local cognition

The default standalone planner is now `cognitive`. It performs local evidence-bound hypothesis competition, records alternatives and a memory-free counterfactual, creates explicit action predictions, resolves them from real tool outcomes, and calibrates causal confidence in the existing temporal world model. External OpenAI-compatible planners are optional and fall back to this local cognition path when unavailable.

```bash
wls --config CONFIG cognition --limit 20
```

This is a bounded engineering cognition layer, not a claim of AGI, subjective consciousness, or unrestricted autonomous reasoning.
