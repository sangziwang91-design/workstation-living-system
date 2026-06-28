# WLS Side-Line Disposition Manifest (2026-06-24)

## PR #2 (Governance & Adversarial) - d6f3772
| File | Disposition | Note |
|---|---|---|
| AGENTS.md | REIMPLEMENT | Adapt to current canonical contracts |
| source/tests/test_round001_invariants.py | PORT | Valid adversarial coverage |
| source/tests/test_resilience_and_edges.py | PORT | Valid reliability coverage |
| source/tests/test_e2e_scenarios.py | SUPERSEDED | Better covered by ET001-003 |
| source/src/wls/repair.py | REJECT | Unused in current runtime |
| tools/clean_round001_generated.py | REJECT | Artifact cleanup tool not needed in core |

## PR #5 (Survival) - 828cba4
| File | Disposition | Note |
|---|---|---|
| source/src/wls/survival.py | PORT | Core survival logic |
| source/src/wls/survival_plugin.py | REJECT | Integrating natively instead |
| source/tests/test_survival.py | PORT | Critical reliability tests |

## PR #8 (ET004 - Persistent Goals) - e1f6939
| File | Disposition | Note |
|---|---|---|
| source/src/wls/goal_ablation.py | PORT | ET004 component |
| source/src/wls/goal_debt.py | PORT | ET004 component |
| source/src/wls/goal_review.py | PORT | ET004 component |
| source/tests/test_evolution_target_004.py | PORT | Required verifier |
| source/tests/test_goal_decomposition.py | PORT | Atomic transition test |
| source/tests/test_goal_persistence.py | PORT | Continuity test |
| source/tests/test_goal_review.py | PORT | State atomicity test |

## PR #14 (Provider Hub) - 1268478
| File | Disposition | Note |
|---|---|---|
| source/src/wls/provider_hub.py | PORT | Hardened candidate-only hub |
| source/src/wls/provider_secrets.py | PORT | Secret handling |
| source/src/wls/provider_ui.py | PORT | Loopback-only UI |
| source/src/wls/provider_cli.py | PORT | CLI entrypoint |
| source/requirements-provider-hub.txt | PORT | Dependencies |
