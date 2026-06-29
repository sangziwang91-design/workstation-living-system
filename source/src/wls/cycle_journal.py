from __future__ import annotations

from typing import Any
import json

from .db import Database
from .evidence import EvidenceLedger
from .schemas import digest_json, new_id, utc_now


PHASE_ORDER = {
    "PLAN_PERSISTED": 10,
    "ACTIONS_TERMINAL": 20,
    "RECOVERY_RESOLVED": 30,
}


class CycleJournal:
    """Durable phase history over canonical cycles, plans, and actions."""

    def __init__(self, db: Database, ledger: EvidenceLedger) -> None:
        self.db = db
        self.ledger = ledger
        with db.transaction() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS cycle_checkpoints(
                    checkpoint_id TEXT PRIMARY KEY,
                    cycle_id TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    phase_order INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    payload_digest TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(cycle_id,phase),
                    FOREIGN KEY(cycle_id) REFERENCES cycles(cycle_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS cycle_recoveries(
                    cycle_id TEXT PRIMARY KEY,
                    plan_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    resolved_at TEXT,
                    FOREIGN KEY(cycle_id) REFERENCES cycles(cycle_id),
                    FOREIGN KEY(plan_id) REFERENCES plans(plan_id)
                )
                """
            )

    def record(
        self,
        cycle_id: str,
        phase: str,
        payload: dict[str, Any],
        connection: Any | None = None,
    ) -> str:
        if phase not in PHASE_ORDER:
            raise ValueError(f"unknown cycle phase: {phase}")
        normalized = json.loads(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        payload_digest = digest_json(normalized)
        if connection is None:
            with self.db.transaction() as owned:
                return self.record(cycle_id, phase, normalized, owned)
        existing = connection.execute(
            "SELECT checkpoint_id,payload_digest FROM cycle_checkpoints WHERE cycle_id=? AND phase=?",
            (cycle_id, phase),
        ).fetchone()
        if existing is not None:
            if str(existing["payload_digest"]) != payload_digest:
                raise ValueError(f"immutable checkpoint changed: {cycle_id}/{phase}")
            return str(existing["checkpoint_id"])
        latest = connection.execute(
            "SELECT phase_order FROM cycle_checkpoints WHERE cycle_id=? ORDER BY phase_order DESC LIMIT 1",
            (cycle_id,),
        ).fetchone()
        order = PHASE_ORDER[phase]
        if latest is not None and order < int(latest["phase_order"]):
            raise ValueError(f"cycle checkpoint regression for {cycle_id}")
        checkpoint_id = new_id("checkpoint")
        connection.execute(
            """
            INSERT INTO cycle_checkpoints(
                checkpoint_id,cycle_id,phase,phase_order,payload_json,
                payload_digest,created_at
            ) VALUES (?,?,?,?,?,?,?)
            """,
            (
                checkpoint_id,
                cycle_id,
                phase,
                order,
                json.dumps(normalized, ensure_ascii=False, sort_keys=True),
                payload_digest,
                utc_now(),
            ),
        )
        self.ledger.append(
            "cycle_checkpoint_recorded",
            {
                "checkpoint_id": checkpoint_id,
                "cycle_id": cycle_id,
                "phase": phase,
                "payload_digest": payload_digest,
            },
            connection,
        )
        return checkpoint_id
