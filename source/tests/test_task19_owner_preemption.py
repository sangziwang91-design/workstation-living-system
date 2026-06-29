from __future__ import annotations

import json
from pathlib import Path

from wls.config import default_config
from wls.runtime import LivingSystem
from wls.schemas import EvidenceKind, Goal, GoalStatus, Observation, VerificationStatus


def test_owner_request_preempts_then_durable_goal_resumes(tmp_path: Path) -> None:
    config = default_config(tmp_path / "home")
    config.sensors = []
    config.max_actions_per_cycle = 1
    config.sleep_after_idle_cycles = 100
    runtime = LivingSystem(config)
    try:

        goal_target = runtime.config.sandbox_path / "goal-target"
        owner_target = runtime.config.sandbox_path / "owner-target"
        for path in (goal_target, owner_target):
            path.mkdir(parents=True, exist_ok=True)
            (path / "evidence.txt").write_text(path.name, encoding="utf-8")

        parent_id = runtime.add_goal(
            Goal(title="Durable background objective", description="Must survive preemption")
        )
        decomposition = runtime.goal_runtime.decomposer.decompose(
            parent_id,
            [
                {
                    "title": "Inspect durable target",
                    "task_spec": {"action": "inspect_path", "path": str(goal_target)},
                }
            ],
        )
        child_id = decomposition["subgoal_ids"][0]

        observation = Observation(
            source="owner",
            kind="external_event",
            subject="owner-request",
            predicate="request",
            value={"action": "inspect_path", "path": str(owner_target)},
            confidence=1.0,
            evidence_kind=EvidenceKind.DIRECT,
            verification=VerificationStatus.VERIFIED,
        )
        runtime.events.add_observation(observation)
        runtime.world.assimilate(observation)

        first = runtime.run_cycle()
        first_action = runtime.db.query_one(
            "SELECT goal_id,arguments_json,status FROM actions ORDER BY rowid DESC LIMIT 1"
        )
        assert first_action is not None
        assert first_action["goal_id"] is None
        assert json.loads(first_action["arguments_json"])["path"] == str(owner_target)
        assert first_action["status"] == "SUCCEEDED"
        interrupted = runtime.goals.get(child_id)
        assert interrupted is not None
        assert interrupted.status == GoalStatus.BLOCKED
        assert interrupted.interruption_count == 1
        assert any(
            item.get("provenance") == "EXECUTED_CURRENT_ACTION"
            for item in first["outcomes"]
        )

        second = runtime.run_cycle()
        second_action = runtime.db.query_one(
            "SELECT goal_id,arguments_json,status FROM actions ORDER BY rowid DESC LIMIT 1"
        )
        assert second_action is not None
        assert second_action["goal_id"] == child_id
        assert json.loads(second_action["arguments_json"])["path"] == str(goal_target)
        assert second_action["status"] == "SUCCEEDED"
        resumed = runtime.goals.get(child_id)
        assert resumed is not None
        assert resumed.status in {
            GoalStatus.COMPLETED,
            GoalStatus.SUCCEEDED,
            GoalStatus.ARCHIVED,
        }
        assert resumed.recovery_count >= 1
        assert any(
            item.get("provenance") == "EXECUTED_CURRENT_ACTION"
            for item in second["outcomes"]
        )
    finally:
        runtime.close()
