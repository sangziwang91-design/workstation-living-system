from __future__ import annotations

from pathlib import Path
from typing import Any
import json

from .config import RuntimeConfig
from .db import Database
from .evidence import EvidenceLedger
from .schemas import new_id, utc_now


def ensure_survival_tables(db: Database) -> None:
    statements = [
        """
        CREATE TABLE IF NOT EXISTS survival_runs (
            run_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            max_cycles INTEGER,
            completed_cycles INTEGER NOT NULL DEFAULT 0,
            failed_cycles INTEGER NOT NULL DEFAULT 0,
            consecutive_failures INTEGER NOT NULL DEFAULT 0,
            peak_pending_events INTEGER NOT NULL DEFAULT 0,
            peak_database_bytes INTEGER NOT NULL DEFAULT 0,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            termination_reason TEXT,
            report_json TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS survival_heartbeats (
            heartbeat_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            cycle_index INTEGER NOT NULL,
            status TEXT NOT NULL,
            pending_events INTEGER NOT NULL,
            database_bytes INTEGER NOT NULL,
            duration_seconds REAL,
            recorded_at TEXT NOT NULL,
            FOREIGN KEY(run_id) REFERENCES survival_runs(run_id)
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_survival_heartbeats_run_cycle
        ON survival_heartbeats(run_id, cycle_index)
        """,
        """
        CREATE TABLE IF NOT EXISTS runtime_incidents (
            incident_id TEXT PRIMARY KEY,
            run_id TEXT,
            cycle_index INTEGER,
            kind TEXT NOT NULL,
            severity TEXT NOT NULL,
            error TEXT NOT NULL,
            consecutive_failures INTEGER NOT NULL DEFAULT 0,
            details_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            resolved_at TEXT,
            FOREIGN KEY(run_id) REFERENCES survival_runs(run_id)
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_runtime_incidents_time
        ON runtime_incidents(created_at DESC)
        """,
    ]
    with db.transaction() as connection:
        for statement in statements:
            connection.execute(statement)


class SurvivalSupervisor:
    """Bounded daemon supervision, operational evidence, and failure containment."""

    def __init__(
        self,
        db: Database,
        ledger: EvidenceLedger,
        config: RuntimeConfig,
    ) -> None:
        self.db = db
        self.ledger = ledger
        self.config = config
        ensure_survival_tables(db)

    def recover_interrupted_cycles(self) -> list[str]:
        rows = self.db.query_all(
            "SELECT cycle_id FROM cycles WHERE status='RUNNING' ORDER BY started_at"
        )
        if not rows:
            return []
        cycle_ids = [str(row["cycle_id"]) for row in rows]
        now = utc_now()
        with self.db.transaction() as connection:
            for cycle_id in cycle_ids:
                connection.execute(
                    """
                    UPDATE cycles
                    SET status='FAILED',finished_at=?,error=?
                    WHERE cycle_id=? AND status='RUNNING'
                    """,
                    (now, "recovered after interrupted runtime process", cycle_id),
                )
            self.ledger.append(
                "interrupted_cycles_recovered",
                {"cycle_ids": cycle_ids, "count": len(cycle_ids)},
                connection,
            )
        return cycle_ids

    def start_run(self, max_cycles: int | None) -> str:
        if max_cycles is not None and max_cycles < 0:
            raise ValueError("max_cycles must be non-negative")
        run_id = new_id("survival")
        with self.db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO survival_runs(
                    run_id,status,max_cycles,started_at
                ) VALUES (?,?,?,?)
                """,
                (run_id, "RUNNING", max_cycles, utc_now()),
            )
            self.ledger.append(
                "survival_run_started",
                {"run_id": run_id, "max_cycles": max_cycles},
                connection,
            )
        return run_id

    def preflight(self, run_id: str, cycle_index: int) -> dict[str, Any]:
        pending = int(
            self.db.query_one(
                "SELECT COUNT(*) AS count FROM events WHERE status='PENDING'"
            )["count"]
        )
        database_bytes = self.database_bytes()
        self._update_peaks(run_id, pending, database_bytes)
        violations: list[dict[str, Any]] = []
        if pending > self.config.daemon_max_pending_events:
            violations.append(
                {
                    "kind": "EVENT_BACKLOG_BUDGET",
                    "actual": pending,
                    "limit": self.config.daemon_max_pending_events,
                }
            )
        if database_bytes > self.config.daemon_max_database_bytes:
            violations.append(
                {
                    "kind": "DATABASE_SIZE_BUDGET",
                    "actual": database_bytes,
                    "limit": self.config.daemon_max_database_bytes,
                }
            )
        if violations:
            for violation in violations:
                self.record_incident(
                    run_id=run_id,
                    cycle_index=cycle_index,
                    kind=str(violation["kind"]),
                    severity="CRITICAL",
                    error="survival resource budget exceeded",
                    details=violation,
                )
            return {
                "allowed": False,
                "pending_events": pending,
                "database_bytes": database_bytes,
                "violations": violations,
            }
        return {
            "allowed": True,
            "pending_events": pending,
            "database_bytes": database_bytes,
            "violations": [],
        }

    def record_success(
        self,
        run_id: str,
        cycle_index: int,
        duration_seconds: float,
        result_status: str,
    ) -> dict[str, Any]:
        slow = duration_seconds > self.config.daemon_max_cycle_seconds
        with self.db.transaction() as connection:
            connection.execute(
                """
                UPDATE survival_runs
                SET completed_cycles=completed_cycles+1,consecutive_failures=0
                WHERE run_id=?
                """,
                (run_id,),
            )
        if slow:
            self.record_incident(
                run_id=run_id,
                cycle_index=cycle_index,
                kind="SLOW_CYCLE",
                severity="WARNING",
                error="cycle duration exceeded configured budget",
                details={
                    "duration_seconds": duration_seconds,
                    "limit": self.config.daemon_max_cycle_seconds,
                    "result_status": result_status,
                },
            )
        self.heartbeat(
            run_id,
            cycle_index,
            "SLOW" if slow else result_status,
            duration_seconds,
        )
        return {"slow": slow, "duration_seconds": duration_seconds}

    def record_failure(
        self,
        run_id: str,
        cycle_index: int,
        exc: Exception,
    ) -> dict[str, Any]:
        row = self.db.query_one(
            "SELECT consecutive_failures FROM survival_runs WHERE run_id=?", (run_id,)
        )
        if row is None:
            raise KeyError(run_id)
        consecutive = int(row["consecutive_failures"]) + 1
        with self.db.transaction() as connection:
            connection.execute(
                """
                UPDATE survival_runs
                SET failed_cycles=failed_cycles+1,consecutive_failures=?
                WHERE run_id=?
                """,
                (consecutive, run_id),
            )
        error = f"{type(exc).__name__}: {exc}"[:4000]
        self.record_incident(
            run_id=run_id,
            cycle_index=cycle_index,
            kind="CYCLE_EXCEPTION",
            severity=(
                "CRITICAL"
                if consecutive > self.config.daemon_max_consecutive_failures
                else "ERROR"
            ),
            error=error,
            consecutive_failures=consecutive,
            details={"exception_type": type(exc).__name__},
        )
        self.heartbeat(run_id, cycle_index, "FAILED", None)
        backoff = min(
            self.config.daemon_failure_backoff_max_seconds,
            self.config.daemon_failure_backoff_seconds
            * (2 ** max(0, consecutive - 1)),
        )
        return {
            "stop": consecutive > self.config.daemon_max_consecutive_failures,
            "consecutive_failures": consecutive,
            "backoff_seconds": backoff,
            "error": error,
        }

    def record_incident(
        self,
        *,
        run_id: str | None,
        cycle_index: int | None,
        kind: str,
        severity: str,
        error: str,
        details: dict[str, Any],
        consecutive_failures: int = 0,
    ) -> str:
        incident_id = new_id("incident")
        payload = {
            "incident_id": incident_id,
            "run_id": run_id,
            "cycle_index": cycle_index,
            "kind": kind,
            "severity": severity,
            "error": error,
            "consecutive_failures": consecutive_failures,
            "details": details,
        }
        with self.db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO runtime_incidents(
                    incident_id,run_id,cycle_index,kind,severity,error,
                    consecutive_failures,details_json,created_at
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    incident_id,
                    run_id,
                    cycle_index,
                    kind,
                    severity,
                    error[:4000],
                    consecutive_failures,
                    json.dumps(details, ensure_ascii=False, sort_keys=True),
                    utc_now(),
                ),
            )
            self.ledger.append("runtime_incident", payload, connection)
        return incident_id

    def heartbeat(
        self,
        run_id: str,
        cycle_index: int,
        status: str,
        duration_seconds: float | None,
    ) -> str | None:
        if cycle_index % self.config.daemon_heartbeat_every_cycles != 0:
            return None
        heartbeat_id = new_id("heartbeat")
        pending = int(
            self.db.query_one(
                "SELECT COUNT(*) AS count FROM events WHERE status='PENDING'"
            )["count"]
        )
        database_bytes = self.database_bytes()
        with self.db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO survival_heartbeats(
                    heartbeat_id,run_id,cycle_index,status,pending_events,
                    database_bytes,duration_seconds,recorded_at
                ) VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    heartbeat_id,
                    run_id,
                    cycle_index,
                    status,
                    pending,
                    database_bytes,
                    duration_seconds,
                    utc_now(),
                ),
            )
            connection.execute(
                """
                DELETE FROM survival_heartbeats
                WHERE rowid IN (
                    SELECT rowid FROM survival_heartbeats
                    ORDER BY rowid DESC LIMIT -1 OFFSET ?
                )
                """,
                (self.config.daemon_heartbeat_retention,),
            )
        return heartbeat_id

    def finish_run(self, run_id: str, status: str, reason: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM survival_runs WHERE run_id=?", (run_id,))
        if row is None:
            raise KeyError(run_id)
        incidents = self.db.query_all(
            "SELECT kind,severity,error,cycle_index,created_at FROM runtime_incidents WHERE run_id=? ORDER BY created_at",
            (run_id,),
        )
        report = {
            "run_id": run_id,
            "status": status,
            "termination_reason": reason,
            "completed_cycles": int(row["completed_cycles"]),
            "failed_cycles": int(row["failed_cycles"]),
            "consecutive_failures": int(row["consecutive_failures"]),
            "peak_pending_events": int(row["peak_pending_events"]),
            "peak_database_bytes": int(row["peak_database_bytes"]),
            "incidents": [dict(item) for item in incidents],
            "finished_at": utc_now(),
        }
        with self.db.transaction() as connection:
            connection.execute(
                """
                UPDATE survival_runs
                SET status=?,finished_at=?,termination_reason=?,report_json=?
                WHERE run_id=?
                """,
                (
                    status,
                    report["finished_at"],
                    reason,
                    json.dumps(report, ensure_ascii=False, sort_keys=True),
                    run_id,
                ),
            )
            self.ledger.append(
                "survival_run_finished",
                {
                    "run_id": run_id,
                    "status": status,
                    "termination_reason": reason,
                    "completed_cycles": report["completed_cycles"],
                    "failed_cycles": report["failed_cycles"],
                },
                connection,
            )
        return report

    def status(self, incident_limit: int = 20) -> dict[str, Any]:
        run = self.db.query_one(
            "SELECT * FROM survival_runs ORDER BY started_at DESC LIMIT 1"
        )
        incidents = self.db.query_all(
            "SELECT * FROM runtime_incidents ORDER BY created_at DESC LIMIT ?",
            (max(1, min(500, incident_limit)),),
        )
        heartbeats = int(
            self.db.query_one(
                "SELECT COUNT(*) AS count FROM survival_heartbeats"
            )["count"]
        )
        return {
            "latest_run": self._run_view(run) if run else None,
            "heartbeat_count": heartbeats,
            "recent_incidents": [self._incident_view(row) for row in incidents],
            "budgets": {
                "max_consecutive_failures": self.config.daemon_max_consecutive_failures,
                "max_pending_events": self.config.daemon_max_pending_events,
                "max_database_bytes": self.config.daemon_max_database_bytes,
                "max_cycle_seconds": self.config.daemon_max_cycle_seconds,
            },
        }

    def database_bytes(self) -> int:
        path = Path(self.db.path)
        total = 0
        for candidate in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")):
            try:
                total += candidate.stat().st_size
            except OSError:
                continue
        return total

    def _update_peaks(self, run_id: str, pending: int, database_bytes: int) -> None:
        self.db.execute(
            """
            UPDATE survival_runs
            SET peak_pending_events=MAX(peak_pending_events,?),
                peak_database_bytes=MAX(peak_database_bytes,?)
            WHERE run_id=?
            """,
            (pending, database_bytes, run_id),
        )

    @staticmethod
    def _run_view(row) -> dict[str, Any]:
        value = dict(row)
        value["report"] = (
            json.loads(value.pop("report_json")) if value.get("report_json") else None
        )
        value.pop("report_json", None)
        return value

    @staticmethod
    def _incident_view(row) -> dict[str, Any]:
        value = dict(row)
        value["details"] = json.loads(value.pop("details_json"))
        return value
