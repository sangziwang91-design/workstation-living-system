from __future__ import annotations

from pathlib import Path

from wls.config import default_config
from wls.runtime import LivingSystem
from wls.schemas import ActionSpec, Goal, Plan, RiskLevel, utc_now


def _persist(runtime: LivingSystem, cycle_id: str, action: ActionSpec) -> Plan:
    runtime.db.execute(
        "INSERT INTO cycles(cycle_id,started_at,status) VALUES (?,?,?)",
        (cycle_id, utc_now(), "RUNNING"),
    )
    plan = Plan(rationale="reuse guard", actions=[action])
    runtime._persist_plan_and_ack_events(cycle_id, plan, [])
    return plan


def _action(key: str, goal_id: str | None = None) -> ActionSpec:
    return ActionSpec(
        tool="noop",
        arguments={"reason": "reuse guard"},
        purpose="reuse guard",
        expected_result="No external change",
        risk=RiskLevel.READ,
        goal_id=goal_id,
        acceptance=["output ok is true"],
        idempotency_key=key,
    )


def test_repeated_historical_reuse_cannot_accumulate_goal_progress(tmp_path: Path) -> None:
    config = default_config(tmp_path / "home")
    config.sensors = []
    runtime = LivingSystem(config)

    seed = _action("same-idempotency-key")
    seed_plan = _persist(runtime, "seed-cycle", seed)
    seed_outcome = runtime._execute_plan(seed_plan)[0]
    assert seed_outcome["provenance"] == "EXECUTED_CURRENT_ACTION"

    goal_id = runtime.add_goal(
        Goal(title="Historical reuse must not complete me", description="negative control")
    )
    for index in range(4):
        reused_action = _action("same-idempotency-key", goal_id)
        plan = _persist(runtime, f"reuse-cycle-{index}", reused_action)
        outcome = runtime._execute_plan(plan)[0]
        assert outcome["provenance"] == "REUSED_PRIOR_RESULT"
        assert outcome["source_action_id"] == seed.action_id

    goal = runtime.goals.get(goal_id)
    assert goal is not None
    assert goal.progress == 0.0
    assert goal.status.value not in {"COMPLETED", "SUCCEEDED", "ARCHIVED"}
