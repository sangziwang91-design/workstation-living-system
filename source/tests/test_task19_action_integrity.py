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
    plan = Plan(rationale="action provenance integrity", actions=[action])
    runtime._persist_plan_and_ack_events(cycle_id, plan, [])
    return plan


def _action(key: str) -> ActionSpec:
    return ActionSpec(
        tool="noop",
        arguments={"reason": key},
        purpose="action provenance integrity",
        expected_result="No external change",
        risk=RiskLevel.READ,
        acceptance=["output ok is true"],
        idempotency_key=key,
    )


def test_legacy_terminal_action_is_migrated_without_fresh_claim(tmp_path: Path) -> None:
    home = tmp_path / "home"
    runtime = _runtime(home)
    action = _action("legacy-row")
    plan = _persist(runtime, "legacy-cycle", action)
    runtime._execute_plan(plan)
    runtime.db.execute(
        """
        UPDATE actions SET outcome_provenance=NULL,
            source_action_id=NULL,provenance_evidence_id=NULL
        WHERE action_id=?
        """,
        (action.action_id,),
    )

    restarted = _runtime(home)
    row = restarted.db.query_one(
        """
        SELECT outcome_provenance,source_action_id,provenance_evidence_id
        FROM actions WHERE action_id=?
        """,
        (action.action_id,),
    )
    assert row is not None
    assert row["outcome_provenance"] == "LEGACY_UNATTRIBUTED"
    assert row["source_action_id"] is None
    assert row["provenance_evidence_id"]
    integrity = restarted.verify_integrity(full=True)
    assert integrity["action_provenance"]["ok"] is True


def test_integrity_rejects_reuse_without_source_action(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path / "home")
    first = _action("reuse-source")
    runtime._execute_plan(_persist(runtime, "first-cycle", first))
    second = _action("reuse-source")
    runtime._execute_plan(_persist(runtime, "second-cycle", second))
    runtime.db.execute(
        "UPDATE actions SET source_action_id=NULL WHERE action_id=?",
        (second.action_id,),
    )
    integrity = runtime.verify_integrity(full=True)
    assert integrity["ok"] is False
    assert integrity["action_provenance"]["counts"]["reused_without_source"] == 1


def test_integrity_rejects_unknown_provenance_value(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path / "home")
    action = _action("unknown-provenance")
    runtime._execute_plan(_persist(runtime, "unknown-cycle", action))
    runtime.db.execute(
        "UPDATE actions SET outcome_provenance='MADE_UP' WHERE action_id=?",
        (action.action_id,),
    )
    integrity = runtime.verify_integrity(full=True)
    assert integrity["ok"] is False
    assert integrity["action_provenance"]["counts"]["invalid_provenance"] == 1
