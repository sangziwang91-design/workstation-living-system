from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .schemas import Event, digest_json, utc_now


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
