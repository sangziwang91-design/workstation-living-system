from __future__ import annotations

from wls.config import default_config
from wls.runtime import LivingSystem
from wls.schemas import Goal, GoalStatus


def test_goal_decomposition_is_bounded_and_dependency_ordered(tmp_path) -> None:
    config = default_config(tmp_path / "home")
    config.sensors = []
    runtime = LivingSystem(config)
    parent_id = runtime.add_goal(
        Goal(
            title="Complete a two-step repository task",
            description="Use the existing canonical goal store.",
            priority=0.7,
        )
    )

    result = runtime.goal_runtime.decomposer.decompose(
        parent_id,
        [
            {"title": "Inspect", "task_spec": {"action": "record_progress"}},
            {
                "title": "Verify",
                "task_spec": {"action": "record_progress"},
                "dependencies": ["previous"],
            },
        ],
    )

    children = runtime.goals.children(parent_id)
    assert result["subgoal_ids"] == [child.goal_id for child in children]
    assert children[0].dependencies == []
    assert children[1].dependencies == [children[0].goal_id]
    assert runtime.goals.actionable(limit=10)[0].goal_id == children[0].goal_id
    assert runtime.goals.get(parent_id).status == GoalStatus.DECOMPOSED  # type: ignore[union-attr]
