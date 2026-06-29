from __future__ import annotations

from pathlib import Path

from wls.config import default_config
from wls.runtime import LivingSystem
from wls.schemas import ActionSpec, Goal, Plan, RiskLevel, utc_now


def test_goal_requires_all_fresh_actions_to_succeed(tmp_path: Path) -> None:
    config = default_config(tmp_path / "home")
    config.sensors = []
    runtime = LivingSystem(config)
    goal_id = runtime.add_goal(Goal(title="aggregate", description="two actions"))
    runtime.db.execute(
        "INSERT INTO cycles(cycle_id,started_at,status) VALUES (?,?,?)",
        ("aggregate-cycle", utc_now(), "RUNNING"),
    )
    actions = [
        ActionSpec(
            tool="noop",
            arguments={"reason": str(index)},
            purpose=f"step {index}",
            expected_result="No external change",
            risk=RiskLevel.READ,
            goal_id=goal_id,
            acceptance=["output ok is true"],
        )
        for index in range(2)
    ]
    plan = Plan(rationale="aggregate", actions=actions)
    runtime._persist_plan_and_ack_events("aggregate-cycle", plan, [])
    outcomes = [
        {
            "action_id": actions[0].action_id,
            "success": True,
            "status": "SUCCEEDED",
            "provenance": "EXECUTED_CURRENT_ACTION",
        },
        {
            "action_id": actions[1].action_id,
            "success": False,
            "status": "FAILED",
            "error": "negative control",
            "provenance": "EXECUTED_CURRENT_ACTION",
        },
    ]
    runtime.goal_runtime.resolve_cycle(
        "aggregate-cycle",
        selected_goal_ids=[goal_id],
        outcomes=outcomes,
        attribution=None,
    )
    goal = runtime.goals.get(goal_id)
    assert goal is not None
    assert goal.status.value == "BLOCKED"
    assert goal.progress < 1.0


def test_missing_or_reused_action_cannot_complete_goal(tmp_path: Path) -> None:
    config = default_config(tmp_path / "home")
    config.sensors = []
    runtime = LivingSystem(config)
    goal_id = runtime.add_goal(Goal(title="nonfresh", description="negative"))
    runtime.goal_runtime.resolve_cycle(
        "missing-cycle",
        selected_goal_ids=[goal_id],
        outcomes=[
            {
                "action_id": "old",
                "success": True,
                "status": "SUCCEEDED",
                "provenance": "REUSED_PRIOR_RESULT",
            }
        ],
        attribution=None,
    )
    goal = runtime.goals.get(goal_id)
    assert goal is not None
    assert goal.progress == 0.0
    assert goal.status.value != "COMPLETED"
