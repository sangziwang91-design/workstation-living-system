from __future__ import annotations

from pathlib import Path
import json

import pytest

from wls.cycle_journal import CycleJournal
from wls.db import Database
from wls.evidence import EvidenceLedger


def _journal(tmp_path: Path) -> tuple[Database, CycleJournal]:
    db = Database(tmp_path / "state.db")
    ledger = EvidenceLedger(db, tmp_path / "evidence.key")
    return db, CycleJournal(db, ledger)


def _cycle(db: Database, cycle_id: str, status: str = "RUNNING") -> None:
    db.execute(
        "INSERT INTO cycles(cycle_id,started_at,status) VALUES (?,?,?)",
        (cycle_id, "2026-06-29T00:00:00+00:00", status),
    )


def _plan(db: Database, cycle_id: str, plan_id: str) -> None:
    db.execute(
        """
        INSERT INTO plans(plan_id,cycle_id,plan_json,status,created_at)
        VALUES (?,?,?,?,?)
        """,
        (
            plan_id,
            cycle_id,
            json.dumps({"plan_id": plan_id, "rationale": "test", "actions": []}),
            "PLANNED",
            "2026-06-29T00:00:01+00:00",
        ),
    )


def test_checkpoint_is_idempotent_but_immutable(tmp_path: Path) -> None:
    db, journal = _journal(tmp_path)
    _cycle(db, "cycle-1")
    first = journal.record("cycle-1", "PLAN_PERSISTED", {"plan_id": "plan-1"})
    second = journal.record("cycle-1", "PLAN_PERSISTED", {"plan_id": "plan-1"})
    assert first == second
    with pytest.raises(ValueError, match="immutable checkpoint"):
        journal.record("cycle-1", "PLAN_PERSISTED", {"plan_id": "different"})


def test_checkpoint_phase_cannot_move_backward(tmp_path: Path) -> None:
    db, journal = _journal(tmp_path)
    _cycle(db, "cycle-1")
    journal.record("cycle-1", "ACTIONS_TERMINAL", {"done": True})
    with pytest.raises(ValueError, match="regression"):
        journal.record("cycle-1", "PLAN_PERSISTED", {"plan_id": "late"})


def test_interrupted_cycles_are_classified_by_durable_plan(tmp_path: Path) -> None:
    db, journal = _journal(tmp_path)
    _cycle(db, "before-plan")
    _cycle(db, "after-plan")
    _plan(db, "after-plan", "plan-1")
    result = journal.classify_interrupted()
    assert result == {
        "failed_before_plan": ["before-plan"],
        "pending_recovery": ["after-plan"],
    }
    before = db.query_one("SELECT status FROM cycles WHERE cycle_id='before-plan'")
    after = db.query_one("SELECT status FROM cycles WHERE cycle_id='after-plan'")
    assert before is not None and before["status"] == "FAILED"
    assert after is not None and after["status"] == "RECOVERY_PENDING"
    assert journal.pending()[0]["plan_id"] == "plan-1"


def test_recovery_resolution_is_persistent_and_idempotent(tmp_path: Path) -> None:
    db, journal = _journal(tmp_path)
    _cycle(db, "cycle-1")
    _plan(db, "cycle-1", "plan-1")
    journal.record("cycle-1", "PLAN_PERSISTED", {"plan_id": "plan-1"})
    journal.classify_interrupted()
    first = journal.resolve("cycle-1", [], {"cognition": None})
    second = journal.resolve("cycle-1", [], {"cognition": None})
    assert first["status"] == "RESOLVED"
    assert second["status"] == "RESOLVED"
    row = db.query_one("SELECT status FROM cycles WHERE cycle_id='cycle-1'")
    assert row is not None and row["status"] == "RECOVERED"
    ok, details = journal.integrity()
    assert ok, details
