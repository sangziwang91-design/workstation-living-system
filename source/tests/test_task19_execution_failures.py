from __future__ import annotations

from pathlib import Path

from wls.config import default_config
from wls.runtime import LivingSystem
from wls.schemas import ActionSpec, Plan
from wls.tools import ToolDefinition


def _runtime(tmp_path: Path) -> LivingSystem:
    config = default_config(tmp_path / "home")
    config.sensors = []
    return LivingSystem(config)


def _raise(_: dict[str, object]) -> dict[str, object]:
    raise RuntimeError("adapter exploded")


def _persist(runtime: LivingSystem, action: ActionSpec) -> None:
    runtime.db.execute(
        "INSERT INTO cycles(cycle_id,started_at,status) VALUES (?,?,?)",
        ("cycle-test", "2026-06-29T00:00:00+00:00", "RUNNING"),
    )
    runtime._persist_plan_and_ack_events(
        "cycle-test", Plan(rationale="failure test", actions=[action]), []
    )


def test_read_only_tool_exception_is_observable_failure(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    runtime.tools.register(ToolDefinition("read_probe", _raise, "none"))
    action = ActionSpec(
        tool="read_probe",
        arguments={},
        purpose="exercise read failure",
        expected_result="failure",
    )
    _persist(runtime, action)
    result = runtime._execute_action(action)
    assert result["status"] == "FAILED"
    assert result["provenance"] == "EXECUTED_CURRENT_ACTION"
    row = runtime.db.query_one(
        "SELECT status,outcome_provenance FROM actions WHERE action_id=?",
        (action.action_id,),
    )
    assert row is not None
    assert row["status"] == "FAILED"
    assert row["outcome_provenance"] == "EXECUTED_CURRENT_ACTION"


def test_external_tool_exception_requires_manual_reconciliation(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    runtime.tools.register(ToolDefinition("external_probe", _raise, "external"))
    action = ActionSpec(
        tool="external_probe",
        arguments={},
        purpose="exercise ambiguous external failure",
        expected_result="manual reconciliation",
    )
    _persist(runtime, action)
    result = runtime._execute_action(action)
    assert result["status"] == "UNKNOWN_SIDE_EFFECT"
    assert result["provenance"] == "NO_OBSERVABLE_OUTCOME"
    row = runtime.db.query_one(
        """
        SELECT status,outcome_provenance,provenance_evidence_id
        FROM actions WHERE action_id=?
        """,
        (action.action_id,),
    )
    assert row is not None
    assert row["status"] == "UNKNOWN_SIDE_EFFECT"
    assert row["outcome_provenance"] == "NO_OBSERVABLE_OUTCOME"
    assert row["provenance_evidence_id"]
