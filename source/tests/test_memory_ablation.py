from __future__ import annotations

from wls.memory_ablation import ADVANTAGE_MEMORY_ID, run_controlled_ablation


def test_memory_enabled_runtime_beats_frozen_disabled_baseline_and_restarts(tmp_path) -> None:
    result = run_controlled_ablation(tmp_path)
    assert result["passed"] is True
    assert result["event_stream_equivalent"] is True
    assert result["memory_enabled"]["success_rate"] > result["memory_disabled_baseline"]["success_rate"]
    assert result["memory_enabled"]["failure_recurrence"] < result["memory_disabled_baseline"]["failure_recurrence"]
    assert result["memory_enabled"]["wrong_tool_rate"] < result["memory_disabled_baseline"]["wrong_tool_rate"]
    assert result["memory_enabled"]["task_completion"] > result["memory_disabled_baseline"]["task_completion"]
    assert ADVANTAGE_MEMORY_ID in result["attributed_memory_ids"]
    assert result["restart_continuity"]["advantage_preserved"] is True
    assert result["regressions"] == 0
