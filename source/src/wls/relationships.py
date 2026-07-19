from __future__ import annotations

from typing import Any
import json

from .db import Database
from .evidence import EvidenceLedger
from .schemas import new_id, utc_now
from .text import tokens


class RelationshipMemory:
    """Separates stable values from transient states and one-off instructions."""

    STABILITIES = {"transient", "working", "stable"}

    def __init__(self, db: Database, ledger: EvidenceLedger):
        self.db = db
        self.ledger = ledger

    def record(
        self,
        subject: str,
        relation_type: str,
        value: Any,
        stability: str,
        confidence: float,
        source_ids: list[str],
    ) -> str:
        if stability not in self.STABILITIES:
            raise ValueError(f"invalid stability: {stability}")
        if not source_ids:
            raise ValueError("relationship memory requires sources")
        if stability == "stable" and (len(set(source_ids)) < 2 or confidence < 0.8):
            raise ValueError(
                "stable relationship facts require at least two sources and confidence >= 0.8"
            )
        relation_id = new_id("rel")
        with self.db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO relationships(
                    relation_id,subject,relation_type,value_json,stability,confidence,
                    source_ids_json,updated_at,active
                ) VALUES (?,?,?,?,?,?,?,?,1)
                """,
                (
                    relation_id,
                    subject,
                    relation_type,
                    json.dumps(value, ensure_ascii=False, sort_keys=True),
                    stability,
                    max(0.0, min(1.0, confidence)),
                    json.dumps(source_ids, ensure_ascii=False),
                    utc_now(),
                ),
            )
            self.ledger.append(
                "relationship_recorded",
                {
                    "relation_id": relation_id,
                    "subject": subject,
                    "relation_type": relation_type,
                    "stability": stability,
                },
                connection,
            )
        return relation_id

    def snapshot(self, subject: str | None = None) -> list[dict[str, Any]]:
        if subject:
            rows = self.db.query_all(
                "SELECT * FROM relationships WHERE active=1 AND subject=? ORDER BY updated_at DESC",
                (subject,),
            )
        else:
            rows = self.db.query_all(
                "SELECT * FROM relationships WHERE active=1 ORDER BY updated_at DESC"
            )
        return [
            {
                "relation_id": row["relation_id"],
                "subject": row["subject"],
                "relation_type": row["relation_type"],
                "value": json.loads(row["value_json"]),
                "stability": row["stability"],
                "confidence": float(row["confidence"]),
                "source_ids": json.loads(row["source_ids_json"]),
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def query(self, text: str, limit: int = 20) -> list[dict[str, Any]]:
        query_tokens = tokens(text)
        rows = self.db.query_all("SELECT * FROM relationships WHERE active=1")
        scored: list[tuple[float, Any]] = []
        for row in rows:
            haystack = (
                f"{row['subject']} {row['relation_type']} "
                f"{row['value_json']} {row['source_ids_json']}"
            ).lower()
            overlap = len(query_tokens & tokens(haystack)) / max(1, len(query_tokens))
            stability_bonus = {"stable": 0.25, "working": 0.15, "transient": 0.05}.get(
                str(row["stability"]),
                0.0,
            )
            score = overlap + stability_bonus + 0.2 * float(row["confidence"])
            if score > 0.05:
                scored.append((score, row))
        scored.sort(key=lambda item: (item[0], item[1]["updated_at"]), reverse=True)
        return [
            {
                "kind": "relationship",
                "relation_id": row["relation_id"],
                "subject": row["subject"],
                "relation_type": row["relation_type"],
                "value": json.loads(row["value_json"]),
                "stability": row["stability"],
                "confidence": float(row["confidence"]),
                "source_ids": json.loads(row["source_ids_json"]),
                "updated_at": row["updated_at"],
                "score": score,
            }
            for score, row in scored[: max(1, int(limit))]
        ]
