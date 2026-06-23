from __future__ import annotations

from typing import Any
import json

from .db import Database
from .evidence import EvidenceLedger
from .schemas import Observation, VerificationStatus, digest_json, new_id, utc_now
from .text import tokens


class WorldModel:
    def __init__(self, db: Database, ledger: EvidenceLedger):
        self.db = db
        self.ledger = ledger

    def assimilate(self, observation: Observation) -> dict[str, Any]:
        key_rows = self.db.query_all(
            """
            SELECT * FROM world_facts
            WHERE subject=? AND predicate=? AND active=1
            ORDER BY last_seen_at DESC
            """,
            (observation.subject, observation.predicate),
        )
        if observation.verification == VerificationStatus.REFUTED:
            return self._apply_refutation(observation, key_rows)
        same = None
        contradictions: list[str] = []
        for row in key_rows:
            old_value = json.loads(row["value_json"])
            if old_value == observation.value:
                same = row
                break
            contradictions.append(str(row["fact_id"]))
        with self.db.transaction() as connection:
            if same is not None:
                source_ids = list(json.loads(same["source_ids_json"]))
                if observation.observation_id not in source_ids:
                    source_ids.append(observation.observation_id)
                combined_confidence = 1.0 - (1.0 - float(same["confidence"])) * (
                    1.0 - observation.confidence
                )
                connection.execute(
                    """
                    UPDATE world_facts SET confidence=?, verification=?, source_ids_json=?,
                        last_seen_at=?, valid_until=? WHERE fact_id=?
                    """,
                    (
                        min(1.0, combined_confidence),
                        self._merge_verification(
                            str(same["verification"]), observation.verification.value
                        ),
                        json.dumps(source_ids, ensure_ascii=False),
                        observation.observed_at,
                        observation.expires_at,
                        same["fact_id"],
                    ),
                )
                fact_id = str(same["fact_id"])
                action = "reinforced"
            else:
                fact_id = new_id("fact")
                contradiction_group = None
                if contradictions:
                    contradiction_group = digest_json(
                        {
                            "subject": observation.subject,
                            "predicate": observation.predicate,
                        }
                    )[:24]
                    for contradiction_id in contradictions:
                        connection.execute(
                            "UPDATE world_facts SET contradiction_group=? WHERE fact_id=?",
                            (contradiction_group, contradiction_id),
                        )
                connection.execute(
                    """
                    INSERT INTO world_facts(
                        fact_id,subject,predicate,value_json,source_kind,confidence,verification,
                        source_ids_json,first_seen_at,last_seen_at,valid_until,active,
                        supersedes_fact_id,contradiction_group
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        fact_id,
                        observation.subject,
                        observation.predicate,
                        json.dumps(
                            observation.value, ensure_ascii=False, sort_keys=True
                        ),
                        observation.evidence_kind.value,
                        observation.confidence,
                        observation.verification.value,
                        json.dumps([observation.observation_id], ensure_ascii=False),
                        observation.observed_at,
                        observation.observed_at,
                        observation.expires_at,
                        1,
                        contradictions[0] if len(contradictions) == 1 else None,
                        contradiction_group,
                    ),
                )
                action = "created_with_contradiction" if contradictions else "created"
            self.ledger.append(
                "world_model_assimilated",
                {
                    "fact_id": fact_id,
                    "observation_id": observation.observation_id,
                    "action": action,
                    "contradictions": contradictions,
                },
                connection,
            )
        prediction_errors = self.resolve_predictions(observation)
        return {
            "fact_id": fact_id,
            "action": action,
            "contradictions": contradictions,
            "prediction_errors": prediction_errors,
        }

    def _apply_refutation(
        self, observation: Observation, key_rows: list[Any]
    ) -> dict[str, Any]:
        matched = []
        with self.db.transaction() as connection:
            for row in key_rows:
                if json.loads(row["value_json"]) != observation.value:
                    continue
                matched.append(str(row["fact_id"]))
                confidence = max(
                    0.0, float(row["confidence"]) - 0.75 * observation.confidence
                )
                active = 1 if confidence >= 0.2 else 0
                connection.execute(
                    "UPDATE world_facts SET confidence=?,verification=?,active=?,last_seen_at=? WHERE fact_id=?",
                    (
                        confidence,
                        VerificationStatus.REFUTED.value,
                        active,
                        observation.observed_at,
                        row["fact_id"],
                    ),
                )
            self.ledger.append(
                "world_fact_refuted",
                {
                    "observation_id": observation.observation_id,
                    "subject": observation.subject,
                    "predicate": observation.predicate,
                    "value": observation.value,
                    "matched_fact_ids": matched,
                },
                connection,
            )
        prediction_errors = self.resolve_predictions(observation)
        return {
            "fact_id": matched[0] if matched else None,
            "action": "refuted" if matched else "refutation_without_target",
            "contradictions": [],
            "prediction_errors": prediction_errors,
        }

    def resolve_contradictions(
        self, confidence_threshold: float = 0.85, margin: float = 0.25
    ) -> list[dict[str, Any]]:
        resolved: list[dict[str, Any]] = []
        groups = self.db.query_all(
            "SELECT DISTINCT contradiction_group FROM world_facts WHERE active=1 AND contradiction_group IS NOT NULL"
        )
        for group_row in groups:
            group = group_row["contradiction_group"]
            facts = self.db.query_all(
                "SELECT * FROM world_facts WHERE active=1 AND contradiction_group=? ORDER BY confidence DESC,last_seen_at DESC",
                (group,),
            )
            if len(facts) < 2:
                continue
            winner = facts[0]
            runner_up = facts[1]
            if (
                winner["verification"] == VerificationStatus.VERIFIED.value
                and float(winner["confidence"]) >= confidence_threshold
                and float(winner["confidence"]) - float(runner_up["confidence"])
                >= margin
            ):
                loser_ids = [str(row["fact_id"]) for row in facts[1:]]
                with self.db.transaction() as connection:
                    connection.execute(
                        "UPDATE world_facts SET contradiction_group=NULL WHERE fact_id=?",
                        (winner["fact_id"],),
                    )
                    for loser_id in loser_ids:
                        connection.execute(
                            "UPDATE world_facts SET active=0 WHERE fact_id=?",
                            (loser_id,),
                        )
                    self.ledger.append(
                        "world_contradiction_resolved",
                        {
                            "group": group,
                            "winner": winner["fact_id"],
                            "deactivated": loser_ids,
                        },
                        connection,
                    )
                resolved.append(
                    {
                        "group": group,
                        "winner": winner["fact_id"],
                        "deactivated": loser_ids,
                    }
                )
        return resolved

    def active_facts(
        self, subject: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        now = utc_now()
        if subject:
            rows = self.db.query_all(
                """
                SELECT * FROM world_facts WHERE active=1 AND subject=?
                AND (valid_until IS NULL OR valid_until>?)
                ORDER BY confidence DESC,last_seen_at DESC LIMIT ?
                """,
                (subject, now, limit),
            )
        else:
            rows = self.db.query_all(
                """
                SELECT * FROM world_facts WHERE active=1
                AND (valid_until IS NULL OR valid_until>?)
                ORDER BY confidence DESC,last_seen_at DESC LIMIT ?
                """,
                (now, limit),
            )
        return [self._fact(row) for row in rows]

    def query(self, text: str, limit: int = 20) -> list[dict[str, Any]]:
        tokens = self._tokens(text)
        rows = self.db.query_all("SELECT * FROM world_facts WHERE active=1")
        scored: list[tuple[float, Any]] = []
        for row in rows:
            haystack = (
                f"{row['subject']} {row['predicate']} {row['value_json']}".lower()
            )
            overlap = len(tokens & self._tokens(haystack)) / max(1, len(tokens))
            score = overlap + 0.2 * float(row["confidence"])
            if score > 0.05:
                scored.append((score, row))
        scored.sort(key=lambda item: (item[0], item[1]["last_seen_at"]), reverse=True)
        return [{**self._fact(row), "score": score} for score, row in scored[:limit]]

    def add_prediction(
        self,
        subject: str,
        predicate: str,
        expected_value: Any,
        confidence: float,
        source: str,
        due_at: str | None = None,
    ) -> str:
        existing = self.db.query_one(
            """
            SELECT prediction_id FROM predictions
            WHERE status='OPEN' AND subject=? AND predicate=? AND expected_value_json=?
            ORDER BY created_at DESC LIMIT 1
            """,
            (
                subject,
                predicate,
                json.dumps(expected_value, ensure_ascii=False, sort_keys=True),
            ),
        )
        if existing is not None:
            return str(existing["prediction_id"])
        prediction_id = new_id("pred")
        with self.db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO predictions(
                    prediction_id,subject,predicate,expected_value_json,confidence,due_at,
                    source,status,created_at
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    prediction_id,
                    subject,
                    predicate,
                    json.dumps(expected_value, ensure_ascii=False, sort_keys=True),
                    max(0.0, min(1.0, confidence)),
                    due_at,
                    source,
                    "OPEN",
                    utc_now(),
                ),
            )
            self.ledger.append(
                "prediction_created",
                {
                    "prediction_id": prediction_id,
                    "subject": subject,
                    "predicate": predicate,
                    "expected": expected_value,
                },
                connection,
            )
        return prediction_id

    def resolve_predictions(self, observation: Observation) -> list[dict[str, Any]]:
        rows = self.db.query_all(
            """
            SELECT * FROM predictions WHERE status='OPEN' AND subject=? AND predicate=?
            ORDER BY created_at ASC
            """,
            (observation.subject, observation.predicate),
        )
        resolved: list[dict[str, Any]] = []
        for row in rows:
            expected = json.loads(row["expected_value_json"])
            error = self._error(expected, observation.value)
            status = "CONFIRMED" if error <= 0.05 else "REFUTED"
            with self.db.transaction() as connection:
                connection.execute(
                    """
                    UPDATE predictions SET status=?,resolved_at=?,actual_value_json=?,error_score=?
                    WHERE prediction_id=?
                    """,
                    (
                        status,
                        utc_now(),
                        json.dumps(observation.value, ensure_ascii=False),
                        error,
                        row["prediction_id"],
                    ),
                )
                self.ledger.append(
                    "prediction_resolved",
                    {
                        "prediction_id": row["prediction_id"],
                        "status": status,
                        "error_score": error,
                        "observation_id": observation.observation_id,
                    },
                    connection,
                )
            resolved.append(
                {
                    "prediction_id": row["prediction_id"],
                    "status": status,
                    "error_score": error,
                }
            )
        return resolved

    def unresolved_contradictions(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.db.query_all(
            """
            SELECT contradiction_group,subject,predicate,COUNT(*) AS n
            FROM world_facts WHERE active=1 AND contradiction_group IS NOT NULL
            GROUP BY contradiction_group,subject,predicate HAVING COUNT(*)>1
            ORDER BY n DESC LIMIT ?
            """,
            (limit,),
        )
        result = []
        for row in rows:
            facts = self.db.query_all(
                "SELECT * FROM world_facts WHERE contradiction_group=? AND active=1",
                (row["contradiction_group"],),
            )
            result.append(
                {
                    "contradiction_group": row["contradiction_group"],
                    "subject": row["subject"],
                    "predicate": row["predicate"],
                    "facts": [self._fact(fact) for fact in facts],
                }
            )
        return result

    def expire_stale(self) -> int:
        return self.db.execute(
            "UPDATE world_facts SET active=0 WHERE active=1 AND valid_until IS NOT NULL AND valid_until<=?",
            (utc_now(),),
        )

    @staticmethod
    def _fact(row) -> dict[str, Any]:
        return {
            "fact_id": row["fact_id"],
            "subject": row["subject"],
            "predicate": row["predicate"],
            "value": json.loads(row["value_json"]),
            "source_kind": row["source_kind"],
            "confidence": float(row["confidence"]),
            "verification": row["verification"],
            "source_ids": json.loads(row["source_ids_json"]),
            "first_seen_at": row["first_seen_at"],
            "last_seen_at": row["last_seen_at"],
            "valid_until": row["valid_until"],
            "contradiction_group": row["contradiction_group"],
        }

    @staticmethod
    def _merge_verification(old: str, new: str) -> str:
        rank = {
            VerificationStatus.REFUTED.value: 0,
            VerificationStatus.UNKNOWN.value: 1,
            VerificationStatus.INFERENCE.value: 2,
            VerificationStatus.VERIFIED.value: 3,
        }
        return old if rank.get(old, 0) >= rank.get(new, 0) else new

    @staticmethod
    def _error(expected: Any, actual: Any) -> float:
        if expected == actual:
            return 0.0
        if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
            denominator = max(1.0, abs(float(expected)), abs(float(actual)))
            return min(1.0, abs(float(expected) - float(actual)) / denominator)
        if isinstance(expected, dict) and isinstance(actual, dict):
            keys = set(expected) | set(actual)
            if not keys:
                return 0.0
            return sum(
                WorldModel._error(expected.get(key), actual.get(key)) for key in keys
            ) / len(keys)
        return 1.0

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return tokens(text)
