from __future__ import annotations

import pytest

from wls.db import Database
from wls.evidence import EvidenceLedger
from wls.scheduler import EventScheduler, PersistentScheduler, ScheduledEvent
from wls.stores import EventStore


@pytest.fixture
def tmp_db(tmp_path):
    db_path = tmp_path / "test.db"
    return Database(db_path)


@pytest.fixture
def tmp_events(tmp_db, tmp_path):
    secret_path = tmp_path / "secret.key"
    ledger = EvidenceLedger(db=tmp_db, secret_path=secret_path)
    return EventStore(tmp_db, ledger)


@pytest.fixture
def ps(tmp_db, tmp_events):
    return PersistentScheduler(tmp_db, tmp_events)


class TestEventScheduler:
    def test_emit_due_returns_event(self):
        s = EventScheduler()
        item = ScheduledEvent(
            schedule_id="s1", event_type="test.run", payload={}, due_at="2026-01-01T00:00:00Z"
        )
        event = s.emit_due(item)
        assert event.event_type == "test.run"
        assert event.source == "scheduler"
        assert event.payload["schedule_id"] == "s1"

    def test_reject_side_effect(self):
        s = EventScheduler()
        item = ScheduledEvent(
            schedule_id="s1",
            event_type="bad",
            payload={},
            due_at="2026-01-01T00:00:00Z",
            side_effect_class="write",
        )
        with pytest.raises(PermissionError):
            s.emit_due(item)


class TestPersistentScheduler:
    def test_create_schedule(self, ps):
        sid = ps.create("test_sched", "test.tick", cron_seconds=60.0)
        s = ps.get_schedule(sid)
        assert s is not None
        assert s["name"] == "test_sched"
        assert s["event_type"] == "test.tick"
        assert s["status"] == "active"

    def test_pause_and_resume(self, ps):
        sid = ps.create("s", "t", cron_seconds=60.0)
        ps.pause(sid)
        assert ps.get_schedule(sid)["status"] == "paused"
        ps.resume(sid)
        assert ps.get_schedule(sid)["status"] == "active"

    def test_cancel_schedule(self, ps):
        sid = ps.create("s", "t", cron_seconds=60.0)
        ps.cancel(sid)
        assert ps.get_schedule(sid)["status"] == "cancelled"

    def test_emit_due_emits_event(self, ps, tmp_events):
        from datetime import UTC, datetime, timedelta

        sid = ps.create("s", "test.tick", cron_seconds=60.0)
        past = (datetime.now(UTC) - timedelta(seconds=10)).isoformat()
        ps.db.execute(
            "UPDATE schedules SET next_due_at=? WHERE schedule_id=?", (past, sid)
        )
        count = ps.emit_due_events()
        assert count == 1
        s = ps.get_schedule(sid)
        assert s["run_count"] == 1
        assert s["last_emitted_event_id"] is not None

    def test_single_run_completes(self, ps):
        from datetime import UTC, datetime, timedelta

        sid = ps.create("oneshot", "test.ping", cron_seconds=None, max_runs=1)
        past = (datetime.now(UTC) - timedelta(seconds=5)).isoformat()
        ps.db.execute(
            "UPDATE schedules SET next_due_at=? WHERE schedule_id=?", (past, sid)
        )
        count = ps.emit_due_events()
        assert count == 1
        s = ps.get_schedule(sid)
        assert s["status"] == "completed"

    def test_max_runs_limit(self, ps):
        from datetime import UTC, datetime, timedelta

        sid = ps.create("limited", "test.ping", cron_seconds=60.0, max_runs=2)
        past = (datetime.now(UTC) - timedelta(seconds=5)).isoformat()
        ps.db.execute(
            "UPDATE schedules SET run_count=2, next_due_at=? WHERE schedule_id=?",
            (past, sid),
        )
        count = ps.emit_due_events()
        assert count == 0
        assert ps.get_schedule(sid)["status"] == "completed"

    def test_consecutive_failures_block(self, ps):
        sid = ps.create("flaky", "test.fail", cron_seconds=60.0, max_consecutive_failures=2)
        ps.db.execute(
            "UPDATE schedules SET consecutive_failures=2 WHERE schedule_id=?", (sid,)
        )
        count = ps.emit_due_events()
        assert count == 0

    def test_list_schedules(self, ps):
        ps.create("a", "t1", cron_seconds=30.0)
        ps.create("b", "t2", cron_seconds=60.0)
        all_s = ps.list_schedules()
        assert len(all_s) == 2
        active = ps.list_schedules(status="active")
        assert len(active) == 2

    def test_recover_stale_schedules(self, ps):
        ps.create("stale", "t", cron_seconds=60.0)
        recovered = ps.recover_stale_schedules()
        assert recovered == 1

    def test_get_nonexistent(self, ps):
        assert ps.get_schedule("nonexistent") is None
