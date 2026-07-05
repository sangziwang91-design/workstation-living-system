from __future__ import annotations

from pathlib import Path
import sqlite3

from wls.db import Database
from wls.db import SCHEMA_VERSION
from wls.evidence import EvidenceLedger
from wls.schemas import Goal, RiskLevel
from wls.stores import GoalStore


def test_goal_store_persists_owner_ui_metadata(tmp_path: Path) -> None:
    database = Database(tmp_path / "state" / "wls.db")
    ledger = EvidenceLedger(database, tmp_path / "secrets" / "evidence.key")
    goals = GoalStore(database, ledger)

    goal = Goal(
        title="Owner UI task",
        description="Created through Owner Console",
        rationale="Owner requested a read-only check",
        origin="owner",
        task_spec={"kind": "task", "created_via": "wls-ui"},
        risk=RiskLevel.READ,
    )
    goal_id = goals.add(goal)

    restored = goals.get(goal_id)
    assert restored is not None
    assert restored.rationale == "Owner requested a read-only check"
    assert restored.origin == "owner"
    assert restored.task_spec == {"kind": "task", "created_via": "wls-ui"}
    assert restored.risk is RiskLevel.READ


def test_database_adds_goal_metadata_columns_to_existing_state(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state" / "legacy.sqlite3"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            """
            CREATE TABLE goals (
                goal_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                priority REAL NOT NULL,
                success_criteria_json TEXT NOT NULL,
                source TEXT NOT NULL,
                autonomous INTEGER NOT NULL,
                parent_goal_id TEXT,
                deadline TEXT,
                status TEXT NOT NULL,
                progress REAL NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.commit()
    finally:
        connection.close()

    database = Database(db_path)

    columns = {
        str(row["name"])
        for row in database.query_all("PRAGMA table_info(goals)")
    }
    version = database.query_one(
        "SELECT version FROM schema_migrations WHERE version=?",
        (SCHEMA_VERSION,),
    )
    assert {"rationale", "origin", "task_spec_json", "risk"} <= columns
    assert version is not None
