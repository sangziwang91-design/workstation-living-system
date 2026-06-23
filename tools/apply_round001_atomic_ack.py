from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if new in text:
        return
    if old not in text:
        raise SystemExit(f"expected baseline block not found in {path}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


runtime = ROOT / "source" / "src" / "wls" / "runtime.py"
replace_once(
    runtime,
    "            self.events.mark_processed(event_ids, cycle_id, connection)\n",
    "            self.events.mark_processed(\n"
    "                event_ids, cycle_id, connection, worker_id=self.worker_id\n"
    "            )\n",
)

stores = ROOT / "source" / "src" / "wls" / "stores.py"
old = '''    def mark_processed(
        self, event_ids: list[str], cycle_id: str, connection=None
    ) -> None:
        if not event_ids:
            return
        if connection is None:
            with self.db.transaction() as owned_connection:
                self.mark_processed(event_ids, cycle_id, owned_connection)
            return
        now = utc_now()
        for event_id in event_ids:
            connection.execute(
                """
                UPDATE events SET status=?, processed_at=?, reserved_by=NULL, reserved_at=NULL
                WHERE event_id=? AND status=?
                """,
                (
                    EventStatus.PROCESSED.value,
                    now,
                    event_id,
                    EventStatus.RESERVED.value,
                ),
            )
        self.ledger.append(
            "events_processed",
            {"cycle_id": cycle_id, "event_ids": event_ids},
            connection,
        )
'''
new = '''    def mark_processed(
        self,
        event_ids: list[str],
        cycle_id: str,
        connection=None,
        *,
        worker_id: str | None = None,
    ) -> None:
        """Acknowledge only events durably owned by the current cycle.

        Every requested event must transition from RESERVED to PROCESSED in the
        same transaction as the durable plan. A stale ID, duplicate ID, or event
        reserved by another worker aborts the transaction instead of emitting
        false evidence that the event was handled.
        """
        if not event_ids:
            return
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("event_ids must be unique")
        if connection is None:
            with self.db.transaction() as owned_connection:
                self.mark_processed(
                    event_ids,
                    cycle_id,
                    owned_connection,
                    worker_id=worker_id,
                )
            return
        now = utc_now()
        transitioned: list[str] = []
        for event_id in event_ids:
            if worker_id is None:
                cursor = connection.execute(
                    """
                    UPDATE events SET status=?, processed_at=?, reserved_by=NULL, reserved_at=NULL
                    WHERE event_id=? AND status=?
                    """,
                    (
                        EventStatus.PROCESSED.value,
                        now,
                        event_id,
                        EventStatus.RESERVED.value,
                    ),
                )
            else:
                cursor = connection.execute(
                    """
                    UPDATE events SET status=?, processed_at=?, reserved_by=NULL, reserved_at=NULL
                    WHERE event_id=? AND status=? AND reserved_by=?
                    """,
                    (
                        EventStatus.PROCESSED.value,
                        now,
                        event_id,
                        EventStatus.RESERVED.value,
                        worker_id,
                    ),
                )
            if cursor.rowcount != 1:
                raise RuntimeError(
                    f"event acknowledgement invariant failed for {event_id}"
                )
            transitioned.append(event_id)
        self.ledger.append(
            "events_processed",
            {
                "cycle_id": cycle_id,
                "event_ids": transitioned,
                "worker_id": worker_id,
            },
            connection,
        )
'''
replace_once(stores, old, new)

# The workflow removes this one-time helper after applying the patch.
print("Round 001 atomic acknowledgement patch applied.")
