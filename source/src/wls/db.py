from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence
import json
import sqlite3
import threading

from .schemas import utc_now


SCHEMA_VERSION = 1


class Database:
    """Single-file durable state store.

    Connections are short-lived, WAL-backed, and transaction-scoped. The class
    keeps a process-local reentrant lock to serialize schema and evidence-chain
    operations while SQLite handles cross-process locking.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    @contextmanager
    def transaction(self, immediate: bool = True) -> Iterator[sqlite3.Connection]:
        with self._lock:
            connection = self.connect()
            try:
                connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
                yield connection
                if connection.in_transaction:
                    connection.execute("COMMIT")
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
            finally:
                connection.close()

    def initialize(self) -> None:
        with self.transaction() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    source TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    salience_hint REAL NOT NULL,
                    occurred_at TEXT NOT NULL,
                    dedupe_key TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    reserved_by TEXT,
                    reserved_at TEXT,
                    processed_at TEXT,
                    last_error TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_events_status_time ON events(status, occurred_at);

                CREATE TABLE IF NOT EXISTS observations (
                    observation_id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    value_json TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    evidence_kind TEXT NOT NULL,
                    verification TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    expires_at TEXT,
                    metadata_json TEXT NOT NULL,
                    event_id TEXT,
                    FOREIGN KEY(event_id) REFERENCES events(event_id)
                );
                CREATE INDEX IF NOT EXISTS idx_observations_subject_predicate ON observations(subject, predicate, observed_at);

                CREATE TABLE IF NOT EXISTS world_facts (
                    fact_id TEXT PRIMARY KEY,
                    subject TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    value_json TEXT NOT NULL,
                    source_kind TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    verification TEXT NOT NULL,
                    source_ids_json TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    valid_until TEXT,
                    active INTEGER NOT NULL DEFAULT 1,
                    supersedes_fact_id TEXT,
                    contradiction_group TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_world_facts_key ON world_facts(subject, predicate, active, last_seen_at);

                CREATE TABLE IF NOT EXISTS predictions (
                    prediction_id TEXT PRIMARY KEY,
                    subject TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    expected_value_json TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    due_at TEXT,
                    source TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    resolved_at TEXT,
                    actual_value_json TEXT,
                    error_score REAL
                );

                CREATE TABLE IF NOT EXISTS goals (
                    goal_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    priority REAL NOT NULL,
                    success_criteria_json TEXT NOT NULL,
                    source TEXT NOT NULL,
                    autonomous INTEGER NOT NULL,
                    parent_goal_id TEXT,
                    deadline TEXT,
                    status TEXT NOT NULL,
                    progress REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_goals_status_priority ON goals(status, priority DESC);

                CREATE TABLE IF NOT EXISTS memories (
                    memory_id TEXT PRIMARY KEY,
                    memory_type TEXT NOT NULL,
                    content_json TEXT NOT NULL,
                    normalized_text TEXT NOT NULL,
                    importance REAL NOT NULL,
                    confidence REAL NOT NULL,
                    source_ids_json TEXT NOT NULL,
                    tags_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_accessed_at TEXT NOT NULL,
                    access_count INTEGER NOT NULL DEFAULT 0,
                    active INTEGER NOT NULL DEFAULT 1
                );
                CREATE INDEX IF NOT EXISTS idx_memories_type_active ON memories(memory_type, active, importance DESC);

                CREATE TABLE IF NOT EXISTS plans (
                    plan_id TEXT PRIMARY KEY,
                    cycle_id TEXT NOT NULL,
                    plan_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    completed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS actions (
                    action_id TEXT PRIMARY KEY,
                    plan_id TEXT NOT NULL,
                    goal_id TEXT,
                    skill_id TEXT,
                    tool TEXT NOT NULL,
                    arguments_json TEXT NOT NULL,
                    purpose TEXT NOT NULL,
                    expected_result TEXT NOT NULL,
                    risk TEXT NOT NULL,
                    acceptance_json TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    status TEXT NOT NULL,
                    approval_id TEXT,
                    started_at TEXT,
                    finished_at TEXT,
                    result_json TEXT,
                    error TEXT,
                    side_effect_class TEXT NOT NULL DEFAULT 'unknown',
                    FOREIGN KEY(plan_id) REFERENCES plans(plan_id)
                );
                CREATE INDEX IF NOT EXISTS idx_actions_idempotency_success
                    ON actions(idempotency_key) WHERE status='SUCCEEDED';
                CREATE INDEX IF NOT EXISTS idx_actions_status ON actions(status, started_at);

                CREATE TABLE IF NOT EXISTS approvals (
                    approval_id TEXT PRIMARY KEY,
                    action_id TEXT NOT NULL,
                    action_digest TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    issued_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT,
                    nonce TEXT NOT NULL UNIQUE,
                    signature TEXT NOT NULL,
                    reason TEXT,
                    FOREIGN KEY(action_id) REFERENCES actions(action_id)
                );

                CREATE TABLE IF NOT EXISTS drive_state (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    state_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS affect_state (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    state_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS self_model (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    evidence_ids_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS relationships (
                    relation_id TEXT PRIMARY KEY,
                    subject TEXT NOT NULL,
                    relation_type TEXT NOT NULL,
                    value_json TEXT NOT NULL,
                    stability TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    source_ids_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS skills (
                    skill_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    definition_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    success_rate REAL NOT NULL,
                    use_count INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(name, version)
                );

                CREATE TABLE IF NOT EXISTS evolution_candidates (
                    candidate_id TEXT PRIMARY KEY,
                    candidate_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    proposal_json TEXT NOT NULL,
                    source_ids_json TEXT NOT NULL,
                    baseline_json TEXT,
                    experiment_json TEXT,
                    result_json TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS cycles (
                    cycle_id TEXT PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    status TEXT NOT NULL,
                    workspace_json TEXT,
                    metrics_json TEXT,
                    error TEXT
                );

                CREATE TABLE IF NOT EXISTS sensor_state (
                    sensor_name TEXT PRIMARY KEY,
                    state_json TEXT NOT NULL,
                    last_polled_at TEXT,
                    last_success_at TEXT,
                    last_error TEXT
                );

                CREATE TABLE IF NOT EXISTS runtime_state (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS evidence (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    evidence_id TEXT NOT NULL UNIQUE,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    record_hash TEXT NOT NULL UNIQUE,
                    signature TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS metrics (
                    metric_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    value REAL NOT NULL,
                    labels_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_metrics_name_time ON metrics(name, recorded_at);

                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );
                """
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (SCHEMA_VERSION, utc_now()),
            )

    def execute(self, sql: str, parameters: Sequence[Any] = ()) -> int:
        with self.transaction() as connection:
            cursor = connection.execute(sql, parameters)
            return cursor.rowcount

    def query_one(self, sql: str, parameters: Sequence[Any] = ()) -> sqlite3.Row | None:
        connection = self.connect()
        try:
            return connection.execute(sql, parameters).fetchone()
        finally:
            connection.close()

    def query_all(self, sql: str, parameters: Sequence[Any] = ()) -> list[sqlite3.Row]:
        connection = self.connect()
        try:
            return list(connection.execute(sql, parameters).fetchall())
        finally:
            connection.close()

    def set_runtime(
        self, key: str, value: Any, connection: sqlite3.Connection | None = None
    ) -> None:
        sql = """
            INSERT INTO runtime_state(key, value_json, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at
        """
        params = (key, json.dumps(value, ensure_ascii=False), utc_now())
        if connection is not None:
            connection.execute(sql, params)
        else:
            self.execute(sql, params)

    def get_runtime(self, key: str, default: Any = None) -> Any:
        row = self.query_one("SELECT value_json FROM runtime_state WHERE key=?", (key,))
        return default if row is None else json.loads(row["value_json"])

    def integrity_check(self) -> tuple[bool, str]:
        connection = self.connect()
        try:
            row = connection.execute("PRAGMA integrity_check").fetchone()
            result = str(row[0]) if row else "missing"
            foreign_rows = connection.execute("PRAGMA foreign_key_check").fetchall()
            if result.lower() != "ok":
                return False, result
            if foreign_rows:
                return False, f"foreign_key_violations={len(foreign_rows)}"
            return True, "ok"
        finally:
            connection.close()

    def checkpoint(self) -> None:
        connection = self.connect()
        try:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            connection.close()
