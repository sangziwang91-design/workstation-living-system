from __future__ import annotations

from pathlib import Path

import pytest

from wls.config import default_config
from wls.goal_tasks import task_spec_action, validate_task_spec
from wls.runtime import LivingSystem
from wls.schemas import Goal, GoalStatus


def _runtime(home: Path) -> LivingSystem:
    config = default_config(home)
    config.sensors = []
    return LivingSystem(config)


def test_decomposition_is_atomic_when_later_task_is_invalid(
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path / "home")
    parent_id = runtime.add_goal(
        Goal(title="atomic parent", description="negative control")
    )
    with pytest.raises(ValueError, match="unsupported task_spec"):
        runtime.goal_runtime.decomposer.decompose(
            parent_id,
            [
                {
                    "title": "valid first child",
                    "task_spec": {
                        "action": "inspect_path",
                        "path": str(runtime.config.sandbox_path),
                    },
                },
                {
                    "title": "invalid second child",
                    "task_spec": {"action": "shell"},
                    "dependencies": ["previous"],
                },
            ],
        )
    parent = runtime.goals.get(parent_id)
    assert parent is not None
    assert parent.status == GoalStatus.ACTIVE
    assert runtime.goals.children(parent_id, include_archived=True) == []
    row = runtime.db.query_one(
        "SELECT COUNT(*) AS n FROM goal_decompositions WHERE goal_id=?",
        (parent_id,),
    )
    assert row is not None
    assert int(row["n"]) == 0


def test_task_spec_normalization_is_single_canonical_contract() -> None:
    spec = validate_task_spec(
        {
            "action": "read_file",
            "path": "example.txt",
            "max_bytes": "1024",
            "encoding": "utf-8",
        }
    )
    assert spec == {
        "action": "read_file",
        "path": "example.txt",
        "max_bytes": 1024,
        "encoding": "utf-8",
    }
    action = task_spec_action(
        {"goal_id": "goal-1", "title": "inspect", "task_spec": spec}
    )
    assert action["tool"] == "read_file"
    assert action["goal_id"] == "goal-1"
    assert action["arguments"]["max_bytes"] == 1024
    assert action["arguments"]["encoding"] == "utf-8"


def test_goal_integrity_uses_exact_dependency_ids(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path / "home")
    first_id = runtime.add_goal(
        Goal(title="goal", description="first")
    )
    second_id = runtime.add_goal(
        Goal(
            title="dependent",
            description="second",
            dependencies=[first_id],
            status=GoalStatus.WAITING,
        )
    )
    ok, details = runtime.goal_runtime.integrity()
    assert ok, details
    runtime.db.execute(
        "UPDATE goals SET dependencies_json=? WHERE goal_id=?",
        (f'["{second_id}"]', second_id),
    )
    ok, details = runtime.goal_runtime.integrity()
    assert ok is False
    assert details["dependency_self_reference"] == 1
