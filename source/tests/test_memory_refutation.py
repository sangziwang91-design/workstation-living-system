from __future__ import annotations

from wls.memory_ablation import REFUTED_MEMORY_ID, run_refutation_probe


def test_repeated_counterevidence_refutes_memory_and_restart_suppresses_it(tmp_path) -> None:
    result = run_refutation_probe(tmp_path)
    assert result["passed"] is True
    assert result["state_after_first"]["validity_state"] == "WEAKENED"
    assert result["state_after_second"]["validity_state"] == "REFUTED"
    assert result["state_after_restart"]["validity_state"] == "REFUTED"
    assert result["suppression_preserved"] is True
    assert REFUTED_MEMORY_ID in result["restart_decision"]["suppressed_memory_ids"]
    assert result["restart_decision"]["task_completed"] is True
