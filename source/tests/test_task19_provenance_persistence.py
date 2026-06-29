from __future__ import annotations

from pathlib import Path

from wls.config import default_config
from wls.runtime import LivingSystem
from wls.schemas import ActionSpec, Plan, RiskLevel, utc_now


def _runtime(home: Path) -> LivingSystem:
    config = default_config(home)
    config.sensors = []
    return LivingSystem(config)


def _persist(runtime: LivingSystem, cycle_id: str, action: ActionSpec) -> Plan:
    runtime.db.execute(
        "INSERT INTO cycles(cycle_id,started_at,status) VALUES (?,?,?)",
        (cycle_id, utc_now(), "RUNNING"),
    )
    plan = Plan(rationale="provenance persistence test", actions=[action])
    runtime._persist_plan_and_ack_events(cycle_id, plan, [])
    return plan


def _noop(*, key: str, reason: str) -> ActionSpec:
    return ActionSpec(
        tool="noop",
        arguments={"reason": reason},
        purpose="idempotency contract",
        expected_result="No external change",
        risk=RiskLevel.READ,
        acceptance=["output ok is true"],
        idempotency_key=key,
    )


def test_exact_reuse_persists_source_and_provenance(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path / "home")
    first = _noop(key="exact-reuse", reason="same")
    runtime._execute_plan(_persist(runtime, "cycle-first", first))

    second = _noop(key="exact-reuse", reason="same")
    outcome = runtime._execute_plan(_persist(runtime, "cycle-second", second))[0]
    assert outcome["provenance"] == "REUSED_PRIOR_RESULT"
    assert outcome["source_action_id"] == first.action_id
    row = runtime.db.query_one(
        """
        SELECT outcome_provenance,source_action_id,provenance_evidence_id,result_json
        FROM actions WHERE action_id=?
        """,
        (second.action_id,),
    )
    assert row is not None
    assert row["outcome_provenance"] == "REUSED_PRIOR_RESULT"
    assert row["source_action_id"] == first.action_id
    assert row["provenance_evidence_id"]
    assert "REUSED_PRIOR_RESULT" in str(row["result_json"])


def test_idempotency_collision_is_rejected_not_reused(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path / "home")
    first = _noop(key="collision-key", reason="first")
    runtime._execute_plan(_persist(runtime, "cycle-first", first))

    second = _noop(key="collision-key", reason="different")
    _persist(runtime, "cycle-second", second)
    outcome = runtime._execute_action(second)
    assert outcome["success"] is False
    assert outcome["status"] == "REJECTED"
    assert outcome["provenance"] == "NO_OBSERVABLE_OUTCOME"
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


def test_provenance_columns_survive_runtime_restart(tmp_path: Path) -> None:
    home = tmp_path / "home"
    runtime = _runtime(home)
    action = _noop(key="restart-query", reason="persist")
    runtime._execute_plan(_persist(runtime, "cycle-one", action))

    restarted = _runtime(home)
    row = restarted.db.query_one(
        """
        SELECT outcome_provenance,source_action_id,provenance_evidence_id
        FROM actions WHERE action_id=?
        """,
        (action.action_id,),
    )
    assert row is not None
    assert row["outcome_provenance"] == "EXECUTED_CURRENT_ACTION"
    assert row["source_action_id"] is None
    assert row["provenance_evidence_id"]
