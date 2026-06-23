from __future__ import annotations

from typing import Any
import json

from .db import Database
from .evidence import EvidenceLedger
from .schemas import AffectState, DriveState, Event, utc_now


class DriveSystem:
    """Homeostatic drives and observable functional affect.

    These variables are control signals, not claims of subjective feeling.
    """

    def __init__(self, db: Database, ledger: EvidenceLedger):
        self.db = db
        self.ledger = ledger

    def load(self) -> tuple[DriveState, AffectState]:
        drive_row = self.db.query_one(
            "SELECT state_json FROM drive_state WHERE singleton=1"
        )
        affect_row = self.db.query_one(
            "SELECT state_json FROM affect_state WHERE singleton=1"
        )
        drives = (
            DriveState(**json.loads(drive_row["state_json"]))
            if drive_row
            else DriveState()
        )
        affect = (
            AffectState(**json.loads(affect_row["state_json"]))
            if affect_row
            else AffectState()
        )
        return drives, affect

    def appraise(
        self,
        events: list[Event],
        outcomes: list[dict[str, Any]] | None = None,
        resource_snapshot: dict[str, Any] | None = None,
    ) -> tuple[DriveState, AffectState, dict[str, Any]]:
        drives, affect = self.load()
        outcomes = outcomes or []
        resource_snapshot = resource_snapshot or {}
        failure_count = sum(1 for item in outcomes if not item.get("success", False))
        success_count = sum(1 for item in outcomes if item.get("success", False))
        threat_count = 0
        novelty_count = 0
        social_positive = 0
        social_negative = 0
        for event in events:
            text = json.dumps(event.payload, ensure_ascii=False).lower()
            if any(
                token in text
                for token in (
                    "failed",
                    "error",
                    "unhealthy",
                    "stopped",
                    "deleted",
                    "threat",
                )
            ):
                threat_count += 1
            if event.event_type.startswith("observation."):
                novelty_count += 1
            if any(
                token in text for token in ("approved", "thanks", "accepted", "success")
            ):
                social_positive += 1
            if any(token in text for token in ("rejected", "correction", "disagree")):
                social_negative += 1

        drives.continuity += 0.02 * success_count - 0.05 * threat_count
        drives.reality_coherence += 0.02 * success_count - 0.06 * failure_count
        drives.curiosity += 0.03 * novelty_count - 0.02 * max(0, novelty_count - 10)
        drives.competence += 0.04 * success_count - 0.06 * failure_count
        drives.relationship_continuity += (
            0.03 * social_positive - 0.05 * social_negative
        )
        drives.safety += 0.01 * success_count - 0.05 * threat_count
        drives.value_output += 0.03 * success_count - 0.03 * failure_count

        disk_ratio = resource_snapshot.get("disk_used_ratio")
        memory_ratio = resource_snapshot.get("memory_used_ratio")
        if isinstance(disk_ratio, (int, float)):
            drives.resource_balance = 1.0 - max(0.0, min(1.0, float(disk_ratio)))
        if isinstance(memory_ratio, (int, float)):
            drives.resource_balance = min(
                drives.resource_balance, 1.0 - max(0.0, min(1.0, float(memory_ratio)))
            )
        drives.clamp()

        affect.valence = (
            0.7 * affect.valence
            + 0.12 * success_count
            - 0.16 * failure_count
            - 0.08 * threat_count
        )
        affect.arousal = (
            0.75 * affect.arousal + 0.08 * novelty_count + 0.12 * threat_count
        )
        affect.control = (
            0.75 * affect.control + 0.12 * success_count - 0.15 * failure_count
        )
        affect.curiosity = 0.65 * affect.curiosity + 0.35 * drives.curiosity
        affect.frustration = (
            0.7 * affect.frustration
            + 0.18 * failure_count
            + 0.08 * threat_count
            - 0.1 * success_count
        )
        affect.confidence = (
            0.8 * affect.confidence + 0.1 * success_count - 0.14 * failure_count
        )
        affect.fatigue = (
            0.85 * affect.fatigue + 0.015 * len(events) + 0.03 * len(outcomes)
        )
        affect.social_trust = (
            0.85 * affect.social_trust + 0.08 * social_positive - 0.12 * social_negative
        )
        affect.safety_tension = (
            0.75 * affect.safety_tension + 0.16 * threat_count + 0.1 * failure_count
        )
        if drives.resource_balance < 0.25:
            affect.fatigue += 0.2
            affect.control -= 0.1
        affect.clamp()

        metadata = {
            "failure_count": failure_count,
            "success_count": success_count,
            "threat_count": threat_count,
            "novelty_count": novelty_count,
            "social_positive": social_positive,
            "social_negative": social_negative,
        }
        self.save(drives, affect, metadata)
        return drives, affect, metadata

    def decay(self, rate: float = 0.05) -> tuple[DriveState, AffectState]:
        drives, affect = self.load()
        baseline_drives = DriveState()
        baseline_affect = AffectState()
        for name in drives.__dataclass_fields__:
            current = getattr(drives, name)
            target = getattr(baseline_drives, name)
            setattr(drives, name, current + rate * (target - current))
        for name in affect.__dataclass_fields__:
            current = getattr(affect, name)
            target = getattr(baseline_affect, name)
            setattr(affect, name, current + rate * (target - current))
        drives.clamp()
        affect.clamp()
        self.save(drives, affect, {"decay": rate})
        return drives, affect

    def behavior_budget(
        self, drives: DriveState, affect: AffectState, configured_max: int
    ) -> dict[str, Any]:
        safety_pressure = max(affect.safety_tension, 1.0 - drives.safety)
        resource_pressure = max(affect.fatigue, 1.0 - drives.resource_balance)
        action_multiplier = max(
            0.0, 1.0 - 0.7 * safety_pressure - 0.5 * resource_pressure
        )
        max_actions = min(
            configured_max, max(0, round(configured_max * action_multiplier))
        )
        exploration_budget = 0
        if affect.curiosity > 0.65 and affect.fatigue < 0.7 and safety_pressure < 0.6:
            exploration_budget = 1
        return {
            "max_actions": max_actions,
            "exploration_budget": exploration_budget,
            "require_extra_verification": safety_pressure > 0.55,
            "prefer_recovery": affect.frustration > 0.7,
            "pause_recommended": safety_pressure > 0.9 or resource_pressure > 0.95,
        }

    def save(
        self, drives: DriveState, affect: AffectState, metadata: dict[str, Any]
    ) -> None:
        with self.db.transaction() as connection:
            now = utc_now()
            connection.execute(
                """
                INSERT INTO drive_state(singleton,state_json,updated_at) VALUES(1,?,?)
                ON CONFLICT(singleton) DO UPDATE SET state_json=excluded.state_json,updated_at=excluded.updated_at
                """,
                (json.dumps(drives.to_dict(), sort_keys=True), now),
            )
            connection.execute(
                """
                INSERT INTO affect_state(singleton,state_json,updated_at) VALUES(1,?,?)
                ON CONFLICT(singleton) DO UPDATE SET state_json=excluded.state_json,updated_at=excluded.updated_at
                """,
                (json.dumps(affect.to_dict(), sort_keys=True), now),
            )
            self.ledger.append(
                "internal_state_updated",
                {
                    "drives": drives.to_dict(),
                    "affect": affect.to_dict(),
                    "metadata": metadata,
                },
                connection,
            )
