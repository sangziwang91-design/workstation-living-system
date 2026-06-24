from __future__ import annotations

from wls.config import default_config
from wls.runtime import LivingSystem
from wls.schemas import Goal, GoalStatus


def test_interruption_creates_debt_and_next_review_recovers_goal(tmp_path) -> None:
    config = default_config(tmp_path / "home")
    config.sensors = []
    runtime = LivingSystem(config)
    goal_id = runtime.add_goal(
        Goal(
            title="Resume me",
            description="A bounded task",
            task_spec={"action": "record_progress"},
        )
    )

    runtime.goals.increment_interruption(goal_id)
    runtime.goals.set_state(
        goal_id,
        status=GoalStatus.BLOCKED,
        blocked_reason="interrupted_by_unrelated_work",
        remaining_work=["resume after interruption"],
    )
    debt_id = runtime.goal_runtime.debts.add(
        goal_id,
        "INTERRUPTION",
        "actionable goal was pre-empted by unrelated work",
        source_ids=["cycle-test"],
    )

    review = runtime.goal_runtime.reviewer.review(
        goal_id,
        cycle_id="cycle-next",
        reason="cycle_prepare",
        source_ids=["cycle-next"],
    )
    recovered = runtime.goals.get(goal_id)

    assert review is not None
    assert review["decided_status"] == GoalStatus.IN_PROGRESS.value
    assert recovered is not None
    assert recovered.recovery_count == 1
    assert runtime.goal_runtime.debts.open_for_goal(goal_id) == []
    row = runtime.db.query_one("SELECT status FROM goal_debts WHERE debt_id=?", (debt_id,))
    assert row is not None and row["status"] == "RESOLVED"
