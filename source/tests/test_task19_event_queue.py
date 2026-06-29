from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from wls.config import default_config
from wls.runtime import LivingSystem
from wls.schemas import Event


def _runtime(tmp_path: Path) -> LivingSystem:
    config = default_config(tmp_path / "home")
    config.sensors = []
    return LivingSystem(config)


def test_failed_event_is_deferred_before_retry(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    event = Event(event_type="test", source="test", payload={})
    runtime.ingest_event(event)
    reserved = runtime.events.reserve("worker", 1)
    assert [item.event_id for item in reserved] == [event.event_id]
    runtime.events.fail(
        event.event_id,
        "temporary",
        max_attempts=3,
        base_backoff_seconds=60,
    )
    assert runtime.events.reserve("worker", 1) == []
    status = runtime.events.reliability_status()
    assert status["deferred"] == 1
    assert status["dead_letter"] == 0


def test_event_moves_to_dead_letter_at_attempt_limit(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    event = Event(event_type="poison", source="test", payload={})
    runtime.ingest_event(event)
    runtime.events.reserve("worker", 1)
    runtime.events.fail(event.event_id, "fatal", max_attempts=1)
    row = runtime.db.query_one(
        "SELECT status,dead_lettered_at FROM events WHERE event_id=?",
        (event.event_id,),
    )
    assert row is not None
    assert row["status"] == "DEAD_LETTER"
    assert row["dead_lettered_at"]


def test_stale_reservation_returns_to_due_queue(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    event = Event(event_type="stale", source="test", payload={})
    runtime.ingest_event(event)
    runtime.events.reserve("old-worker", 1)
    stale_before = (datetime.now(UTC) + timedelta(seconds=1)).isoformat()
    assert runtime.events.recover_stale_reservations(stale_before) == 1
    reserved = runtime.events.reserve("new-worker", 1)
    assert [item.event_id for item in reserved] == [event.event_id]
    assert reserved[0].attempts == 2
