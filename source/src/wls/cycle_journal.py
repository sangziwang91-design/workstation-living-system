from __future__ import annotations

from typing import Any
import json

from .db import Database
from .evidence import EvidenceLedger
from .schemas import digest_json, new_id, utc_now


PHASE_ORDER = {
    "PLAN_PERSISTED": 10,
    "ACTIONS_TERMINAL": 20,
    "RECOVERY_RESOLVED": 30,
}


class CycleJournal:
    """Durable phase history over canonical cycles, plans, and actions."""

    def __init__(self, db: Database, ledger: EvidenceLedger) -> None:
        self.db = db
        self.ledger = ledger
        with db.transaction() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS cycle_checkpoints(
                    checkpoint_id TEXT PRIMARY KEY,
                    cycle_id TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    phase_order INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    payload_digest TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(cycle_id,phase),
                    FOREIGN KEY(cycle_id) REFERENCES cycles(cycle_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS cycle_recoveries(
                    cycle_id TEXT PRIMARY KEY,
                    plan_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    resolved_at TEXT,
                    FOREIGN KEY(cycle_id) REFERENCES cycles(cycle_id),
                    FOREIGN KEY(plan_id) REFERENCES plans(plan_id)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_cycle_recoveries_status_time
                ON cycle_recoveries(status,updated_at)
                """
            )

    def record(
        self,
        cycle_id: str,
        phase: str,
        payload: dict[str, Any],
        connection: Any | None = None,
    ) -> str:
        if phase not in PHASE_ORDER:
            raise ValueError(f"unknown cycle phase: {phase}")
        normalized = json.loads(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        payload_digest = digest_json(normalized)
        if connection is None:
            with self.db.transaction() as owned:
                return self.record(cycle_id, phase, normalized, owned)
        existing = connection.execute(
            "SELECT checkpoint_id,payload_digest FROM cycle_checkpoints WHERE cycle_id=? AND phase=?",
            (cycle_id, phase),
        ).fetchone()
        if existing is not None:
            if str(existing["payload_digest"]) != payload_digest:
                raise ValueError(f"immutable checkpoint changed: {cycle_id}/{phase}")
            return str(existing["checkpoint_id"])
        latest = connection.execute(
            "SELECT phase_order FROM cycle_checkpoints WHERE cycle_id=? ORDER BY phase_order DESC LIMIT 1",
            (cycle_id,),
        ).fetchone()
        order = PHASE_ORDER[phase]
        if latest is not None and order < int(latest["phase_order"]):
            raise ValueError(f"cycle checkpoint regression for {cycle_id}")
        checkpoint_id = new_id("checkpoint")
        connection.execute(
            """
            INSERT INTO cycle_checkpoints(
                checkpoint_id,cycle_id,phase,phase_order,payload_json,
                payload_digest,created_at
            ) VALUES (?,?,?,?,?,?,?)
            """,
            (
                checkpoint_id,
                cycle_id,
                phase,
                order,
                json.dumps(normalized, ensure_ascii=False, sort_keys=True),
                payload_digest,
                utc_now(),
            ),
        )
        self.ledger.append(
            "cycle_checkpoint_recorded",
            {
                "checkpoint_id": checkpoint_id,
                "cycle_id": cycle_id,
                "phase": phase,
                "payload_digest": payload_digest,
            },
            connection,
        )
        return checkpoint_id

    def classify_interrupted(self) -> dict[str, list[str]]:
        """Classify interrupted cycles before generic startup cleanup."""

        result: dict[str, list[str]] = {
            "failed_before_plan": [],
            "pending_recovery": [],
        }
        rows = self.db.query_all(
            "SELECT cycle_id FROM cycles WHERE status='RUNNING' ORDER BY started_at"
        )
        if not rows:
            return result
        now = utc_now()
        with self.db.transaction() as connection:
            for row in rows:
                cycle_id = str(row["cycle_id"])
                plan = connection.execute(
                    "SELECT plan_id FROM plans WHERE cycle_id=? ORDER BY created_at DESC LIMIT 1",
                    (cycle_id,),
                ).fetchone()
                if plan is None:
                    updated = connection.execute(
                        """
                        UPDATE cycles SET status='FAILED',finished_at=?,error=?
                        WHERE cycle_id=? AND status='RUNNING'
                        """,
                        (now, "interrupted before durable plan", cycle_id),
                    ).rowcount
                    if updated == 1:
                        result["failed_before_plan"].append(cycle_id)
                    continue
                plan_id = str(plan["plan_id"])
                updated = connection.execute(
                    """
                    UPDATE cycles SET status='RECOVERY_PENDING',error=?
                    WHERE cycle_id=? AND status='RUNNING'
                    """,
                    ("interrupted after durable plan", cycle_id),
                ).rowcount
                if updated != 1:
                    continue
                details = {"classification": "DURABLE_PLAN", "plan_id": plan_id}
                connection.execute(
                    """
                    INSERT INTO cycle_recoveries(
                        cycle_id,plan_id,status,details_json,created_at,updated_at,resolved_at
                    ) VALUES (?,?,?,?,?,?,NULL)
                    ON CONFLICT(cycle_id) DO UPDATE SET
                        plan_id=excluded.plan_id,status='PENDING',
                        details_json=excluded.details_json,
                        updated_at=excluded.updated_at,resolved_at=NULL
                    """,
                    (
                        cycle_id,
                        plan_id,
                        "PENDING",
                        json.dumps(details, ensure_ascii=False, sort_keys=True),
                        now,
                        now,
                    ),
                )
                result["pending_recovery"].append(cycle_id)
            self.ledger.append("interrupted_cycles_classified", result, connection)
        return result

    def resolve(
        self,
        cycle_id: str,
        outcomes: list[dict[str, Any]],
        postprocess: dict[str, Any],
    ) -> dict[str, Any]:
        payload = {"outcomes": outcomes, "postprocess": postprocess}
        now = utc_now()
        with self.db.transaction() as connection:
            row = connection.execute(
                "SELECT status FROM cycle_recoveries WHERE cycle_id=?", (cycle_id,)
            ).fetchone()
            if row is None:
                raise KeyError(cycle_id)
            if str(row["status"]) == "RESOLVED":
                return self.get(cycle_id)
            connection.execute(
                """
                UPDATE cycle_recoveries
                SET status='RESOLVED',details_json=?,updated_at=?,resolved_at=?
                WHERE cycle_id=?
                """,
                (
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    now,
                    now,
                    cycle_id,
                ),
            )
            connection.execute(
                """
                UPDATE cycles
                SET status='RECOVERED',finished_at=?,error=NULL,metrics_json=?
                WHERE cycle_id=? AND status='RECOVERY_PENDING'
                """,
                (
                    now,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    cycle_id,
                ),
            )
            self.record(cycle_id, "RECOVERY_RESOLVED", payload, connection)
            self.ledger.append(
                "cycle_recovery_resolved",
                {"cycle_id": cycle_id, **payload},
                connection,
            )
        return self.get(cycle_id)

    def get(self, cycle_id: str) -> dict[str, Any]:
        row = self.db.query_one(
            "SELECT * FROM cycle_recoveries WHERE cycle_id=?", (cycle_id,)
        )
        if row is None:
            raise KeyError(cycle_id)
        return {
            "cycle_id": str(row["cycle_id"]),
            "plan_id": str(row["plan_id"]),
            "status": str(row["status"]),
            "details": json.loads(row["details_json"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "resolved_at": str(row["resolved_at"]) if row["resolved_at"] else None,
        }

    def pending(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.db.query_all(
            """
            SELECT cycle_id FROM cycle_recoveries
            WHERE status='PENDING' ORDER BY created_at LIMIT ?
            """,
            (max(1, min(1000, int(limit))),),
        )
        return [self.get(str(row["cycle_id"])) for row in rows]

    def summary(self) -> dict[str, Any]:
        checkpoint = self.db.query_one("SELECT COUNT(*) AS n FROM cycle_checkpoints")
        rows = self.db.query_all(
            "SELECT status,COUNT(*) AS n FROM cycle_recoveries GROUP BY status"
        )
        return {
            "checkpoints": int(checkpoint["n"]) if checkpoint else 0,
            "recoveries": {str(row["status"]): int(row["n"]) for row in rows},
            "pending": self.pending(limit=20),
        }

    def integrity(self) -> tuple[bool, dict[str, int]]:
        queries = {
            "orphan_checkpoints": """
                SELECT COUNT(*) AS n FROM cycle_checkpoints j
                LEFT JOIN cycles c ON c.cycle_id=j.cycle_id
                WHERE c.cycle_id IS NULL
            """,
            "orphan_recoveries": """
                SELECT COUNT(*) AS n FROM cycle_recoveries r
                LEFT JOIN cycles c ON c.cycle_id=r.cycle_id
                LEFT JOIN plans p ON p.plan_id=r.plan_id
                WHERE c.cycle_id IS NULL OR p.plan_id IS NULL
            """,
            "invalid_recovery_status": """
                SELECT COUNT(*) AS n FROM cycle_recoveries
                WHERE status NOT IN ('PENDING','RESOLVED')
            """,
            "resolved_without_time": """
                SELECT COUNT(*) AS n FROM cycle_recoveries
                WHERE status='RESOLVED' AND resolved_at IS NULL
            """,
        }
        counts: dict[str, int] = {}
        for name, sql in queries.items():
            row = self.db.query_one(sql)
            counts[name] = int(row["n"]) if row else 0
        return all(value == 0 for value in counts.values()), counts
