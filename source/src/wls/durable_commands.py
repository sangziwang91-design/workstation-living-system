from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
import json
import secrets

from .schemas import digest_json, utc_now


TERMINAL_COMMAND_STATES = {"SUCCEEDED", "CANCELLED", "DEAD_LETTER"}
ACTIVE_COMMAND_STATES = {"PENDING", "LEASED", "WAITING_OWNER", "RETRY_WAIT"}
ALL_COMMAND_STATES = TERMINAL_COMMAND_STATES | ACTIVE_COMMAND_STATES


@dataclass(frozen=True, slots=True)
class ClaimedCommand:
    command_id: str
    command_type: str
    payload: dict[str, Any]
    lease_token: str
    attempt: int
    priority: float
    trace_id: str


class DurableCommandQueue:
    """SQLite-backed command inbox with leases, retries, owner interrupts, and DLQ.

    This is a subordinate store owned by ``LivingSystem``. It does not execute
    commands, plan actions, or create an alternative runtime authority.
    """

    def __init__(self, db: Any, ledger: Any) -> None:
        self.db = db
        self.ledger = ledger
        with self.db.transaction() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS durable_commands(
                    command_id TEXT PRIMARY KEY,
                    command_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    payload_digest TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    priority REAL NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL,
                    available_at TEXT NOT NULL,
                    lease_owner TEXT,
                    lease_token TEXT,
                    lease_expires_at TEXT,
                    trace_id TEXT NOT NULL,
                    result_json TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_durable_commands_claim
                ON durable_commands(status,available_at,priority DESC,created_at)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS owner_interrupts(
                    interrupt_id TEXT PRIMARY KEY,
                    command_id TEXT NOT NULL UNIQUE,
                    prompt_json TEXT NOT NULL,
                    prompt_digest TEXT NOT NULL,
                    status TEXT NOT NULL,
                    decision_json TEXT,
                    decided_by TEXT,
                    created_at TEXT NOT NULL,
                    decided_at TEXT,
                    FOREIGN KEY(command_id) REFERENCES durable_commands(command_id)
                )
                """
            )

    @staticmethod
    def _parse_time(value: str) -> datetime:
        return datetime.fromisoformat(value)

    def enqueue(
        self,
        command_type: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str,
        priority: float = 0.5,
        max_attempts: int = 3,
        trace_id: str | None = None,
    ) -> str:
        if not command_type.strip():
            raise ValueError("command_type is required")
        if not idempotency_key.strip():
            raise ValueError("idempotency_key is required")
        if not 0.0 <= float(priority) <= 1.0:
            raise ValueError("priority must be within 0..1")
        if not 1 <= int(max_attempts) <= 100:
            raise ValueError("max_attempts must be within 1..100")
        normalized = json.loads(
            json.dumps(payload, ensure_ascii=False, sort_keys=True)
        )
        payload_digest = digest_json(normalized)
        now = utc_now()
        command_id = f"cmd_{secrets.token_hex(12)}"
        trace_id = trace_id or f"trace_{secrets.token_hex(12)}"
        with self.db.transaction() as connection:
            row = connection.execute(
                """
                SELECT command_id,payload_digest,command_type
                FROM durable_commands WHERE idempotency_key=?
                """,
                (idempotency_key,),
            ).fetchone()
            if row is not None:
                if (
                    str(row["payload_digest"]) != payload_digest
                    or str(row["command_type"]) != command_type
                ):
                    raise ValueError(
                        "idempotency key belongs to a different command contract"
                    )
                return str(row["command_id"])
            connection.execute(
                """
                INSERT INTO durable_commands(
                    command_id,command_type,payload_json,payload_digest,
                    idempotency_key,status,priority,max_attempts,available_at,
                    trace_id,created_at,updated_at
                ) VALUES (?,?,?,?,?,'PENDING',?,?,?,?,?,?)
                """,
                (
                    command_id,
                    command_type,
                    json.dumps(normalized, ensure_ascii=False, sort_keys=True),
                    payload_digest,
                    idempotency_key,
                    float(priority),
                    int(max_attempts),
                    now,
                    trace_id,
                    now,
                    now,
                ),
            )
            self.ledger.append(
                "durable_command_enqueued",
                {
                    "command_id": command_id,
                    "command_type": command_type,
                    "payload_digest": payload_digest,
                    "idempotency_key": idempotency_key,
                    "priority": float(priority),
                    "trace_id": trace_id,
                },
                connection,
            )
        return command_id

    def _release_expired_locked(self, connection: Any, now: str) -> list[str]:
        rows = connection.execute(
            """
            SELECT command_id FROM durable_commands
            WHERE status='LEASED' AND lease_expires_at IS NOT NULL
              AND lease_expires_at<=?
            """,
            (now,),
        ).fetchall()
        command_ids = [str(row["command_id"]) for row in rows]
        if command_ids:
            connection.execute(
                """
                UPDATE durable_commands
                SET status='PENDING',lease_owner=NULL,lease_token=NULL,
                    lease_expires_at=NULL,updated_at=?,
                    last_error=COALESCE(last_error,'lease expired')
                WHERE status='LEASED' AND lease_expires_at IS NOT NULL
                  AND lease_expires_at<=?
                """,
                (now, now),
            )
            self.ledger.append(
                "durable_command_leases_released",
                {"command_ids": command_ids},
                connection,
            )
        return command_ids

    def claim(
        self, worker_id: str, *, lease_seconds: float = 60.0
    ) -> ClaimedCommand | None:
        if not worker_id.strip():
            raise ValueError("worker_id is required")
        if not 1.0 <= float(lease_seconds) <= 86_400:
            raise ValueError("lease_seconds must be within 1..86400")
        now = utc_now()
        expires = (
            datetime.now(UTC) + timedelta(seconds=float(lease_seconds))
        ).isoformat()
        token = secrets.token_urlsafe(24)
        with self.db.transaction() as connection:
            self._release_expired_locked(connection, now)
            row = connection.execute(
                """
                SELECT * FROM durable_commands
                WHERE status IN ('PENDING','RETRY_WAIT') AND available_at<=?
                ORDER BY priority DESC,created_at ASC LIMIT 1
                """,
                (now,),
            ).fetchone()
            if row is None:
                return None
            updated = connection.execute(
                """
                UPDATE durable_commands
                SET status='LEASED',lease_owner=?,lease_token=?,
                    lease_expires_at=?,attempt_count=attempt_count+1,
                    updated_at=?
                WHERE command_id=? AND status IN ('PENDING','RETRY_WAIT')
                """,
                (
                    worker_id,
                    token,
                    expires,
                    now,
                    row["command_id"],
                ),
            ).rowcount
            if updated != 1:
                return None
            attempt = int(row["attempt_count"]) + 1
            self.ledger.append(
                "durable_command_claimed",
                {
                    "command_id": str(row["command_id"]),
                    "worker_id": worker_id,
                    "attempt": attempt,
                    "lease_expires_at": expires,
                    "trace_id": str(row["trace_id"]),
                },
                connection,
            )
            return ClaimedCommand(
                command_id=str(row["command_id"]),
                command_type=str(row["command_type"]),
                payload=json.loads(row["payload_json"]),
                lease_token=token,
                attempt=attempt,
                priority=float(row["priority"]),
                trace_id=str(row["trace_id"]),
            )

    def _owned(
        self, connection: Any, command_id: str, lease_token: str
    ) -> Any:
        row = connection.execute(
            "SELECT * FROM durable_commands WHERE command_id=?", (command_id,)
        ).fetchone()
        if row is None:
            raise KeyError(command_id)
        if (
            str(row["status"]) != "LEASED"
            or str(row["lease_token"] or "") != lease_token
        ):
            raise RuntimeError("command lease is not owned by caller")
        if row["lease_expires_at"] and self._parse_time(
            str(row["lease_expires_at"])
        ) <= datetime.now(UTC):
            raise RuntimeError("command lease expired")
        return row

    def succeed(
        self, command_id: str, lease_token: str, result: dict[str, Any]
    ) -> dict[str, Any]:
        normalized = json.loads(
            json.dumps(result, ensure_ascii=False, sort_keys=True)
        )
        now = utc_now()
        with self.db.transaction() as connection:
            row = self._owned(connection, command_id, lease_token)
            connection.execute(
                """
                UPDATE durable_commands
                SET status='SUCCEEDED',result_json=?,last_error=NULL,
                    lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL,
                    updated_at=?,completed_at=?
                WHERE command_id=?
                """,
                (
                    json.dumps(normalized, ensure_ascii=False, sort_keys=True),
                    now,
                    now,
                    command_id,
                ),
            )
            self.ledger.append(
                "durable_command_succeeded",
                {
                    "command_id": command_id,
                    "result_digest": digest_json(normalized),
                    "attempt": int(row["attempt_count"]),
                    "trace_id": str(row["trace_id"]),
                },
                connection,
            )
        return self.get(command_id)

    def fail(
        self,
        command_id: str,
        lease_token: str,
        error: str,
        *,
        backoff_seconds: float = 1.0,
    ) -> dict[str, Any]:
        if not 0 <= float(backoff_seconds) <= 86_400:
            raise ValueError("backoff_seconds must be within 0..86400")
        now_value = datetime.now(UTC)
        now = now_value.isoformat()
        with self.db.transaction() as connection:
            row = self._owned(connection, command_id, lease_token)
            exhausted = int(row["attempt_count"]) >= int(row["max_attempts"])
            status = "DEAD_LETTER" if exhausted else "RETRY_WAIT"
            available_at = (
                now_value + timedelta(seconds=float(backoff_seconds))
            ).isoformat()
            completed_at = now if exhausted else None
            connection.execute(
                """
                UPDATE durable_commands
                SET status=?,available_at=?,last_error=?,lease_owner=NULL,
                    lease_token=NULL,lease_expires_at=NULL,updated_at=?,
                    completed_at=?
                WHERE command_id=?
                """,
                (
                    status,
                    available_at,
                    str(error)[:4000],
                    now,
                    completed_at,
                    command_id,
                ),
            )
            self.ledger.append(
                "durable_command_failed",
                {
                    "command_id": command_id,
                    "status": status,
                    "attempt": int(row["attempt_count"]),
                    "max_attempts": int(row["max_attempts"]),
                    "error": str(error)[:1000],
                    "trace_id": str(row["trace_id"]),
                },
                connection,
            )
        return self.get(command_id)

    def interrupt(
        self, command_id: str, lease_token: str, prompt: dict[str, Any]
    ) -> str:
        normalized = json.loads(
            json.dumps(prompt, ensure_ascii=False, sort_keys=True)
        )
        now = utc_now()
        interrupt_id = f"interrupt_{secrets.token_hex(12)}"
        with self.db.transaction() as connection:
            row = self._owned(connection, command_id, lease_token)
            connection.execute(
                """
                UPDATE durable_commands
                SET status='WAITING_OWNER',lease_owner=NULL,lease_token=NULL,
                    lease_expires_at=NULL,updated_at=? WHERE command_id=?
                """,
                (now, command_id),
            )
            connection.execute(
                """
                INSERT INTO owner_interrupts(
                    interrupt_id,command_id,prompt_json,prompt_digest,status,
                    created_at
                ) VALUES (?,?,?,?, 'PENDING',?)
                """,
                (
                    interrupt_id,
                    command_id,
                    json.dumps(normalized, ensure_ascii=False, sort_keys=True),
                    digest_json(normalized),
                    now,
                ),
            )
            self.ledger.append(
                "owner_interrupt_requested",
                {
                    "interrupt_id": interrupt_id,
                    "command_id": command_id,
                    "prompt_digest": digest_json(normalized),
                    "trace_id": str(row["trace_id"]),
                },
                connection,
            )
        return interrupt_id

    def decide(
        self,
        interrupt_id: str,
        *,
        approved: bool,
        decided_by: str,
        decision: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not decided_by.strip():
            raise ValueError("decided_by is required")
        normalized = json.loads(
            json.dumps(decision or {}, ensure_ascii=False, sort_keys=True)
        )
        now = utc_now()
        with self.db.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM owner_interrupts WHERE interrupt_id=?",
                (interrupt_id,),
            ).fetchone()
            if row is None:
                raise KeyError(interrupt_id)
            if str(row["status"]) != "PENDING":
                existing = json.loads(row["decision_json"] or "{}")
                if bool(existing.get("approved")) != bool(approved):
                    raise ValueError("owner decision is immutable")
                return self.get(str(row["command_id"]))
            command = connection.execute(
                "SELECT status FROM durable_commands WHERE command_id=?",
                (row["command_id"],),
            ).fetchone()
            if command is None or str(command["status"]) != "WAITING_OWNER":
                raise RuntimeError("interrupted command is not waiting for owner")
            payload = {"approved": bool(approved), "details": normalized}
            connection.execute(
                """
                UPDATE owner_interrupts
                SET status='DECIDED',decision_json=?,decided_by=?,decided_at=?
                WHERE interrupt_id=?
                """,
                (
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    decided_by,
                    now,
                    interrupt_id,
                ),
            )
            new_status = "PENDING" if approved else "CANCELLED"
            connection.execute(
                """
                UPDATE durable_commands
                SET status=?,available_at=?,updated_at=?,completed_at=?
                WHERE command_id=?
                """,
                (
                    new_status,
                    now,
                    now,
                    None if approved else now,
                    row["command_id"],
                ),
            )
            self.ledger.append(
                "owner_interrupt_decided",
                {
                    "interrupt_id": interrupt_id,
                    "command_id": str(row["command_id"]),
                    "approved": bool(approved),
                    "decided_by": decided_by,
                },
                connection,
            )
        return self.get(str(row["command_id"]))

    def cancel(self, command_id: str, reason: str) -> dict[str, Any]:
        now = utc_now()
        with self.db.transaction() as connection:
            row = connection.execute(
                "SELECT status,trace_id FROM durable_commands WHERE command_id=?",
                (command_id,),
            ).fetchone()
            if row is None:
                raise KeyError(command_id)
            if str(row["status"]) in TERMINAL_COMMAND_STATES:
                return self.get(command_id)
            connection.execute(
                """
                UPDATE durable_commands
                SET status='CANCELLED',last_error=?,lease_owner=NULL,
                    lease_token=NULL,lease_expires_at=NULL,updated_at=?,
                    completed_at=? WHERE command_id=?
                """,
                (str(reason)[:4000], now, now, command_id),
            )
            self.ledger.append(
                "durable_command_cancelled",
                {
                    "command_id": command_id,
                    "reason": str(reason)[:1000],
                    "trace_id": str(row["trace_id"]),
                },
                connection,
            )
        return self.get(command_id)

    def get(self, command_id: str) -> dict[str, Any]:
        row = self.db.query_one(
            "SELECT * FROM durable_commands WHERE command_id=?", (command_id,)
        )
        if row is None:
            raise KeyError(command_id)
        return {
            "command_id": str(row["command_id"]),
            "command_type": str(row["command_type"]),
            "payload": json.loads(row["payload_json"]),
            "status": str(row["status"]),
            "priority": float(row["priority"]),
            "attempt_count": int(row["attempt_count"]),
            "max_attempts": int(row["max_attempts"]),
            "available_at": str(row["available_at"]),
            "trace_id": str(row["trace_id"]),
            "result": (
                json.loads(row["result_json"]) if row["result_json"] else None
            ),
            "last_error": (
                str(row["last_error"]) if row["last_error"] else None
            ),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "completed_at": (
                str(row["completed_at"]) if row["completed_at"] else None
            ),
        }

    def integrity(self) -> tuple[bool, dict[str, int]]:
        checks = {
            "invalid_state": """
                SELECT COUNT(*) AS n FROM durable_commands
                WHERE status NOT IN (
                    'PENDING','LEASED','WAITING_OWNER','RETRY_WAIT',
                    'SUCCEEDED','CANCELLED','DEAD_LETTER'
                )
            """,
            "leased_without_token": """
                SELECT COUNT(*) AS n FROM durable_commands
                WHERE status='LEASED' AND (
                    lease_token IS NULL OR lease_owner IS NULL
                    OR lease_expires_at IS NULL
                )
            """,
            "nonleased_with_lease": """
                SELECT COUNT(*) AS n FROM durable_commands
                WHERE status<>'LEASED' AND (
                    lease_token IS NOT NULL OR lease_owner IS NOT NULL
                    OR lease_expires_at IS NOT NULL
                )
            """,
            "waiting_without_interrupt": """
                SELECT COUNT(*) AS n FROM durable_commands c
                LEFT JOIN owner_interrupts i
                  ON i.command_id=c.command_id AND i.status='PENDING'
                WHERE c.status='WAITING_OWNER' AND i.interrupt_id IS NULL
            """,
            "terminal_without_time": """
                SELECT COUNT(*) AS n FROM durable_commands
                WHERE status IN ('SUCCEEDED','CANCELLED','DEAD_LETTER')
                  AND completed_at IS NULL
            """,
        }
        counts: dict[str, int] = {}
        for name, sql in checks.items():
            row = self.db.query_one(sql)
            counts[name] = int(row["n"]) if row else 0
        return all(value == 0 for value in counts.values()), counts
