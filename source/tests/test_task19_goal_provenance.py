from __future__ import annotations

from pathlib import Path
import json

import pytest

from wls.config import default_config
from wls.runtime import LivingSystem
from wls.schemas import ActionSpec, Goal, GoalStatus, Plan, RiskLevel, utc_now


def _runtime(home: Path) -> LivingSystem:
    config = default_config(home)
    config.sensors = []
    config.max_actions_per_cycle = 1
    config.sleep_after_idle_cycles = 100
    return LivingSystem(config)


def _persist_manual_plan(runtime: LivingSystem, cycle_id: str, action: ActionSpec) -> Plan:
    runtime.db.execute(
        "INSERT INTO cycles(cycle_id,started_at,status) VALUES (?,?,?)",
        (cycle_id, utc_now(), "RUNNING"),
    )
    plan = Plan(rationale="focused provenance test", actions=[action])
    runtime._persist_plan_and_ack_events(cycle_id, plan, [])
    return plan


def test_task_spec_executes_through_canonical_planner_and_policy(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path / "home")
    target = runtime.config.sandbox_path / "target"
    target.mkdir(parents=True)
    (target / "evidence.txt").write_text("ok", encoding="utf-8")
    parent_id = runtime.add_goal(
        Goal(
            title="Generic parent",
            description="No actionable path appears in prose.",
            priority=0.9,
        )
    )
    decomposition = runtime.goal_runtime.decomposer.decompose(
        parent_id,
        [
            {
                "title": "Generic child",
                "description": "The task specification is the only action source.",
                "task_spec": {"action": "inspect_path", "path": str(target)},
            }
        ],
    )
    child_id = decomposition["subgoal_ids"][0]
    result = runtime.run_cycle()
    row = runtime.db.query_one(
        "SELECT goal_id,tool,status,arguments_json FROM actions WHERE goal_id=? ORDER BY rowid DESC LIMIT 1",
        (child_id,),
    )
    assert row is not None
    assert row["goal_id"] == child_id
    assert row["tool"] == "list_directory"
    assert row["status"] == "SUCCEEDED"
    assert json.loads(row["arguments_json"])["path"] == str(target)
    assert any(
        item.get("provenance") == "EXECUTED_CURRENT_ACTION"
        for item in result["outcomes"]
    )
    child = runtime.goals.get(child_id)
    assert child is not None
    assert child.status in {GoalStatus.COMPLETED, GoalStatus.SUCCEEDED, GoalStatus.ARCHIVED}


def test_invalid_task_spec_is_rejected_before_persistence(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path / "home")
    parent_id = runtime.add_goal(Goal(title="negative parent", description="negative"))
    with pytest.raises(ValueError, match="unsupported task_spec"):
        runtime.goal_runtime.decomposer.decompose(
            parent_id,
            [{"title": "bad child", "task_spec": {"action": "shell", "command": "whoami"}}],
        )
    assert runtime.goals.children(parent_id, include_archived=True) == []


def test_goal_counterfactual_is_write_free_rank_not_constant_placeholder(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path / "home")
    runtime.add_goal(
        Goal(
            title="Record bounded progress",
            description="Use a task specification rather than prose inference.",
            task_spec={"action": "record_progress", "reason": "counterfactual test"},
            priority=0.8,
        )
    )
    runtime.run_cycle()
    row = runtime.db.query_one(
        "SELECT counterfactual_json,selected_key,goal_influenced_decision FROM goal_attributions ORDER BY created_at DESC LIMIT 1"
    )
    assert row is not None
    counterfactual = json.loads(row["counterfactual_json"])
    assert counterfactual["method"] == "WRITE_FREE_BOUNDED_COGNITIVE_RANK"
    assert counterfactual["key"] != "goal-free"
    assert len(counterfactual["action_digest"]) == 64
    assert len(counterfactual["context_digest"]) == 64


def test_outcome_provenance_distinguishes_execution_reuse_and_recovery(tmp_path: Path) -> None:
    home = tmp_path / "home"
    runtime = _runtime(home)
    first = ActionSpec(
        tool="noop",
        arguments={"reason": "provenance"},
        purpose="provenance test",
        expected_result="No external change",
        risk=RiskLevel.READ,
        acceptance=["output ok is true"],
        idempotency_key="task19-provenance-key",
    )
    first_plan = _persist_manual_plan(runtime, "cycle-first", first)
    first_outcome = runtime._execute_action(first)
    runtime._refresh_plan_status(first_plan.plan_id)
    assert first_outcome["provenance"] == "EXECUTED_CURRENT_ACTION"
    assert first_outcome["source_action_id"] is None

    second = ActionSpec(
        tool="noop",
        arguments={"reason": "provenance"},
        purpose="provenance test",
        expected_result="No external change",
        risk=RiskLevel.READ,
        acceptance=["output ok is true"],
        idempotency_key="task19-provenance-key",
    )
    second_plan = _persist_manual_plan(runtime, "cycle-second", second)
    second_outcome = runtime._execute_action(second)
    runtime._refresh_plan_status(second_plan.plan_id)
    assert second_outcome["provenance"] == "REUSED_PRIOR_RESULT"
    assert second_outcome["source_action_id"] == first.action_id
    assert second_outcome["provenance_evidence_id"]

    recovered = ActionSpec(
        tool="noop",
        arguments={"reason": "recovery"},
        purpose="durable recovery test",
        expected_result="No external change",
        risk=RiskLevel.READ,
        acceptance=["output ok is true"],
        idempotency_key="task19-recovery-key",
    )
    _persist_manual_plan(runtime, "cycle-recovery", recovered)
    restarted = _runtime(home)
    recovered_outcomes = restarted._resume_durable_actions()
    matching = [item for item in recovered_outcomes if item["action_id"] == recovered.action_id]
    assert len(matching) == 1
    assert matching[0]["provenance"] == "RECOVERED_DURABLE_ACTION"


def test_reused_result_does_not_complete_goal_as_fresh_intervention(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path / "home")
    goal_id = runtime.add_goal(Goal(title="Do not fake progress", description="negative"))
    reused = {
        "action_id": "reused-action",
        "success": True,
        "status": "SUCCEEDED",
        "provenance": "REUSED_PRIOR_RESULT",
        "source_action_id": "old-action",
    }
    runtime.goal_runtime.resolve_cycle(
        "synthetic-cycle",
        selected_goal_ids=[goal_id],
        outcomes=[reused],
        attribution=None,
    )
    goal = runtime.goals.get(goal_id)
    assert goal is not None
    assert goal.status not in {GoalStatus.COMPLETED, GoalStatus.SUCCEEDED, GoalStatus.ARCHIVED}
    assert goal.progress < 1.0
