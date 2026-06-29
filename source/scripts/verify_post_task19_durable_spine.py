from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
import json
import sys

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


def verify() -> dict[str, Any]:
    with TemporaryDirectory(prefix="wls-durable-spine-") as directory:
        db = Database(Path(directory) / "state.db")
        ledger = RecordingLedger()

        queue = DurableCommandQueue(db, ledger)
        command_id = queue.enqueue(
            "bounded-inspection",
            {"path": "sandbox"},
            idempotency_key="verify:bounded-inspection",
            max_attempts=2,
        )
        claim = queue.claim("verifier")
        assert claim is not None
        interrupt_id = queue.interrupt(
            command_id,
            claim.lease_token,
            {"question": "approve bounded inspection"},
        )
        assert queue.decide(
            interrupt_id,
            approved=True,
            decided_by="verifier-owner",
        )["status"] == "PENDING"
        resumed = queue.claim("verifier")
        assert resumed is not None
        assert queue.succeed(
            command_id,
            resumed.lease_token,
            {"accepted": True},
        )["status"] == "SUCCEEDED"

        dead_id = queue.enqueue(
            "bounded-failure",
            {},
            idempotency_key="verify:bounded-failure",
            max_attempts=1,
        )
        dead_claim = queue.claim("verifier")
        assert dead_claim is not None
        assert queue.fail(
            dead_id,
            dead_claim.lease_token,
            "expected verifier failure",
            backoff_seconds=0,
        )["status"] == "DEAD_LETTER"

        trace = RuntimeTraceStore(db, ledger)
        parent = trace.start("cycle", cycle_id="cycle-verifier")
        child = trace.start(
            "command",
            parent_span_id=parent["span_id"],
            command_id=command_id,
        )
        trace.event(child["span_id"], "command.completed")
        trace.end(child["span_id"], status="OK")
        trace.end(parent["span_id"], status="OK")

        journal = CycleJournal(db, ledger)
        cycle_id = "cycle-replay-verifier"
        db.execute(
            "INSERT INTO cycles(cycle_id,started_at,status) VALUES (?,?,?)",
            (cycle_id, utc_now(), "RUNNING"),
        )
        checkpoint_id = journal.record(
            cycle_id,
            "PLAN_PERSISTED",
            {"plan_id": "plan-verifier", "action_ids": []},
        )
        replay = ReplayManifestStore(db, ledger)
        replay_id = replay.create(cycle_id, checkpoint_id)
        replay.approve(replay_id, approved_by="verifier-owner")
        replay.mark_consumed(replay_id)

        queue_ok, queue_counts = queue.integrity()
        trace_ok, trace_counts = trace.integrity()
        replay_ok, replay_counts = replay.integrity()
        passed = queue_ok and trace_ok and replay_ok
        return {
            "schema_version": "1.0",
            "target": "POST_TASK19_DURABLE_SPINE",
            "passed": passed,
            "sandbox_only": True,
            "command_queue": queue_counts,
            "runtime_trace": trace_counts,
            "replay_manifest": replay_counts,
            "evidence_events": len(ledger.events),
            "claim_ceiling": (
                "Isolated SQLite state-machine verification only; not full WLS "
                "integration, owner-host, provider, action-execution, or production proof."
            ),
        }


def main() -> int:
    report = verify()
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
