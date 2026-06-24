from __future__ import annotations

from wls.config import default_config
from wls.runtime import LivingSystem
from wls.schemas import Goal, GoalStatus, RiskLevel


def test_persistent_goal_state_survives_restart(tmp_path) -> None:
    config = default_config(tmp_path / "home")
    config.sensors = []
    runtime = LivingSystem(config)
    goal = Goal(
        title="Preserve a bounded long-horizon objective",
        description="Keep structured state across restart.",
        status=GoalStatus.IN_PROGRESS,
        progress=0.4,
        rationale="Owner-authorized continuity test.",
        origin="test",
        task_spec={"action": "record_progress"},
        dependencies=["prior-goal"],
        progress_evidence=["evidence-1"],
        remaining_work=["finish the bounded task"],
        risk=RiskLevel.READ,
        interruption_count=2,
        recovery_count=1,
    )
    runtime.add_goal(goal)

    restarted = LivingSystem(config)
    restored = restarted.goals.get(goal.goal_id)

    assert restored is not None
    assert restored.status == GoalStatus.IN_PROGRESS
    assert restored.progress == 0.4
    assert restored.task_spec == {"action": "record_progress"}
    assert restored.dependencies == ["prior-goal"]
    assert restored.progress_evidence == ["evidence-1"]
    assert restored.interruption_count == 2
    assert restored.recovery_count == 1
