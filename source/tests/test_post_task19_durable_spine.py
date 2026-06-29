from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from wls.cycle_journal import CycleJournal
from wls.db import Database
from wls.durable_commands import DurableCommandQueue
from wls.replay_manifest import ReplayManifestStore
from wls.runtime_trace import RuntimeTraceStore
from wls.schemas import utc_now


class RecordingLedger:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def append(
        self,
        event_type: str,
        payload: dict[str, Any],
        connection: Any | None = None,
    ) -> str:
        self.events.append((event_type, payload))
        return f"evidence-{len(self.events)}"


def _database(tmp_path: Path) -> Database:
    return Database(tmp_path / "state.db")


def test_command_idempotency_contract_is_immutable(tmp_path: Path) -> None:
    queue = DurableCommandQueue(_database(tmp_path), RecordingLedger())
    first = queue.enqueue(
        "inspect",
        {"path": "sandbox"},
        idempotency_key="inspect:sandbox",
    )
    second = queue.enqueue(
        "inspect",
        {"path": "sandbox"},
        idempotency_key="inspect:sandbox",
    )
    assert first == second
    with pytest.raises(ValueError, match="different command contract"):
        queue.enqueue(
            "inspect",
            {"path": "other"},
            idempotency_key="inspect:sandbox",
        )


def test_command_claim_success_and_wrong_lease_rejection(tmp_path: Path) -> None:
    queue = DurableCommandQueue(_database(tmp_path), RecordingLedger())
    command_id = queue.enqueue("noop", {}, idempotency_key="noop:1")
    claim = queue.claim("worker-1")
    assert claim is not None
    assert claim.command_id == command_id
    with pytest.raises(RuntimeError, match="not owned"):
        queue.succeed(command_id, "wrong-token", {"ok": True})
    result = queue.succeed(command_id, claim.lease_token, {"ok": True})
    assert result["status"] == "SUCCEEDED"
    assert queue.claim("worker-1") is None
    assert queue.integrity()[0]


def test_command_retry_reaches_dead_letter(tmp_path: Path) -> None:
    queue = DurableCommandQueue(_database(tmp_path), RecordingLedger())
    command_id = queue.enqueue(
        "bounded-work",
        {},
        idempotency_key="bounded-work:1",
        max_attempts=2,
    )
    first = queue.claim("worker")
    assert first is not None
    assert queue.fail(
        command_id,
        first.lease_token,
        "first failure",
        backoff_seconds=0,
    )["status"] == "RETRY_WAIT"
    second = queue.claim("worker")
    assert second is not None
    assert queue.fail(
        command_id,
        second.lease_token,
        "second failure",
        backoff_seconds=0,
    )["status"] == "DEAD_LETTER"
    assert queue.integrity()[0]


def test_owner_interrupt_approval_resumes_same_command(tmp_path: Path) -> None:
    queue = DurableCommandQueue(_database(tmp_path), RecordingLedger())
    command_id = queue.enqueue("reviewed", {}, idempotency_key="reviewed:1")
    claim = queue.claim("worker")
    assert claim is not None
    interrupt_id = queue.interrupt(
        command_id,
        claim.lease_token,
        {"question": "approve?"},
    )
    assert queue.get(command_id)["status"] == "WAITING_OWNER"
    assert queue.decide(
        interrupt_id,
        approved=True,
        decided_by="owner",
    )["status"] == "PENDING"
    resumed = queue.claim("worker")
    assert resumed is not None
    assert resumed.command_id == command_id


def test_owner_rejection_is_terminal_and_immutable(tmp_path: Path) -> None:
    queue = DurableCommandQueue(_database(tmp_path), RecordingLedger())
    command_id = queue.enqueue("reviewed", {}, idempotency_key="reviewed:2")
    claim = queue.claim("worker")
    assert claim is not None
    interrupt_id = queue.interrupt(command_id, claim.lease_token, {"risk": "write"})
    assert queue.decide(
        interrupt_id,
        approved=False,
        decided_by="owner",
    )["status"] == "CANCELLED"
    with pytest.raises(ValueError, match="immutable"):
        queue.decide(interrupt_id, approved=True, decided_by="owner")


def test_trace_parentage_events_and_terminal_boundary(tmp_path: Path) -> None:
    trace = RuntimeTraceStore(_database(tmp_path), RecordingLedger())
    parent = trace.start("cycle", cycle_id="cycle-1")
    child = trace.start(
        "action",
        parent_span_id=parent["span_id"],
        action_id="action-1",
    )
    trace.event(child["span_id"], "tool.called", {"tool": "noop"})
    ended = trace.end(
        child["span_id"],
        status="OK",
        attributes={"accepted": True},
    )
    assert ended["trace_id"] == parent["trace_id"]
    assert ended["events"][0]["name"] == "tool.called"
    assert ended["attributes"]["accepted"] is True
    with pytest.raises(RuntimeError, match="terminal span"):
        trace.event(child["span_id"], "late-event")
    trace.end(parent["span_id"])
    assert trace.integrity()[0]


def test_trace_rejects_cross_trace_parenting(tmp_path: Path) -> None:
    trace = RuntimeTraceStore(_database(tmp_path), RecordingLedger())
    parent = trace.start("parent", trace_id="trace-a")
    with pytest.raises(ValueError, match="match parent"):
        trace.start(
            "child",
            trace_id="trace-b",
            parent_span_id=parent["span_id"],
        )


def _checkpointed_database(tmp_path: Path) -> tuple[Database, RecordingLedger, str]:
    db = _database(tmp_path)
    ledger = RecordingLedger()
    journal = CycleJournal(db, ledger)
    cycle_id = "cycle-replay-source"
    db.execute(
        "INSERT INTO cycles(cycle_id,started_at,status) VALUES (?,?,?)",
        (cycle_id, utc_now(), "RUNNING"),
    )
    checkpoint_id = journal.record(
        cycle_id,
        "PLAN_PERSISTED",
        {"plan_id": "plan-1", "action_ids": []},
    )
    return db, ledger, checkpoint_id


def test_read_only_replay_manifest_lifecycle(tmp_path: Path) -> None:
    db, ledger, checkpoint_id = _checkpointed_database(tmp_path)
    store = ReplayManifestStore(db, ledger)
    replay_id = store.create(
        "cycle-replay-source",
        checkpoint_id,
        mode="READ_ONLY",
    )
    assert store.get(replay_id)["status"] == "PROPOSED"
    assert store.approve(replay_id)["status"] == "APPROVED"
    assert store.mark_consumed(replay_id)["status"] == "CONSUMED"
    assert store.integrity()[0]


def test_read_only_replay_rejects_overrides(tmp_path: Path) -> None:
    db, ledger, checkpoint_id = _checkpointed_database(tmp_path)
    store = ReplayManifestStore(db, ledger)
    with pytest.raises(ValueError, match="cannot contain overrides"):
        store.create(
            "cycle-replay-source",
            checkpoint_id,
            mode="READ_ONLY",
            overrides={"provider": "different"},
        )


def test_replay_checkpoint_membership_is_exact(tmp_path: Path) -> None:
    db, ledger, checkpoint_id = _checkpointed_database(tmp_path)
    db.execute(
        "INSERT INTO cycles(cycle_id,started_at,status) VALUES (?,?,?)",
        ("different-cycle", utc_now(), "RUNNING"),
    )
    store = ReplayManifestStore(db, ledger)
    with pytest.raises(ValueError, match="does not belong"):
        store.create("different-cycle", checkpoint_id)
