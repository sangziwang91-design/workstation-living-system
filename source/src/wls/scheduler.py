from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .db import Database
from .schemas import Event, digest_json, new_id, utc_now
from .stores import EventStore


@dataclass(slots=True)
class ScheduledEvent:
    schedule_id: str
    event_type: str
    payload: dict[str, Any]
    due_at: str
    source: str = "scheduler"
    side_effect_class: str = "none"
    emitted_at: str = field(default_factory=utc_now)


class EventScheduler:
    """Scheduler organ that emits Events, never Actions."""

    def emit_due(self, item: ScheduledEvent) -> Event:
        if item.side_effect_class != "none":
            raise PermissionError("scheduler may only emit read-only events")
        return Event(
            event_type=item.event_type,
            source=item.source,
            payload={
                "schedule_id": item.schedule_id,
                "due_at": item.due_at,
                "payload": item.payload,
            },
            salience_hint=0.55,
            occurred_at=item.emitted_at,
            dedupe_key=digest_json(
                {
                    "schedule_id": item.schedule_id,
                    "due_at": item.due_at,
                    "payload": item.payload,
                }
            ),
        )

    def submit_due(self, item: ScheduledEvent, events: EventStore) -> tuple[str, bool]:
        return events.add_event(self.emit_due(item))


class PersistentScheduler:
    """DB-backed scheduler with persistent schedules, missed-run detection,
    backoff, max-runs enforcement, and restart recovery."""

    def __init__(self, db: Database, events: EventStore) -> None:
        self.db = db
        self.events = events
        self._emitter = EventScheduler()

    def create(
        self,
        name: str,
        event_type: str,
        *,
        payload: dict[str, Any] | None = None,
        cron_seconds: float | None = None,
        max_runs: int | None = None,
        max_consecutive_failures: int = 3,
        backoff_seconds: float = 1.0,
        backoff_max_seconds: float = 60.0,
    ) -> str:
        schedule_id = new_id("sched")
        import json
        from datetime import UTC, datetime, timedelta

        now = datetime.now(UTC)
        next_due = now.isoformat()
        if cron_seconds is not None:
            next_due = (now + timedelta(seconds=cron_seconds)).isoformat()

        self.db.execute(
            """INSERT INTO schedules(
                schedule_id, name, event_type, payload_json, cron_seconds,
                next_due_at, status, max_runs, max_consecutive_failures,
                backoff_seconds, backoff_max_seconds, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?)""",
            (
                schedule_id,
                name,
                event_type,
                json.dumps(payload or {}, ensure_ascii=False),
                cron_seconds,
                next_due,
                max_runs,
                max_consecutive_failures,
                backoff_seconds,
                backoff_max_seconds,
                utc_now(),
                utc_now(),
            ),
        )
        return schedule_id

    def pause(self, schedule_id: str) -> None:
        self.db.execute(
            "UPDATE schedules SET status='paused', updated_at=? WHERE schedule_id=?",
            (utc_now(), schedule_id),
        )

    def resume(self, schedule_id: str) -> None:
        from datetime import UTC, datetime

        now = datetime.now(UTC)
        self.db.execute(
            "UPDATE schedules SET status='active', next_due_at=?, updated_at=? WHERE schedule_id=?",
            (now.isoformat(), utc_now(), schedule_id),
        )

    def cancel(self, schedule_id: str) -> None:
        self.db.execute(
            "UPDATE schedules SET status='cancelled', updated_at=? WHERE schedule_id=?",
            (utc_now(), schedule_id),
        )

    def emit_due_events(self, *, max_events: int = 10) -> int:
        rows = self.db.query_all(
            """SELECT * FROM schedules
               WHERE status='active' AND next_due_at <= ?
               ORDER BY next_due_at ASC LIMIT ?""",
            (utc_now(), max_events),
        )
        count = 0
        for row in rows:
            schedule_id = str(row["schedule_id"])
            event_type = str(row["event_type"])
            import json as _json
            payload = _json.loads(str(row["payload_json"]))
            cron_seconds = row["cron_seconds"]
            run_count = int(row["run_count"])
            max_runs = row["max_runs"]
            max_failures = int(row["max_consecutive_failures"])
            backoff = float(row["backoff_seconds"])
            backoff_max = float(row["backoff_max_seconds"])
            current_failures = int(row["consecutive_failures"])

            if max_runs is not None and run_count >= max_runs:
                self.db.execute(
                    "UPDATE schedules SET status='completed', updated_at=? WHERE schedule_id=?",
                    (utc_now(), schedule_id),
                )
                continue

            if current_failures >= max_failures:
                continue

            item = ScheduledEvent(
                schedule_id=schedule_id,
                event_type=event_type,
                payload=payload,
                due_at=str(row["next_due_at"]),
            )
            event_id, admitted = self._emitter.submit_due(item, self.events)
            if admitted:
                count += 1
                self.db.execute(
                    """UPDATE schedules SET
                       last_emitted_at=?, last_emitted_event_id=?,
                       run_count=run_count+1, consecutive_failures=0,
                       backoff_seconds=1.0, updated_at=?
                       WHERE schedule_id=?""",
                    (utc_now(), event_id, utc_now(), schedule_id),
                )
            else:
                new_failures = current_failures + 1
                new_backoff = min(backoff * 2, backoff_max)
                self.db.execute(
                    """UPDATE schedules SET
                       consecutive_failures=?, backoff_seconds=?,
                       updated_at=? WHERE schedule_id=?""",
                    (new_failures, new_backoff, utc_now(), schedule_id),
                )
            self._reschedule_next(schedule_id, cron_seconds)
        return count

    def recover_stale_schedules(self) -> int:
        rows = self.db.query_all(
            "SELECT schedule_id, cron_seconds FROM schedules WHERE status IN ('active','paused')"
        )
        recovered = 0
        for row in rows:
            self._reschedule_next(str(row["schedule_id"]), row["cron_seconds"])
            recovered += 1
        return recovered

    def get_schedule(self, schedule_id: str) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT * FROM schedules WHERE schedule_id=?", (schedule_id,)
        )
        if row is None:
            return None
        return dict(row)

    def list_schedules(self, *, status: str | None = None) -> list[dict[str, Any]]:
        if status:
            rows = self.db.query_all(
                "SELECT * FROM schedules WHERE status=? ORDER BY next_due_at ASC",
                (status,),
            )
        else:
            rows = self.db.query_all("SELECT * FROM schedules ORDER BY next_due_at ASC")
        return [dict(r) for r in rows]

    def _reschedule_next(self, schedule_id: str, cron_seconds: float | None) -> None:
        if cron_seconds is None:
            self.db.execute(
                "UPDATE schedules SET status='completed', updated_at=? WHERE schedule_id=?",
                (utc_now(), schedule_id),
            )
            return
        from datetime import UTC, datetime, timedelta

        now = datetime.now(UTC)
        next_due = (now + timedelta(seconds=cron_seconds)).isoformat()
        self.db.execute(
            "UPDATE schedules SET next_due_at=?, updated_at=? WHERE schedule_id=?",
            (next_due, utc_now(), schedule_id),
        )
