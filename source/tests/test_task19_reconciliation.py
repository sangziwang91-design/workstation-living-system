from __future__ import annotations

from pathlib import Path

import pytest

from wls.config import default_config
from wls.runtime import LivingSystem
from wls.schemas import ActionSpec, Plan


def _unknown(tmp_path: Path) -> tuple[LivingSystem, ActionSpec]:
    config = default_config(tmp_path / "home")
    config.sensors = []
    runtime = LivingSystem(config)
    action = ActionSpec(
        tool="noop",
        arguments={"reason": "reconcile"},
        purpose="reconciliation regression",
        expected_result="owner decision",
    )
    runtime.db.execute(
        "INSERT INTO cycles(cycle_id,started_at,status) VALUES (?,?,?)",
        ("cycle-r", "2026-06-29T00:00:00+00:00", "RUNNING"),
    )
    runtime._persist_plan_and_ack_events(
        "cycle-r", Plan(rationale="reconcile", actions=[action]), []
    )
    runtime.db.execute(
        "UPDATE actions SET status='UNKNOWN_SIDE_EFFECT' WHERE action_id=?",
        (action.action_id,),
    )
    return runtime, action


def _evidence() -> dict[str, str]:
    return {
        "actor": "owner",
        "authorization_reference": "review-1",
        "finding": "external inspection completed",
    }


def test_reconciliation_requires_owner_evidence(tmp_path: Path) -> None:
    runtime, action = _unknown(tmp_path)
    with pytest.raises(ValueError, match="missing"):
        runtime.resolve_unknown_action(action.action_id, "FAILED", {})


def test_terminal_resolution_binds_provenance(tmp_path: Path) -> None:
    runtime, action = _unknown(tmp_path)
    runtime.resolve_unknown_action(action.action_id, "FAILED", _evidence())
    row = runtime.db.query_one(
        """
        SELECT status,outcome_provenance,provenance_evidence_id
        FROM actions WHERE action_id=?
        """,
        (action.action_id,),
    )
    assert row is not None
    assert row["status"] == "FAILED"
    assert row["outcome_provenance"] == "NO_OBSERVABLE_OUTCOME"
    assert row["provenance_evidence_id"]


def test_retry_safe_requires_new_approval(tmp_path: Path) -> None:
    runtime, action = _unknown(tmp_path)
    runtime.resolve_unknown_action(action.action_id, "RETRY_SAFE", _evidence())
    row = runtime.db.query_one(
        "SELECT status,approval_id FROM actions WHERE action_id=?",
        (action.action_id,),
    )
    assert row is not None
    assert row["status"] == "WAITING_APPROVAL"
    assert row["approval_id"] is None
