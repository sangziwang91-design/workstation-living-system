from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from .schemas import Event, EventStatus, utc_now
from .stores import EventStore


class ReliableEventStore(EventStore):
    """EventStore with deterministic retry scheduling and dead-letter evidence."""

    def __init__(self, db: Any, ledger: Any) -> None:
        super().__init__(db, ledger)
        with self.db.transaction() as connection:
            columns = {
                str(row["name"])
                for row in connection.execute(
                    "PRAGMA table_info(events)"
                ).fetchall()
            }
            if "next_attempt_at" not in columns:
                connection.execute(
                    "ALTER TABLE events ADD COLUMN next_attempt_at TEXT"
                )
            if "last_failed_at" not in columns:
                connection.execute(
                    "ALTER TABLE events ADD COLUMN last_failed_at TEXT"
                )
            if "dead_lettered_at" not in columns:
                connection.execute(
                    "ALTER TABLE events ADD COLUMN dead_lettered_at TEXT"
                )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_events_due
                ON events(status,next_attempt_at,salience_hint,occurred_at)
                """
            )

    def reserve(self, worker_id: str, limit: int) -> list[Event]:
        now = utc_now()
        with self.db.transaction() as connection:
            rows = connection.execute(
                """
                SELECT * FROM events
                WHERE status=?
                  AND (next_attempt_at IS NULL OR next_attempt_at<=?)
                ORDER BY salience_hint DESC,occurred_at ASC
                LIMIT ?
                """,
                (EventStatus.PENDING.value, now, limit),
            ).fetchall()
            events: list[Event] = []
            for row in rows:
                updated = connection.execute(
                    """
                    UPDATE events
                    SET status=?,reserved_by=?,reserved_at=?,attempts=attempts+1
                    WHERE event_id=? AND status=?
                      AND (next_attempt_at IS NULL OR next_attempt_at<=?)
                    """,
                    (
                        EventStatus.RESERVED.value,
                        worker_id,
                        now,
                        row["event_id"],
                        EventStatus.PENDING.value,
                        now,
                    ),
                ).rowcount
                if updated == 1:
                    events.append(
                        self._row_to_event(
                            row,
                            status=EventStatus.RESERVED,
                            attempts=int(row["attempts"]) + 1,
                        )
                    )
            return events

    def fail(
        self,
        event_id: str,
        error: str,
        max_attempts: int = 5,
        base_backoff_seconds: float = 2.0,
        max_backoff_seconds: float = 300.0,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if base_backoff_seconds < 0 or max_backoff_seconds < 0:
            raise ValueError("event backoff must be non-negative")
        now = datetime.now(UTC)
        with self.db.transaction() as connection:
            row = connection.execute(
                "SELECT attempts,status FROM events WHERE event_id=?",
                (event_id,),
            ).fetchone()
            if row is None:
                return
            attempts = int(row["attempts"])
            dead = attempts >= max_attempts
            if dead:
                status = EventStatus.DEAD_LETTER
                next_attempt_at = None
                dead_lettered_at = now.isoformat()
                delay_seconds = None
            else:
                status = EventStatus.PENDING
                delay_seconds = min(
                    max_backoff_seconds,
                    base_backoff_seconds * (2 ** max(0, attempts - 1)),
                )
                next_attempt_at = (
                    now + timedelta(seconds=delay_seconds)
                ).isoformat()
                dead_lettered_at = None
            connection.execute(
                """
                UPDATE events
                SET status=?,reserved_by=NULL,reserved_at=NULL,last_error=?,
                    last_failed_at=?,next_attempt_at=?,dead_lettered_at=?
                WHERE event_id=?
                """,
                (
                    status.value,
                    error[:4000],
                    now.isoformat(),
                    next_attempt_at,
                    dead_lettered_at,
                    event_id,
                ),
            )
            self.ledger.append(
                "event_retry_scheduled" if not dead else "event_dead_lettered",
                {
                    "event_id": event_id,
                    "status": status.value,
                    "attempts": attempts,
                    "max_attempts": max_attempts,
                    "next_attempt_at": next_attempt_at,
                    "delay_seconds": delay_seconds,
                    "error": error[:1000],
                },
                connection,
            )

    def recover_stale_reservations(self, stale_before: str) -> int:
        now = utc_now()
        with self.db.transaction() as connection:
            rows = connection.execute(
                """
                SELECT event_id FROM events
                WHERE status=? AND reserved_at<?
                """,
                (EventStatus.RESERVED.value, stale_before),
            ).fetchall()
            if not rows:
                return 0
            event_ids = [str(row["event_id"]) for row in rows]
            connection.execute(
                """
                UPDATE events
                SET status=?,reserved_by=NULL,reserved_at=NULL,
                    next_attempt_at=COALESCE(next_attempt_at,?)
                WHERE status=? AND reserved_at<?
                """,
                (
                    EventStatus.PENDING.value,
                    now,
                    EventStatus.RESERVED.value,
                    stale_before,
                ),
            )
            self.ledger.append(
                "stale_event_reservations_recovered",
                {"event_ids": event_ids, "count": len(event_ids)},
                connection,
            )
            return len(event_ids)

    def reliability_status(self) -> dict[str, Any]:
        now = utc_now()
        due = self.db.query_one(
            """
            SELECT COUNT(*) AS n FROM events
            WHERE status='PENDING'
              AND (next_attempt_at IS NULL OR next_attempt_at<=?)
            """,
            (now,),
        )
        deferred = self.db.query_one(
            """
            SELECT COUNT(*) AS n FROM events
            WHERE status='PENDING' AND next_attempt_at>?
            """,
            (now,),
        )
        dead = self.db.query_one(
            "SELECT COUNT(*) AS n FROM events WHERE status='DEAD_LETTER'"
        )
        return {
            "due": int(due["n"]) if due else 0,
            "deferred": int(deferred["n"]) if deferred else 0,
            "dead_letter": int(dead["n"]) if dead else 0,
        }
