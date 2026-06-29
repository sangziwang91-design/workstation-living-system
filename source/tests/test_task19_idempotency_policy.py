from __future__ import annotations

from pathlib import Path

from wls.config import default_config
from wls.runtime import LivingSystem
from wls.schemas import ActionSpec, Plan, RiskLevel, utc_now


def _runtime(home: Path, *, reads: bool = True) -> LivingSystem:
    config = default_config(home)
    config.sensors = []
    config.allow_autonomous_read_actions = reads
    return LivingSystem(config)


def _persist(runtime: LivingSystem, cycle_id: str, action: ActionSpec) -> Plan:
    runtime.db.execute(
        "INSERT INTO cycles(cycle_id,started_at,status) VALUES (?,?,?)",
        (cycle_id, utc_now(), "RUNNING"),
    )
    plan = Plan(rationale="idempotency policy regression", actions=[action])
    runtime._persist_plan_and_ack_events(cycle_id, plan, [])
    return plan


def _noop(*, key: str, reason: str) -> ActionSpec:
    return ActionSpec(
        tool="noop",
        arguments={"reason": reason},
        purpose="idempotency policy regression",
        expected_result="No external change",
        risk=RiskLevel.READ,
        acceptance=["output ok is true"],
        idempotency_key=key,
    )


def test_idempotency_collision_is_rejected_not_reused(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path / "home")
    first = _noop(key="collision-key", reason="first")
    first_plan = _persist(runtime, "cycle-first", first)
    first_outcome = runtime._execute_plan(first_plan)[0]
    assert first_outcome["success"] is True
    assert first_outcome["provenance"] == "EXECUTED_CURRENT_ACTION"

    second = _noop(key="collision-key", reason="different")
    _persist(runtime, "cycle-second", second)
    outcome = runtime._execute_action(second)
    assert outcome["success"] is False
    assert outcome["status"] == "REJECTED"
    assert outcome["provenance"] == "NO_OBSERVABLE_OUTCOME"
    assert "collision" in outcome["reason"]
    row = runtime.db.query_one(
        """
        SELECT status,outcome_provenance,source_action_id
        FROM actions WHERE action_id=?
        """,
        (second.action_id,),
    )
    assert row is not None
    assert row["status"] == "REJECTED"
    assert row["outcome_provenance"] == "NO_OBSERVABLE_OUTCOME"
    assert row["source_action_id"] is None


def test_policy_is_checked_before_exact_result_reuse(tmp_path: Path) -> None:
    home = tmp_path / "home"
    first_runtime = _runtime(home, reads=True)
    first = _noop(key="policy-before-reuse", reason="same")
    first_plan = _persist(first_runtime, "seed-cycle", first)
    assert first_runtime._execute_plan(first_plan)[0]["success"] is True

    restricted = _runtime(home, reads=False)
    second = _noop(key="policy-before-reuse", reason="same")
    _persist(restricted, "restricted-cycle", second)
    outcome = restricted._execute_action(second)
    assert outcome["success"] is False
    assert outcome["status"] == "WAITING_APPROVAL"
    assert outcome["provenance"] == "NO_OBSERVABLE_OUTCOME"
    assert outcome.get("reused") is not True


def test_exact_reuse_persists_source_and_provenance(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path / "home")
    first = _noop(key="exact-reuse", reason="same")
    first_plan = _persist(runtime, "first-cycle", first)
    runtime._execute_plan(first_plan)

    second = _noop(key="exact-reuse", reason="same")
    second_plan = _persist(runtime, "second-cycle", second)
    outcome = runtime._execute_plan(second_plan)[0]
    assert outcome["success"] is True
    assert outcome["provenance"] == "REUSED_PRIOR_RESULT"
    assert outcome["source_action_id"] == first.action_id
    row = runtime.db.query_one(
        """
        SELECT outcome_provenance,source_action_id,provenance_evidence_id
        FROM actions WHERE action_id=?
        """,
        (second.action_id,),
    )
    assert row is not None
    assert row["outcome_provenance"] == "REUSED_PRIOR_RESULT"
    assert row["source_action_id"] == first.action_id
    assert row["provenance_evidence_id"]
