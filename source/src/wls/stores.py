from __future__ import annotations

from typing import Any, Iterable
import json
import sqlite3

from .db import Database
from .evidence import EvidenceLedger
from .memory_index import CausalMemoryIndex
from .text import tokens
from .schemas import (
    Event,
    EventStatus,
    Goal,
    GoalStatus,
    MemoryItem,
    Observation,
    RiskLevel,
    utc_now,
)


class EventStore:
    def __init__(self, db: Database, ledger: EvidenceLedger):
        self.db = db
        self.ledger = ledger

    def add_observation(self, observation: Observation) -> tuple[str, str]:
        payload = observation.to_dict()
        event = Event(
            event_type=f"observation.{observation.kind}",
            source=observation.source,
            payload={"observation": payload},
            salience_hint=self._salience(observation),
            occurred_at=observation.observed_at,
            dedupe_key=str(
                observation.metadata.get("dedupe_key")
                or f"observation:{observation.observation_id}"
            ),
        )
        with self.db.transaction() as connection:
            inserted = connection.execute(
                """
                INSERT OR IGNORE INTO events(
                    event_id,event_type,source,payload_json,salience_hint,occurred_at,
                    dedupe_key,status,attempts
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    event.event_id,
                    event.event_type,
                    event.source,
                    json.dumps(event.payload, ensure_ascii=False, sort_keys=True),
                    event.salience_hint,
                    event.occurred_at,
                    event.dedupe_key,
                    event.status.value,
                    event.attempts,
                ),
            ).rowcount
            if inserted == 0:
                row = connection.execute(
                    "SELECT event_id FROM events WHERE dedupe_key=?",
                    (event.dedupe_key,),
                ).fetchone()
                return str(row["event_id"]), observation.observation_id
            connection.execute(
                """
                INSERT INTO observations(
                    observation_id,source,kind,subject,predicate,value_json,confidence,
                    evidence_kind,verification,observed_at,expires_at,metadata_json,event_id
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    observation.observation_id,
                    observation.source,
                    observation.kind,
                    observation.subject,
                    observation.predicate,
                    json.dumps(observation.value, ensure_ascii=False, sort_keys=True),
                    observation.confidence,
                    observation.evidence_kind.value,
                    observation.verification.value,
                    observation.observed_at,
                    observation.expires_at,
                    json.dumps(
                        observation.metadata, ensure_ascii=False, sort_keys=True
                    ),
                    event.event_id,
                ),
            )
            self.ledger.append(
                "observation_ingested", {"event": event.to_dict()}, connection
            )
        return event.event_id, observation.observation_id

    def add_event(self, event: Event) -> tuple[str, bool]:
        with self.db.transaction() as connection:
            inserted = connection.execute(
                """
                INSERT OR IGNORE INTO events(
                    event_id,event_type,source,payload_json,salience_hint,occurred_at,
                    dedupe_key,status,attempts
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    event.event_id,
                    event.event_type,
                    event.source,
                    json.dumps(event.payload, ensure_ascii=False, sort_keys=True),
                    event.salience_hint,
                    event.occurred_at,
                    event.dedupe_key,
                    event.status.value,
                    event.attempts,
                ),
            ).rowcount
            if inserted:
                self.ledger.append(
                    "event_ingested", {"event": event.to_dict()}, connection
                )
                return event.event_id, True
            row = connection.execute(
                "SELECT event_id FROM events WHERE dedupe_key=?", (event.dedupe_key,)
            ).fetchone()
            return str(row["event_id"]), False

    def reserve(self, worker_id: str, limit: int) -> list[Event]:
        with self.db.transaction() as connection:
            rows = connection.execute(
                """
                SELECT * FROM events WHERE status=?
                ORDER BY salience_hint DESC, occurred_at ASC LIMIT ?
                """,
                (EventStatus.PENDING.value, limit),
            ).fetchall()
            events: list[Event] = []
            for row in rows:
                updated = connection.execute(
                    """
                    UPDATE events SET status=?, reserved_by=?, reserved_at=?, attempts=attempts+1
                    WHERE event_id=? AND status=?
                    """,
                    (
                        EventStatus.RESERVED.value,
                        worker_id,
                        utc_now(),
                        row["event_id"],
                        EventStatus.PENDING.value,
                    ),
                ).rowcount
                if updated:
                    events.append(
                        self._row_to_event(
                            row,
                            status=EventStatus.RESERVED,
                            attempts=row["attempts"] + 1,
                        )
                    )
            return events

    def release_unselected(self, worker_id: str, selected_ids: set[str]) -> int:
        with self.db.transaction() as connection:
            rows = connection.execute(
                "SELECT event_id FROM events WHERE status=? AND reserved_by=?",
                (EventStatus.RESERVED.value, worker_id),
            ).fetchall()
            released = 0
            for row in rows:
                event_id = str(row["event_id"])
                if event_id not in selected_ids:
                    released += connection.execute(
                        "UPDATE events SET status=?, reserved_by=NULL, reserved_at=NULL WHERE event_id=?",
                        (EventStatus.PENDING.value, event_id),
                    ).rowcount
            return released

    def mark_processed(
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

    def fail(self, event_id: str, error: str, max_attempts: int = 3) -> None:
        with self.db.transaction() as connection:
            row = connection.execute(
                "SELECT attempts FROM events WHERE event_id=?", (event_id,)
            ).fetchone()
            if row is None:
                return
            status = (
                EventStatus.DEAD_LETTER
                if int(row["attempts"]) >= max_attempts
                else EventStatus.PENDING
            )
            truncated = len(error) > 4000
            connection.execute(
                """
                UPDATE events SET status=?, reserved_by=NULL, reserved_at=NULL, last_error=?
                WHERE event_id=?
                """,
                (status.value, error[:4000], event_id),
            )
            self.ledger.append(
                "event_failed",
                {
                    "event_id": event_id,
                    "status": status.value,
                    "error": error[:1000],
                    "error_truncated": len(error) > 1000,
                },
                connection,
            )

    def recover_stale_reservations(self, stale_before: str) -> int:
        return self.db.execute(
            """
            UPDATE events SET status=?, reserved_by=NULL, reserved_at=NULL
            WHERE status=? AND reserved_at < ?
            """,
            (EventStatus.PENDING.value, EventStatus.RESERVED.value, stale_before),
        )

    def counts(self) -> dict[str, int]:
        rows = self.db.query_all(
            "SELECT status, COUNT(*) AS n FROM events GROUP BY status"
        )
        return {str(row["status"]): int(row["n"]) for row in rows}

    @staticmethod
    def _row_to_event(
        row: sqlite3.Row, status: EventStatus | None = None, attempts: int | None = None
    ) -> Event:
        return Event(
            event_id=row["event_id"],
            event_type=row["event_type"],
            source=row["source"],
            payload=json.loads(row["payload_json"]),
            salience_hint=float(row["salience_hint"]),
            occurred_at=row["occurred_at"],
            dedupe_key=row["dedupe_key"],
            status=status or EventStatus(row["status"]),
            attempts=int(row["attempts"] if attempts is None else attempts),
        )

    @staticmethod
    def _salience(observation: Observation) -> float:
        warning = bool(observation.metadata.get("warning"))
        error_kind = observation.kind in {
            "sensor_error",
            "service_health",
            "process_change",
        }
        base = 0.45 + (0.25 if warning else 0.0) + (0.15 if error_kind else 0.0)
        if observation.verification.value == "UNKNOWN":
            base += 0.05
        return min(1.0, base)


class GoalStore:
    def __init__(self, db: Database, ledger: EvidenceLedger):
        self.db = db
        self.ledger = ledger

    def add(self, goal: Goal) -> str:
        with self.db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO goals(
                    goal_id,title,description,priority,success_criteria_json,source,autonomous,
                    parent_goal_id,deadline,status,progress,created_at,updated_at,
                    rationale,origin,task_spec_json,risk
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    goal.goal_id,
                    goal.title,
                    goal.description,
                    goal.priority,
                    json.dumps(goal.success_criteria, ensure_ascii=False),
                    goal.source,
                    int(goal.autonomous),
                    goal.parent_goal_id,
                    goal.deadline,
                    goal.status.value,
                    goal.progress,
                    goal.created_at,
                    goal.updated_at,
                    goal.rationale,
                    goal.origin,
                    json.dumps(goal.task_spec, ensure_ascii=False, sort_keys=True),
                    goal.risk.value,
                ),
            )
            self.ledger.append("goal_created", {"goal": goal.to_dict()}, connection)
        return goal.goal_id

    def active(self, limit: int = 20) -> list[Goal]:
        rows = self.db.query_all(
            "SELECT * FROM goals WHERE status IN (?,?) ORDER BY priority DESC, updated_at ASC LIMIT ?",
            (GoalStatus.ACTIVE.value, GoalStatus.BLOCKED.value, limit),
        )
        return [self._row(row) for row in rows]

    def get(self, goal_id: str) -> Goal | None:
        row = self.db.query_one("SELECT * FROM goals WHERE goal_id=?", (goal_id,))
        return self._row(row) if row is not None else None

    def update_progress(
        self, goal_id: str, progress: float, status: GoalStatus | None = None
    ) -> None:
        progress = max(0.0, min(1.0, progress))
        if status is None:
            inferred_status = GoalStatus.SUCCEEDED if progress >= 1.0 else None
            if inferred_status is None:
                self.db.execute(
                    "UPDATE goals SET progress=?, updated_at=? WHERE goal_id=?",
                    (progress, utc_now(), goal_id),
                )
            else:
                self.db.execute(
                    "UPDATE goals SET progress=?, status=?, updated_at=? WHERE goal_id=?",
                    (progress, inferred_status.value, utc_now(), goal_id),
                )
        else:
            self.db.execute(
                "UPDATE goals SET progress=?, status=?, updated_at=? WHERE goal_id=?",
                (progress, status.value, utc_now(), goal_id),
            )

    def autonomous_count(self) -> int:
        row = self.db.query_one(
            "SELECT COUNT(*) AS n FROM goals WHERE autonomous=1 AND status IN (?,?)",
            (GoalStatus.ACTIVE.value, GoalStatus.BLOCKED.value),
        )
        return int(row["n"]) if row else 0

    @staticmethod
    def _row(row: sqlite3.Row) -> Goal:
        return Goal(
            goal_id=row["goal_id"],
            title=row["title"],
            description=row["description"],
            priority=float(row["priority"]),
            success_criteria=json.loads(row["success_criteria_json"]),
            source=row["source"],
            autonomous=bool(row["autonomous"]),
            parent_goal_id=row["parent_goal_id"],
            deadline=row["deadline"],
            status=GoalStatus(row["status"]),
            progress=float(row["progress"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            rationale=row["rationale"],
            origin=row["origin"],
            task_spec=json.loads(row["task_spec_json"]),
            risk=RiskLevel(row["risk"]),
        )


class MemoryStore:
    def __init__(self, db: Database, ledger: EvidenceLedger):
        self.db = db
        self.ledger = ledger
        self.causal = CausalMemoryIndex(db, ledger)

    def add(self, memory: MemoryItem) -> str:
        normalized = self._text(memory.content)
        with self.db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO memories(
                    memory_id,memory_type,content_json,normalized_text,importance,confidence,
                    source_ids_json,tags_json,created_at,last_accessed_at,access_count,active
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,1)
                """,
                (
                    memory.memory_id,
                    memory.memory_type,
                    json.dumps(memory.content, ensure_ascii=False, sort_keys=True),
                    normalized,
                    memory.importance,
                    memory.confidence,
                    json.dumps(memory.source_ids, ensure_ascii=False),
                    json.dumps(memory.tags, ensure_ascii=False),
                    memory.created_at,
                    memory.last_accessed_at,
                    memory.access_count,
                ),
            )
            self.ledger.append(
                "memory_created",
                {
                    "memory_id": memory.memory_id,
                    "memory_type": memory.memory_type,
                    "source_ids": memory.source_ids,
                },
                connection,
            )
        self.causal.index_memory(memory.memory_id)
        return memory.memory_id

    def retrieve(
        self, query: str, limit: int = 8, memory_types: list[str] | None = None
    ) -> list[dict[str, Any]]:
        query_tokens = self._tokens(query)
        rows = self.db.query_all("SELECT * FROM memories WHERE active=1")
        if memory_types:
            allowed_types = set(memory_types)
            rows = [row for row in rows if str(row["memory_type"]) in allowed_types]
        scored: list[tuple[float, sqlite3.Row]] = []
        for row in rows:
            text_tokens = self._tokens(row["normalized_text"])
            overlap = len(query_tokens & text_tokens) / max(1, len(query_tokens | text_tokens))
            score = (
                0.55 * overlap
                + 0.3 * float(row["importance"])
                + 0.15 * float(row["confidence"])
            )
            if score > 0.05 or not query_tokens:
                scored.append((score, row))
        scored.sort(key=lambda item: (item[0], item[1]["created_at"]), reverse=True)
        selected = scored[:limit]
        if selected:
            with self.db.transaction() as connection:
                for score, row in selected:
                    connection.execute(
                        "UPDATE memories SET access_count=access_count+1,last_accessed_at=? WHERE memory_id=?",
                        (utc_now(), row["memory_id"]),
                    )
        return [
            {
                "memory_id": row["memory_id"],
                "memory_type": row["memory_type"],
                "content": json.loads(row["content_json"]),
                "importance": float(row["importance"]),
                "confidence": float(row["confidence"]),
                "source_ids": json.loads(row["source_ids_json"]),
                "tags": json.loads(row["tags_json"]),
                "score": score,
            }
            for score, row in selected
        ]

    def retrieve_causal(
        self,
        query: str,
        limit: int = 8,
        *,
        context: dict[str, Any] | None = None,
        enabled: bool = True,
        frozen: bool = False,
        excluded_memory_types: frozenset[str] = frozenset(),
    ) -> dict[str, Any]:
        return self.causal.retrieve(
            query,
            limit,
            context=context,
            enabled=enabled,
            frozen=frozen,
            excluded_memory_types=excluded_memory_types,
        )

    def build_query_context(
        self, events: Iterable[Any], goals: Iterable[Any]
    ) -> dict[str, Any]:
        return self.causal.build_query_context(events, goals)

    def record_decision_outcome(
        self,
        memory_ids: Iterable[str],
        *,
        success: bool | None,
        prediction_statuses: Iterable[str],
        source_ids: Iterable[str],
    ) -> list[dict[str, Any]]:
        return self.causal.record_outcome(
            memory_ids,
            success=success,
            prediction_statuses=prediction_statuses,
            source_ids=source_ids,
        )

    def memory_state(self, memory_id: str) -> dict[str, Any]:
        return self.causal.state(memory_id)

    def memory_summary(self) -> dict[str, Any]:
        return self.causal.summary()

    def memory_integrity(self) -> tuple[bool, dict[str, int]]:
        return self.causal.integrity()

    def recent(
        self, memory_type: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        if memory_type:
            rows = self.db.query_all(
                "SELECT * FROM memories WHERE active=1 AND memory_type=? ORDER BY created_at DESC LIMIT ?",
                (memory_type, limit),
            )
        else:
            rows = self.db.query_all(
                "SELECT * FROM memories WHERE active=1 ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        return [
            {
                "memory_id": row["memory_id"],
                "memory_type": row["memory_type"],
                "content": json.loads(row["content_json"]),
                "importance": float(row["importance"]),
                "confidence": float(row["confidence"]),
                "source_ids": json.loads(row["source_ids_json"]),
                "tags": json.loads(row["tags_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def deactivate(self, memory_id: str) -> None:
        self.db.execute("UPDATE memories SET active=0 WHERE memory_id=?", (memory_id,))
        state = self.causal.state(memory_id)["validity_state"]
        if state not in {"REFUTED", "EXPIRED", "SUPERSEDED"}:
            self.causal.set_state(
                memory_id,
                "SUPERSEDED",
                "memory deactivated",
                ["MemoryStore.deactivate"],
            )

    @staticmethod
    def _text(content: dict[str, Any]) -> str:
        return json.dumps(content, ensure_ascii=False, sort_keys=True).lower()

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return tokens(text)
