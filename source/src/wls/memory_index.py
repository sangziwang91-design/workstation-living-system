from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Iterable
import json
import sqlite3

from .db import Database
from .evidence import EvidenceLedger
from .schemas import utc_now
from .text import tokens


MEMORY_STATES = {"ACTIVE", "WEAKENED", "REFUTED", "EXPIRED", "SUPERSEDED"}
TERMINAL_MEMORY_STATES = {"REFUTED", "EXPIRED", "SUPERSEDED"}


def ensure_causal_memory_tables(db: Database) -> None:
    statements = [
        """
        CREATE TABLE IF NOT EXISTS causal_memory_index (
            memory_id TEXT PRIMARY KEY,
            project_ids_json TEXT NOT NULL,
            context_ids_json TEXT NOT NULL,
            entity_ids_json TEXT NOT NULL,
            entity_names_json TEXT NOT NULL,
            failure_signatures_json TEXT NOT NULL,
            causal_hypothesis_ids_json TEXT NOT NULL,
            prediction_ids_json TEXT NOT NULL,
            skill_ids_json TEXT NOT NULL,
            outcome_type TEXT NOT NULL,
            validity_state TEXT NOT NULL,
            refutation_state TEXT NOT NULL,
            applicability_json TEXT NOT NULL,
            valid_from TEXT,
            valid_until TEXT,
            positive_outcomes INTEGER NOT NULL DEFAULT 0,
            negative_outcomes INTEGER NOT NULL DEFAULT 0,
            consecutive_negative INTEGER NOT NULL DEFAULT 0,
            last_outcome_at TEXT,
            indexed_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(memory_id) REFERENCES memories(memory_id)
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_causal_memory_validity
        ON causal_memory_index(validity_state, refutation_state, valid_until)
        """,
        """
        CREATE TABLE IF NOT EXISTS memory_state_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            memory_id TEXT NOT NULL,
            previous_state TEXT NOT NULL,
            new_state TEXT NOT NULL,
            reason TEXT NOT NULL,
            source_ids_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(memory_id) REFERENCES memories(memory_id)
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_memory_state_events_memory
        ON memory_state_events(memory_id, created_at DESC)
        """,
    ]
    with db.transaction() as connection:
        for statement in statements:
            connection.execute(statement)


class CausalMemoryIndex:
    """Structured, evidence-linked retrieval and contradiction-aware memory validity."""

    def __init__(self, db: Database, ledger: EvidenceLedger) -> None:
        self.db = db
        self.ledger = ledger
        ensure_causal_memory_tables(db)

    def index_memory(self, memory_id: str) -> None:
        row = self.db.query_one("SELECT * FROM memories WHERE memory_id=?", (memory_id,))
        if row is None:
            raise KeyError(memory_id)
        content = json.loads(row["content_json"])
        metadata = self._derive_metadata(content, row)
        now = utc_now()
        with self.db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO causal_memory_index(
                    memory_id,project_ids_json,context_ids_json,entity_ids_json,
                    entity_names_json,failure_signatures_json,causal_hypothesis_ids_json,
                    prediction_ids_json,skill_ids_json,outcome_type,validity_state,
                    refutation_state,applicability_json,valid_from,valid_until,
                    positive_outcomes,negative_outcomes,consecutive_negative,last_outcome_at,
                    indexed_at,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,0,0,NULL,?,?)
                ON CONFLICT(memory_id) DO UPDATE SET
                    project_ids_json=excluded.project_ids_json,
                    context_ids_json=excluded.context_ids_json,
                    entity_ids_json=excluded.entity_ids_json,
                    entity_names_json=excluded.entity_names_json,
                    failure_signatures_json=excluded.failure_signatures_json,
                    causal_hypothesis_ids_json=excluded.causal_hypothesis_ids_json,
                    prediction_ids_json=excluded.prediction_ids_json,
                    skill_ids_json=excluded.skill_ids_json,
                    outcome_type=excluded.outcome_type,
                    applicability_json=excluded.applicability_json,
                    valid_from=excluded.valid_from,
                    valid_until=excluded.valid_until,
                    updated_at=excluded.updated_at
                """,
                (
                    memory_id,
                    self._dump(metadata["project_ids"]),
                    self._dump(metadata["context_ids"]),
                    self._dump(metadata["entity_ids"]),
                    self._dump(metadata["entity_names"]),
                    self._dump(metadata["failure_signatures"]),
                    self._dump(metadata["causal_hypothesis_ids"]),
                    self._dump(metadata["prediction_ids"]),
                    self._dump(metadata["skill_ids"]),
                    metadata["outcome_type"],
                    metadata["validity_state"],
                    metadata["refutation_state"],
                    json.dumps(metadata["applicability_conditions"], ensure_ascii=False, sort_keys=True),
                    metadata["valid_from"],
                    metadata["valid_until"],
                    now,
                    now,
                ),
            )
            self.ledger.append(
                "causal_memory_indexed",
                {
                    "memory_id": memory_id,
                    "project_ids": metadata["project_ids"],
                    "entity_ids": metadata["entity_ids"],
                    "failure_signatures": metadata["failure_signatures"],
                    "causal_hypothesis_ids": metadata["causal_hypothesis_ids"],
                    "validity_state": metadata["validity_state"],
                },
                connection,
            )

    def ensure_all_indexed(self) -> int:
        rows = self.db.query_all(
            """
            SELECT m.memory_id FROM memories m
            LEFT JOIN causal_memory_index i ON i.memory_id=m.memory_id
            WHERE i.memory_id IS NULL
            """
        )
        for row in rows:
            self.index_memory(str(row["memory_id"]))
        return len(rows)

    def build_query_context(self, events: Iterable[Any], goals: Iterable[Any]) -> dict[str, Any]:
        context: dict[str, Any] = {
            "project_ids": set(), "context_ids": set(), "entity_ids": set(),
            "entity_names": set(), "failure_signatures": set(),
            "causal_hypothesis_ids": set(), "prediction_ids": set(),
            "skill_ids": set(), "outcome_types": set(), "conditions": {},
            "timestamp": utc_now(),
        }
        for event in events:
            payload = getattr(event, "payload", {}) or {}
            observation = payload.get("observation", {}) if isinstance(payload, dict) else {}
            if isinstance(observation, dict):
                subject = str(observation.get("subject", "")).strip()
                if subject:
                    context["entity_names"].add(subject)
                self._merge_query_values(context, observation)
                metadata = observation.get("metadata", {})
                if isinstance(metadata, dict):
                    self._merge_query_values(context, metadata)
                value = observation.get("value")
                if isinstance(value, dict):
                    self._merge_query_values(context, value)
            event_id = str(getattr(event, "event_id", "")).strip()
            if event_id:
                context["context_ids"].add(event_id)
            occurred_at = str(getattr(event, "occurred_at", "")).strip()
            if occurred_at:
                context["timestamp"] = max(str(context["timestamp"]), occurred_at)
        for goal in goals:
            goal_id = str(getattr(goal, "goal_id", "")).strip()
            source = str(getattr(goal, "source", "")).strip()
            if goal_id:
                context["context_ids"].add(goal_id)
            if source:
                context["context_ids"].add(source)
                if source.startswith("project:"):
                    context["project_ids"].add(source.split(":", 1)[1])
        return {key: sorted(value) if isinstance(value, set) else value for key, value in context.items()}

    def retrieve(self, query: str, limit: int = 8, *, context: dict[str, Any] | None = None, enabled: bool = True, frozen: bool = False) -> dict[str, Any]:
        self.ensure_all_indexed()
        if enabled and not frozen:
            self._expire_due()
        normalized_context = self._normalize_query_context(context or {})
        rows = self.db.query_all(
            """SELECT m.*,i.* FROM memories m JOIN causal_memory_index i ON i.memory_id=m.memory_id WHERE m.active=1"""
        )
        selected: list[tuple[float, sqlite3.Row, list[str], float]] = []
        suppressed: list[dict[str, Any]] = []
        query_tokens = tokens(query)
        for row in rows:
            memory_id = str(row["memory_id"])
            state = str(row["validity_state"])
            if not enabled:
                suppressed.append(self._suppressed(row, "memory_disabled_baseline", state))
                continue
            rollback_reason = None if frozen else self._linked_rollback_reason(row)
            if rollback_reason and state not in TERMINAL_MEMORY_STATES:
                self.set_state(memory_id, "SUPERSEDED", rollback_reason, [rollback_reason])
                state = "SUPERSEDED"
            if state in TERMINAL_MEMORY_STATES or str(row["refutation_state"]) == "CONFIRMED":
                suppressed.append(self._suppressed(row, f"state:{state.lower()}", state))
                continue
            applicability_score, mismatch = self._applicability(json.loads(row["applicability_json"]), normalized_context)
            if mismatch:
                suppressed.append(self._suppressed(row, "applicability_mismatch", state))
                continue
            structured_score, reasons = self._structured_score(row, normalized_context)
            substantive_reasons = [
                reason for reason in reasons if reason != "time_window"
            ]
            lexical_score = self._overlap(query_tokens, tokens(str(row["normalized_text"])))
            state_multiplier = 0.55 if state == "WEAKENED" else 1.0
            score = state_multiplier * min(
                1.0,
                structured_score + 0.08 * lexical_score + 0.08 * float(row["importance"])
                + 0.06 * float(row["confidence"]) + 0.08 * applicability_score,
            )
            has_structured_query = any(
                normalized_context[key]
                for key in (
                    "project_ids", "context_ids", "entity_ids", "entity_names",
                    "failure_signatures", "causal_hypothesis_ids", "prediction_ids",
                    "skill_ids", "outcome_types",
                )
            )
            if score <= 0.10 or (
                has_structured_query
                and not substantive_reasons
                and lexical_score <= 0.15
            ):
                continue
            selected.append((score, row, reasons, applicability_score))
        selected.sort(key=lambda item: (item[0], str(item[1]["created_at"])), reverse=True)
        chosen = selected[: max(0, int(limit))]
        if chosen and not frozen:
            with self.db.transaction() as connection:
                for _, row, _, _ in chosen:
                    connection.execute(
                        "UPDATE memories SET access_count=access_count+1,last_accessed_at=? WHERE memory_id=?",
                        (utc_now(), row["memory_id"]),
                    )
        return {
            "mode": "frozen" if frozen else ("enabled" if enabled else "disabled"),
            "selected": [self._render(row, score, reasons, applicability) for score, row, reasons, applicability in chosen],
            "suppressed": suppressed,
            "query_context": normalized_context,
        }

    def record_outcome(self, memory_ids: Iterable[str], *, success: bool | None, prediction_statuses: Iterable[str], source_ids: Iterable[str]) -> list[dict[str, Any]]:
        if success is None:
            return []
        transitions: list[dict[str, Any]] = []
        statuses = [str(value) for value in prediction_statuses]
        evidence_ids = [str(value) for value in source_ids if str(value)]
        for memory_id in dict.fromkeys(str(value) for value in memory_ids if str(value)):
            row = self.db.query_one("SELECT * FROM causal_memory_index WHERE memory_id=?", (memory_id,))
            if row is None:
                continue
            previous = str(row["validity_state"])
            if previous in TERMINAL_MEMORY_STATES:
                continue
            positive = int(row["positive_outcomes"])
            negative = int(row["negative_outcomes"])
            consecutive = int(row["consecutive_negative"])
            if success and "REFUTED" not in statuses:
                positive += 1
                consecutive = 0
                new_state = "ACTIVE" if previous == "WEAKENED" and positive >= negative else previous
                new_refutation = "NONE" if new_state == "ACTIVE" else str(row["refutation_state"])
                reason = "attributed decision outcome succeeded"
            else:
                negative += 1
                consecutive += 1
                if consecutive >= 2 or negative >= max(2, positive + 2):
                    new_state, new_refutation = "REFUTED", "CONFIRMED"
                    reason = "repeated attributed decision failure refuted memory"
                else:
                    new_state, new_refutation = "WEAKENED", "PENDING"
                    reason = "attributed decision failure weakened memory"
            now = utc_now()
            with self.db.transaction() as connection:
                connection.execute(
                    """UPDATE causal_memory_index SET validity_state=?,refutation_state=?,positive_outcomes=?,negative_outcomes=?,consecutive_negative=?,last_outcome_at=?,updated_at=? WHERE memory_id=?""",
                    (new_state, new_refutation, positive, negative, consecutive, now, now, memory_id),
                )
                if new_state != previous:
                    connection.execute(
                        """INSERT INTO memory_state_events(memory_id,previous_state,new_state,reason,source_ids_json,created_at) VALUES (?,?,?,?,?,?)""",
                        (memory_id, previous, new_state, reason, self._dump(evidence_ids), now),
                    )
                self.ledger.append(
                    "memory_outcome_attributed",
                    {
                        "memory_id": memory_id, "success": success,
                        "prediction_statuses": statuses, "previous_state": previous,
                        "new_state": new_state, "positive_outcomes": positive,
                        "negative_outcomes": negative, "source_ids": evidence_ids,
                    },
                    connection,
                )
            transitions.append({
                "memory_id": memory_id, "previous_state": previous,
                "new_state": new_state, "positive_outcomes": positive,
                "negative_outcomes": negative,
            })
        return transitions

    def set_state(self, memory_id: str, state: str, reason: str, source_ids: Iterable[str]) -> None:
        if state not in MEMORY_STATES:
            raise ValueError(f"invalid memory state: {state}")
        row = self.db.query_one("SELECT validity_state FROM causal_memory_index WHERE memory_id=?", (memory_id,))
        if row is None:
            self.index_memory(memory_id)
            row = self.db.query_one("SELECT validity_state FROM causal_memory_index WHERE memory_id=?", (memory_id,))
        if row is None:
            raise KeyError(memory_id)
        previous = str(row["validity_state"])
        if previous == state:
            return
        now = utc_now()
        refutation = "CONFIRMED" if state == "REFUTED" else ("NONE" if state == "ACTIVE" else "PENDING")
        evidence_ids = [str(value) for value in source_ids if str(value)]
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE causal_memory_index SET validity_state=?,refutation_state=?,updated_at=? WHERE memory_id=?",
                (state, refutation, now, memory_id),
            )
            connection.execute(
                """INSERT INTO memory_state_events(memory_id,previous_state,new_state,reason,source_ids_json,created_at) VALUES (?,?,?,?,?,?)""",
                (memory_id, previous, state, reason, self._dump(evidence_ids), now),
            )
            self.ledger.append(
                "memory_state_changed",
                {
                    "memory_id": memory_id, "previous_state": previous,
                    "new_state": state, "reason": reason, "source_ids": evidence_ids,
                },
                connection,
            )

    def state(self, memory_id: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM causal_memory_index WHERE memory_id=?", (memory_id,))
        if row is None:
            self.index_memory(memory_id)
            row = self.db.query_one("SELECT * FROM causal_memory_index WHERE memory_id=?", (memory_id,))
        if row is None:
            raise KeyError(memory_id)
        return self._state_dict(row)

    def summary(self) -> dict[str, Any]:
        rows = self.db.query_all("SELECT validity_state,COUNT(*) AS n FROM causal_memory_index GROUP BY validity_state")
        states = {str(row["validity_state"]): int(row["n"]) for row in rows}
        state_events = self.db.query_one("SELECT COUNT(*) AS n FROM memory_state_events")
        return {"indexed": sum(states.values()), "states": states, "state_events": int(state_events["n"]) if state_events else 0}

    def integrity(self) -> tuple[bool, dict[str, int]]:
        checks = {
            "orphan_index_rows": """SELECT COUNT(*) AS n FROM causal_memory_index i LEFT JOIN memories m ON m.memory_id=i.memory_id WHERE m.memory_id IS NULL""",
            "invalid_states": """SELECT COUNT(*) AS n FROM causal_memory_index WHERE validity_state NOT IN ('ACTIVE','WEAKENED','REFUTED','EXPIRED','SUPERSEDED')""",
            "terminal_active_mismatch": """SELECT COUNT(*) AS n FROM causal_memory_index i JOIN memories m ON m.memory_id=i.memory_id WHERE m.active=0 AND i.validity_state NOT IN ('REFUTED','EXPIRED','SUPERSEDED')""",
        }
        counts: dict[str, int] = {}
        for name, sql in checks.items():
            row = self.db.query_one(sql)
            counts[name] = int(row["n"]) if row else 0
        return all(value == 0 for value in counts.values()), counts

    def _derive_metadata(self, content: dict[str, Any], row: sqlite3.Row) -> dict[str, Any]:
        fields = {
            "project_ids": self._collect(content, {"project_id", "project_ids"}),
            "context_ids": self._collect(content, {"context_id", "context_ids", "cycle_id", "plan_id", "goal_id", "source_candidate_id", "candidate_id"}),
            "entity_ids": self._collect(content, {"entity_id", "entity_ids"}),
            "entity_names": self._collect(content, {"entity_name", "entity_names", "subject"}),
            "failure_signatures": self._collect(content, {"failure_signature", "failure_signatures"}),
            "causal_hypothesis_ids": self._collect(content, {"causal_hypothesis_id", "causal_hypothesis_ids", "hypothesis_id"}),
            "prediction_ids": self._collect(content, {"prediction_id", "prediction_ids"}),
            "skill_ids": self._collect(content, {"skill_id", "skill_ids"}),
        }
        for cycle_id in self._collect(content, {"cycle_id"}):
            hypothesis = self.db.query_one(
                "SELECT temporal_json FROM cognitive_hypotheses WHERE cycle_id=? AND selected=1 ORDER BY created_at DESC LIMIT 1",
                (cycle_id,),
            )
            if hypothesis is not None:
                temporal = json.loads(hypothesis["temporal_json"] or "{}")
                fields["entity_ids"].update(self._string_values(temporal.get("entity_id")))
                fields["causal_hypothesis_ids"].update(self._string_values(temporal.get("causal_hypothesis_id")))
            trace = self.db.query_one("SELECT trace_id FROM cognitive_traces WHERE cycle_id=?", (cycle_id,))
            if trace is not None:
                prediction_rows = self.db.query_all("SELECT prediction_id FROM cognitive_predictions WHERE trace_id=?", (trace["trace_id"],))
                fields["prediction_ids"].update(str(item["prediction_id"]) for item in prediction_rows)
        for plan_id in self._collect(content, {"plan_id"}):
            for action in self.db.query_all("SELECT skill_id,tool,status,error FROM actions WHERE plan_id=? ORDER BY rowid", (plan_id,)):
                if action["skill_id"]:
                    fields["skill_ids"].add(str(action["skill_id"]))
                if str(action["status"]) in {"FAILED", "REJECTED", "UNKNOWN_SIDE_EFFECT"}:
                    fields["failure_signatures"].add(self._failure_signature(str(action["tool"]), str(action["error"] or "")))
        outcomes = content.get("outcomes", [])
        outcome_type = str(content.get("outcome_type", "")).strip().upper()
        if not outcome_type and isinstance(outcomes, list) and outcomes:
            outcome_type = "SUCCESS" if all(bool(item.get("success")) for item in outcomes if isinstance(item, dict)) else "FAILURE"
        if not outcome_type:
            outcome_type = "UNKNOWN"
        mechanism = str(content.get("mechanism", "")).strip()
        if mechanism:
            fields["failure_signatures"].add(mechanism.lower()[:240])
        applicability = content.get("applicability_conditions", {})
        if not isinstance(applicability, dict):
            applicability = {}
        validity_state = str(content.get("validity_state", "ACTIVE")).upper()
        if validity_state not in MEMORY_STATES:
            validity_state = "ACTIVE"
        refutation_state = str(content.get("refutation_state", "NONE")).upper()
        if refutation_state not in {"NONE", "PENDING", "CONFIRMED"}:
            refutation_state = "NONE"
        fields["context_ids"].update(str(value) for value in json.loads(row["source_ids_json"]))
        return {
            **{key: sorted(value) for key, value in fields.items()},
            "outcome_type": outcome_type, "validity_state": validity_state,
            "refutation_state": refutation_state, "applicability_conditions": applicability,
            "valid_from": content.get("valid_from") or row["created_at"],
            "valid_until": content.get("valid_until"),
        }

    def _structured_score(self, row: sqlite3.Row, context: dict[str, Any]) -> tuple[float, list[str]]:
        dimensions = [
            ("project_ids", "project_ids_json", 0.24, "project"),
            ("context_ids", "context_ids_json", 0.12, "context"),
            ("entity_ids", "entity_ids_json", 0.24, "entity"),
            ("entity_names", "entity_names_json", 0.16, "entity_name"),
            ("failure_signatures", "failure_signatures_json", 0.24, "failure"),
            ("causal_hypothesis_ids", "causal_hypothesis_ids_json", 0.20, "causal_hypothesis"),
            ("prediction_ids", "prediction_ids_json", 0.12, "prediction"),
            ("skill_ids", "skill_ids_json", 0.14, "skill"),
        ]
        score = 0.0
        reasons: list[str] = []
        for context_key, row_key, weight, label in dimensions:
            overlap = set(context[context_key]) & set(json.loads(row[row_key]))
            if overlap:
                score += weight
                reasons.append(f"{label}:{','.join(sorted(overlap))}")
        if set(context["outcome_types"]) and str(row["outcome_type"]) in set(context["outcome_types"]):
            score += 0.10
            reasons.append(f"outcome:{row['outcome_type']}")
        timestamp = self._parse_time(context.get("timestamp"))
        valid_from = self._parse_time(row["valid_from"])
        valid_until = self._parse_time(row["valid_until"])
        if timestamp and (valid_from is None or valid_from <= timestamp) and (valid_until is None or timestamp <= valid_until):
            score += 0.04
            reasons.append("time_window")
        return min(0.90, score), reasons

    @staticmethod
    def _applicability(conditions: dict[str, Any], context: dict[str, Any]) -> tuple[float, bool]:
        if not conditions:
            return 0.5, False
        actual = context.get("conditions", {})
        if not isinstance(actual, dict):
            return 0.0, True
        matches = 0
        for key, expected in conditions.items():
            if key not in actual:
                return 0.0, True
            observed = actual[key]
            if isinstance(expected, list):
                if observed not in expected:
                    return 0.0, True
            elif observed != expected:
                return 0.0, True
            matches += 1
        return matches / max(1, len(conditions)), False

    def _linked_rollback_reason(self, row: sqlite3.Row) -> str | None:
        for skill_id in json.loads(row["skill_ids_json"]):
            skill = self.db.query_one("SELECT status FROM skills WHERE skill_id=?", (skill_id,))
            if skill is not None and str(skill["status"]) == "ROLLED_BACK":
                return f"linked_skill_rolled_back:{skill_id}"
        for context_id in json.loads(row["context_ids_json"]):
            candidate = self.db.query_one("SELECT status FROM evolution_candidates WHERE candidate_id=?", (context_id,))
            if candidate is not None and str(candidate["status"]) == "ROLLED_BACK":
                return f"linked_candidate_rolled_back:{context_id}"
        return None

    def _expire_due(self) -> None:
        now = datetime.now(UTC)
        for row in self.db.query_all("SELECT memory_id,valid_until FROM causal_memory_index WHERE validity_state IN ('ACTIVE','WEAKENED') AND valid_until IS NOT NULL"):
            expires = self._parse_time(row["valid_until"])
            if expires is not None and expires < now:
                self.set_state(str(row["memory_id"]), "EXPIRED", "memory validity window elapsed", ["clock"])

    def _render(self, row: sqlite3.Row, score: float, reasons: list[str], applicability_score: float) -> dict[str, Any]:
        return {
            "memory_id": row["memory_id"], "memory_type": row["memory_type"],
            "content": json.loads(row["content_json"]), "importance": float(row["importance"]),
            "confidence": float(row["confidence"]), "source_ids": json.loads(row["source_ids_json"]),
            "tags": json.loads(row["tags_json"]), "score": score, "causal_score": score,
            "retrieval_reasons": reasons, "validity_state": row["validity_state"],
            "refutation_state": row["refutation_state"], "applicability_score": applicability_score,
            "created_at": row["created_at"],
        }

    @staticmethod
    def _suppressed(row: sqlite3.Row, reason: str, state: str) -> dict[str, Any]:
        return {"memory_id": row["memory_id"], "reason": reason, "validity_state": state, "refutation_state": row["refutation_state"]}

    @staticmethod
    def _state_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "memory_id": row["memory_id"], "validity_state": row["validity_state"],
            "refutation_state": row["refutation_state"], "positive_outcomes": int(row["positive_outcomes"]),
            "negative_outcomes": int(row["negative_outcomes"]), "consecutive_negative": int(row["consecutive_negative"]),
            "valid_from": row["valid_from"], "valid_until": row["valid_until"], "last_outcome_at": row["last_outcome_at"],
        }

    @classmethod
    def _normalize_query_context(cls, context: dict[str, Any]) -> dict[str, Any]:
        normalized = {
            "project_ids": cls._string_values(context.get("project_ids")),
            "context_ids": cls._string_values(context.get("context_ids")),
            "entity_ids": cls._string_values(context.get("entity_ids")),
            "entity_names": cls._string_values(context.get("entity_names")),
            "failure_signatures": cls._string_values(context.get("failure_signatures")),
            "causal_hypothesis_ids": cls._string_values(context.get("causal_hypothesis_ids")),
            "prediction_ids": cls._string_values(context.get("prediction_ids")),
            "skill_ids": cls._string_values(context.get("skill_ids")),
            "outcome_types": cls._string_values(context.get("outcome_types")),
            "conditions": dict(context.get("conditions", {})) if isinstance(context.get("conditions", {}), dict) else {},
            "timestamp": str(context.get("timestamp") or utc_now()),
        }
        return {key: sorted(value) if isinstance(value, set) else value for key, value in normalized.items()}

    @classmethod
    def _merge_query_values(cls, target: dict[str, Any], value: dict[str, Any]) -> None:
        mapping = {
            "project_id": "project_ids", "project_ids": "project_ids",
            "context_id": "context_ids", "context_ids": "context_ids",
            "entity_id": "entity_ids", "entity_ids": "entity_ids",
            "entity_name": "entity_names", "entity_names": "entity_names",
            "failure_signature": "failure_signatures", "failure_signatures": "failure_signatures",
            "causal_hypothesis_id": "causal_hypothesis_ids", "causal_hypothesis_ids": "causal_hypothesis_ids",
            "prediction_id": "prediction_ids", "prediction_ids": "prediction_ids",
            "skill_id": "skill_ids", "skill_ids": "skill_ids",
            "outcome_type": "outcome_types", "outcome_types": "outcome_types",
        }
        for key, destination in mapping.items():
            if key in value:
                target[destination].update(cls._string_values(value[key]))
        conditions = value.get("conditions") or value.get("applicability_conditions")
        if isinstance(conditions, dict):
            target["conditions"].update(conditions)

    @classmethod
    def _collect(cls, value: Any, keys: set[str]) -> set[str]:
        collected: set[str] = set()
        if isinstance(value, dict):
            for key, item in value.items():
                if key in keys:
                    collected.update(cls._string_values(item))
                collected.update(cls._collect(item, keys))
        elif isinstance(value, list):
            for item in value:
                collected.update(cls._collect(item, keys))
        return collected

    @staticmethod
    def _string_values(value: Any) -> set[str]:
        if value is None:
            return set()
        if isinstance(value, (list, tuple, set)):
            return {str(item).strip() for item in value if str(item).strip()}
        text = str(value).strip()
        return {text} if text else set()

    @staticmethod
    def _failure_signature(tool: str, error: str) -> str:
        normalized = " ".join(error.lower().split())[:180]
        return f"{tool.lower()}:{normalized or 'unknown'}"

    @staticmethod
    def _parse_time(value: Any) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            return None

    @staticmethod
    def _overlap(left: set[str], right: set[str]) -> float:
        if not left or not right:
            return 0.0
        return len(left & right) / max(1, len(left | right))

    @staticmethod
    def _dump(values: Iterable[str]) -> str:
        return json.dumps(sorted({str(value) for value in values if str(value)}), ensure_ascii=False)
