from __future__ import annotations

from pathlib import Path
import json
import subprocess
import sys


def _report(tmp_path: Path) -> dict:
    output = tmp_path / "et004.json"
    script = Path(__file__).parents[1] / "scripts" / "verify_evolution_target_004.py"
    subprocess.run(
        [sys.executable, str(script), "--output", str(output), "--quiet"],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(output.read_text(encoding="utf-8"))


def test_goal_enabled_arm_outperforms_goal_disabled_frozen_baseline(tmp_path) -> None:
    report = _report(tmp_path)

    enabled = report["goal_enabled"]
    disabled = report["goal_disabled_baseline"]
    assert report["event_stream_equivalent"] is True
    assert enabled["task_completion"] is True
    assert enabled["completed_children"] == 4
    assert enabled["interruption_recovery"] is True
    assert disabled["task_completion"] is False
    assert disabled["completed_children"] == 0
    assert report["decision_differences"]["completion_rate_delta"] == 1.0
    assert report["restart_continuity"]["progress_preserved"] is True
