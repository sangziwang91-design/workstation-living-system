from __future__ import annotations

from typing import Any
import json

from .db import Database
from .evidence import EvidenceLedger
from .schemas import new_id, utc_now


class GoalDebtLedger:
    """Durable ledger for unfinished, interrupted, contradicted, or failed goal work."""

    def __init__(self, db: Database, ledger: EvidenceLedger):
        self.db = db
        self.ledger = ledger
        self._ensure_table()

    def _ensure_table(self) -> None:
        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS goal_debts (
                debt_id TEXT PRIMARY KEY,
                goal_id TEXT NOT NULL,
                debt_type TEXT NOT NULL,
                reason TEXT NOT NULL,
                severity REAL NOT NULL,
                source_ids_json TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                resolved_at TEXT,
                resolution TEXT
            )
            """
        )
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_goal_debts_goal_status ON goal_debts(goal_id,status,created_at)"
        )

    def add(
        self,
        goal_id: str,
        debt_type: str,
        reason: str,
        *,
        severity: float = 0.5,
        source_ids: list[str] | None = None,
    ) -> str:
        severity = max(0.0, min(1.0, float(severity)))
        source_ids = list(dict.fromkeys(source_ids or []))
        existing = self.db.query_one(
            """
            SELECT debt_id FROM goal_debts
            WHERE goal_id=? AND debt_type=? AND reason=? AND status='OPEN'
            ORDER BY created_at DESC LIMIT 1
            """,
            (goal_id, debt_type, reason),
        )
        if existing:
            return str(existing["debt_id"])
        debt_id = new_id("goaldebt")
        with self.db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO goal_debts(
                    debt_id,goal_id,debt_type,reason,severity,source_ids_json,
                    status,created_at,resolved_at,resolution
                ) VALUES (?,?,?,?,?,?,'OPEN',?,NULL,NULL)
                """,
                (
                    debt_id,
                    goal_id,
                    debt_type,
                    reason,
                    severity,
                    json.dumps(source_ids, ensure_ascii=False),
                    utc_now(),
                ),
            )
            self.ledger.append(
                "goal_debt_opened",
                {
                    "debt_id": debt_id,
                    "goal_id": goal_id,
                    "debt_type": debt_type,
                    "reason": reason,
                    "severity": severity,
                    "source_ids": source_ids,
                },
                connection,
            )
        return debt_id

    def resolve(self, debt_id: str, resolution: str, *, source_ids: list[str] | None = None) -> bool:
        source_ids = list(dict.fromkeys(source_ids or []))
        with self.db.transaction() as connection:
            updated = connection.execute(
                """
                UPDATE goal_debts SET status='RESOLVED',resolved_at=?,resolution=?
                WHERE debt_id=? AND status='OPEN'
                """,
                (utc_now(), resolution, debt_id),
            ).rowcount
            if updated:
                self.ledger.append(
                    "goal_debt_resolved",
                    {
                        "debt_id": debt_id,
                        "resolution": resolution,
                        "source_ids": source_ids,
                    },
                    connection,
                )
        return bool(updated)

    def resolve_for_goal(self, goal_id: str, resolution: str, *, source_ids: list[str] | None = None) -> int:
        rows = self.db.query_all(
            "SELECT debt_id FROM goal_debts WHERE goal_id=? AND status='OPEN'",
            (goal_id,),
        )
        return sum(
            self.resolve(str(row["debt_id"]), resolution, source_ids=source_ids)
            for row in rows
        )

    def open_for_goal(self, goal_id: str) -> list[dict[str, Any]]:
        rows = self.db.query_all(
            """
            SELECT * FROM goal_debts WHERE goal_id=? AND status='OPEN'
            ORDER BY severity DESC,created_at ASC
            """,
            (goal_id,),
        )
        return [self._row(row) for row in rows]

    def open_all(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.db.query_all(
            """
            SELECT * FROM goal_debts WHERE status='OPEN'
            ORDER BY severity DESC,created_at ASC LIMIT ?
            """,
            (max(1, min(1000, int(limit))),),
        )
        return [self._row(row) for row in rows]

    @staticmethod
    def _row(row: Any) -> dict[str, Any]:
        return {
            "debt_id": row["debt_id"],
            "goal_id": row["goal_id"],
            "debt_type": row["debt_type"],
            "reason": row["reason"],
            "severity": float(row["severity"]),
            "source_ids": json.loads(row["source_ids_json"]),
            "status": row["status"],
            "created_at": row["created_at"],
            "resolved_at": row["resolved_at"],
            "resolution": row["resolution"],
        }
