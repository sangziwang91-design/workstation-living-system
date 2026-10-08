from __future__ import annotations

from typing import Any
import json

from .db import Database
from .evidence import EvidenceLedger
from .schemas import ActionResult, ActionSpec, utc_now


class SelfModel:
    """Evidence-bound model of capabilities, limits, permissions, and commitments."""

    def __init__(self, db: Database, ledger: EvidenceLedger):
        self.db = db
        self.ledger = ledger

    def set(
        self,
        key: str,
        value: Any,
        confidence: float,
        evidence_ids: list[str],
        connection=None,
    ) -> None:
        if not evidence_ids:
            raise ValueError("self-model updates require evidence")
        confidence = max(0.0, min(1.0, confidence))
        if connection is None:
            with self.db.transaction() as owned_connection:
                self.set(key, value, confidence, evidence_ids, owned_connection)
            return
        connection.execute(
            """
            INSERT INTO self_model(key,value_json,confidence,evidence_ids_json,updated_at)
            VALUES (?,?,?,?,?)
            ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,
                confidence=excluded.confidence,evidence_ids_json=excluded.evidence_ids_json,
                updated_at=excluded.updated_at
            """,
            (
                key,
                json.dumps(value, ensure_ascii=False, sort_keys=True),
                confidence,
                json.dumps(evidence_ids, ensure_ascii=False),
                utc_now(),
            ),
        )
        self.ledger.append(
            "self_model_updated",
            {
                "key": key,
                "value": value,
                "confidence": confidence,
                "evidence_ids": evidence_ids,
            },
            connection,
        )

    def snapshot(self) -> dict[str, Any]:
        rows = self.db.query_all("SELECT * FROM self_model ORDER BY key")
        return {
            row["key"]: {
                "value": json.loads(row["value_json"]),
                "confidence": float(row["confidence"]),
                "evidence_ids": json.loads(row["evidence_ids_json"]),
                "updated_at": row["updated_at"],
            }
            for row in rows
        }

    def record_action_outcome(
        self,
        action: ActionSpec,
        result: ActionResult,
        evidence_id: str,
        connection=None,
    ) -> None:
        key = f"capability.tool.{action.tool}"
        if connection is None:
            row = self.db.query_one("SELECT * FROM self_model WHERE key=?", (key,))
        else:
            row = connection.execute(
                "SELECT * FROM self_model WHERE key=?", (key,)
            ).fetchone()
        current: dict[str, Any] = (
            {
                "value": json.loads(row["value_json"]),
                "confidence": float(row["confidence"]),
            }
            if row is not None
            else {"value": {"successes": 0, "failures": 0}, "confidence": 0.5}
        )
        raw_value = current["value"]
        value = (
            dict(raw_value)
            if isinstance(raw_value, dict)
            else {"successes": 0, "failures": 0}
        )
        value["successes"] = int(value.get("successes", 0)) + int(result.success)
        value["failures"] = int(value.get("failures", 0)) + int(not result.success)
        total = value["successes"] + value["failures"]
        value["observed_success_rate"] = value["successes"] / max(1, total)
        confidence = min(0.98, 0.35 + 0.08 * total)
        self.set(key, value, confidence, [evidence_id], connection)

    def record_owner_outcome(
        self,
        *,
        action: dict[str, Any],
        outcome: str,
        evidence_id: str,
        connection=None,
    ) -> dict[str, Any]:
        key = self.owner_capability_key(action)
        if connection is None:
            row = self.db.query_one("SELECT * FROM self_model WHERE key=?", (key,))
        else:
            row = connection.execute(
                "SELECT * FROM self_model WHERE key=?", (key,)
            ).fetchone()
        raw_value = json.loads(row["value_json"]) if row is not None else None
        value: dict[str, Any] = (
            dict(raw_value)
            if isinstance(raw_value, dict)
            else {
                "helped": 0,
                "failed": 0,
                "avoid": 0,
                "neutral": 0,
                "tool": action.get("tool") or action.get("recommended_tool"),
                "risk_class": action.get("risk_class"),
            }
        )
        normalized = str(outcome).lower()
        if normalized not in {"helped", "failed", "avoid", "neutral"}:
            normalized = "neutral"
        value[normalized] = int(value.get(normalized, 0)) + 1
        helped = int(value.get("helped", 0))
        failed = int(value.get("failed", 0))
        avoid = int(value.get("avoid", 0))
        total = helped + failed + avoid + int(value.get("neutral", 0))
        capability_confidence = helped / max(1, helped + failed + avoid)
        should_defer = avoid > 0 or (helped + failed >= 2 and capability_confidence < 0.5)
        value.update(
            {
                "owner_observation_count": total,
                "capability_confidence": round(capability_confidence, 4),
                "should_defer": should_defer,
                "defer_reason": (
                    "owner_avoid_feedback"
                    if avoid > 0
                    else (
                        "owner_feedback_success_rate_below_threshold"
                        if should_defer
                        else ""
                    )
                ),
                "last_outcome": normalized,
            }
        )
        calibration_confidence = min(0.98, 0.35 + 0.12 * total)
        self.set(key, value, calibration_confidence, [evidence_id], connection)
        return {"key": key, "value": value, "confidence": calibration_confidence}

    def readiness_for_action(self, action: dict[str, Any]) -> dict[str, Any]:
        key = self.owner_capability_key(action)
        row = self.db.query_one("SELECT * FROM self_model WHERE key=?", (key,))
        if row is None:
            return {
                "key": key,
                "attempt_allowed": True,
                "reason": "no owner outcome calibration for this action class yet",
                "capability_confidence": None,
            }
        value = json.loads(row["value_json"])
        should_defer = bool(value.get("should_defer", False))
        return {
            "key": key,
            "attempt_allowed": not should_defer,
            "reason": str(value.get("defer_reason") or "owner calibration permits attempt"),
            "capability_confidence": value.get("capability_confidence"),
            "owner_observation_count": value.get("owner_observation_count", 0),
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def owner_capability_key(action: dict[str, Any]) -> str:
        tool = str(action.get("tool") or action.get("recommended_tool") or "none")
        risk_class = str(action.get("risk_class") or action.get("risk") or "unknown")
        return f"capability.owner_outcome.{tool}.{risk_class}".lower()

    def initialize_identity(self, name: str, evidence_id: str) -> None:
        if "identity" not in self.snapshot():
            self.set(
                "identity",
                {
                    "name": name,
                    "type": "bounded functional software-life runtime",
                    "subjective_consciousness": "UNKNOWN",
                    "genuine_emotion": "UNKNOWN",
                    "core_constraint": "preserve evidence, reality binding, human authority, and reversible growth",
                },
                1.0,
                [evidence_id],
            )
