from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from .schemas import utc_now, new_id


class RoundStatus(StrEnum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"
    OWNER_REVIEW = "OWNER_REVIEW"
    ROLLED_BACK = "ROLLED_BACK"


@dataclass(slots=True)
class RoundDefinition:
    round_id: str
    title: str
    description: str
    entry_conditions: list[str]
    success_criteria: list[str]
    automation_level: int = 2
    owner_gate_required: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


class CampaignRunner:
    """Orchestrator for the 30-round life campaign."""

    def __init__(self, db: Any, ledger: Any):
        self.db = db
        self.ledger = ledger
        self.rounds: dict[str, RoundDefinition] = {}
        self._initialize_tables()

    def _initialize_tables(self) -> None:
        self.db.execute(\"\"\"
            CREATE TABLE IF NOT EXISTS campaign_rounds (
                round_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                automation_level INTEGER NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                verdict TEXT,
                evidence_ids_json TEXT NOT NULL,
                blocked_reason TEXT,
                repair_history_json TEXT NOT NULL
            )
        \"\"\")

    def register_round(self, definition: RoundDefinition) -> None:
        self.rounds[definition.round_id] = definition
        self.db.execute(
            \"\"\"
            INSERT OR IGNORE INTO campaign_rounds(
                round_id, status, automation_level, evidence_ids_json, repair_history_json
            ) VALUES (?, ?, ?, '[]', '[]')
            \"\"\",
            (definition.round_id, RoundStatus.PENDING.value, definition.automation_level)
        )

    def start_round(self, round_id: str) -> None:
        if round_id not in self.rounds:
            raise KeyError(f"unknown round: {round_id}")
            
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE campaign_rounds SET status=?, started_at=? WHERE round_id=?",
                (RoundStatus.ACTIVE.value, utc_now(), round_id)
            )
            self.ledger.append(
                "campaign_round_started",
                {"round_id": round_id},
                connection
            )

    def complete_round(self, round_id: str, status: RoundStatus, evidence_id: str | None = None, reason: str | None = None) -> None:
        with self.db.transaction() as connection:
            row = connection.execute(
                "SELECT evidence_ids_json FROM campaign_rounds WHERE round_id=?",
                (round_id,)
            ).fetchone()
            evidence_ids = json.loads(row["evidence_ids_json"]) if row else []
            if evidence_id:
                evidence_ids.append(evidence_id)
                
            connection.execute(
                \"\"\"
                UPDATE campaign_rounds 
                SET status=?, finished_at=?, verdict=?, evidence_ids_json=?, blocked_reason=?
                WHERE round_id=?
                \"\"\",
                (status.value, utc_now(), status.value, json.dumps(evidence_ids), reason, round_id)
            )
            self.ledger.append(
                "campaign_round_completed",
                {"round_id": round_id, "status": status.value, "reason": reason},
                connection
            )

