from __future__ import annotations

from wls.memory_ablation import ADVANTAGE_MEMORY_ID, run_evolution_target_003


def test_evolution_target_003_complete_local_probe(tmp_path) -> None:
    report = run_evolution_target_003(tmp_path)
    assert report["passed"] is True
    assert report["regressions"] == 0
    assert ADVANTAGE_MEMORY_ID in report["attributed_memory_ids"]
    changed = [item for item in report["memory_enabled"]["results"] if item["memory_changed_decision"]]
    assert changed
    attribution = changed[0]
    assert attribution["selected_memory_ids"]
    assert attribution["counterfactual_without_memory"]["key"]
    assert attribution["memory_delta"] > 0
    assert attribution["causal_reason"]
    assert report["refuted_memory_ids"]
    assert report["restart_continuity"]["advantage_preserved"] is True
