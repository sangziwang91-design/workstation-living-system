from __future__ import annotations

from typing import Any
import json
import secrets

from .schemas import utc_now


class RuntimeTraceStore:
    """Persistent trace/span records correlated to WLS cycle, plan, action, and command IDs."""

    KINDS = {"INTERNAL", "SERVER", "CLIENT", "PRODUCER", "CONSUMER"}
    TERMINAL = {"OK", "ERROR", "CANCELLED"}

    def __init__(self, db: Any, ledger: Any) -> None:
        self.db = db
        self.ledger = ledger
        with db.transaction() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS runtime_spans(
                    span_id TEXT PRIMARY KEY,
                    trace_id TEXT NOT NULL,
                    parent_span_id TEXT,
                    name TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attributes_json TEXT NOT NULL,
                    cycle_id TEXT,
                    plan_id TEXT,
                    action_id TEXT,
                    command_id TEXT,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    error TEXT,
                    FOREIGN KEY(parent_span_id) REFERENCES runtime_spans(span_id)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_runtime_spans_trace ON runtime_spans(trace_id,started_at)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS runtime_span_events(
                    event_id TEXT PRIMARY KEY,
                    span_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    attributes_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    FOREIGN KEY(span_id) REFERENCES runtime_spans(span_id)
                )
                """
            )

    def start(
        self,
        name: str,
        *,
        trace_id: str | None = None,
        parent_span_id: str | None = None,
        kind: str = "INTERNAL",
        attributes: dict[str, Any] | None = None,
        cycle_id: str | None = None,
        plan_id: str | None = None,
        action_id: str | None = None,
        command_id: str | None = None,
    ) -> dict[str, str]:
        if not name.strip():
            raise ValueError("span name is required")
        if kind not in self.KINDS:
            raise ValueError("invalid span kind")
        attrs = json.loads(json.dumps(attributes or {}, ensure_ascii=False, sort_keys=True))
        span_id = f"span_{secrets.token_hex(8)}"
        with self.db.transaction() as connection:
            if parent_span_id:
                parent = connection.execute(
                    "SELECT trace_id FROM runtime_spans WHERE span_id=?", (parent_span_id,)
                ).fetchone()
                if parent is None:
                    raise KeyError(parent_span_id)
                inherited = str(parent["trace_id"])
                if trace_id and trace_id != inherited:
                    raise ValueError("child trace_id must match parent")
                trace_id = inherited
            trace_id = trace_id or f"trace_{secrets.token_hex(16)}"
            connection.execute(
                """
                INSERT INTO runtime_spans(
                    span_id,trace_id,parent_span_id,name,kind,status,attributes_json,
                    cycle_id,plan_id,action_id,command_id,started_at
                ) VALUES (?,?,?,?,?,'RUNNING',?,?,?,?,?,?)
                """,
                (
                    span_id,
                    trace_id,
                    parent_span_id,
                    name,
                    kind,
                    json.dumps(attrs, ensure_ascii=False, sort_keys=True),
                    cycle_id,
                    plan_id,
                    action_id,
                    command_id,
                    utc_now(),
                ),
            )
            self.ledger.append(
                "runtime_span_started",
                {
                    "span_id": span_id,
                    "trace_id": trace_id,
                    "parent_span_id": parent_span_id,
                    "name": name,
                    "kind": kind,
                },
                connection,
            )
        return {"span_id": span_id, "trace_id": trace_id}

    def event(self, span_id: str, name: str, attributes: dict[str, Any] | None = None) -> str:
        if not name.strip():
            raise ValueError("event name is required")
        attrs = json.loads(json.dumps(attributes or {}, ensure_ascii=False, sort_keys=True))
        event_id = f"spanevent_{secrets.token_hex(8)}"
        with self.db.transaction() as connection:
            row = connection.execute(
                "SELECT status,trace_id FROM runtime_spans WHERE span_id=?", (span_id,)
            ).fetchone()
            if row is None:
                raise KeyError(span_id)
            if str(row["status"]) != "RUNNING":
                raise RuntimeError("cannot append to terminal span")
            connection.execute(
                "INSERT INTO runtime_span_events(event_id,span_id,name,attributes_json,recorded_at) VALUES (?,?,?,?,?)",
                (
                    event_id,
                    span_id,
                    name,
                    json.dumps(attrs, ensure_ascii=False, sort_keys=True),
                    utc_now(),
                ),
            )
            self.ledger.append(
                "runtime_span_event",
                {
                    "event_id": event_id,
                    "span_id": span_id,
                    "trace_id": str(row["trace_id"]),
                    "name": name,
                },
                connection,
            )
        return event_id

    def end(
        self,
        span_id: str,
        *,
        status: str = "OK",
        error: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if status not in self.TERMINAL:
            raise ValueError("invalid terminal span status")
        attrs = json.loads(json.dumps(attributes or {}, ensure_ascii=False, sort_keys=True))
        with self.db.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM runtime_spans WHERE span_id=?", (span_id,)
            ).fetchone()
            if row is None:
                raise KeyError(span_id)
            if str(row["status"]) != "RUNNING":
                return self.get(span_id)
            merged = json.loads(row["attributes_json"])
            merged.update(attrs)
            connection.execute(
                "UPDATE runtime_spans SET status=?,attributes_json=?,ended_at=?,error=? WHERE span_id=?",
                (
                    status,
                    json.dumps(merged, ensure_ascii=False, sort_keys=True),
                    utc_now(),
                    str(error)[:4000] if error else None,
                    span_id,
                ),
            )
            self.ledger.append(
                "runtime_span_ended",
                {
                    "span_id": span_id,
                    "trace_id": str(row["trace_id"]),
                    "status": status,
                },
                connection,
            )
        return self.get(span_id)

    def get(self, span_id: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM runtime_spans WHERE span_id=?", (span_id,))
        if row is None:
            raise KeyError(span_id)
        events = self.db.query_all(
            "SELECT * FROM runtime_span_events WHERE span_id=? ORDER BY recorded_at",
            (span_id,),
        )
        return {
            "span_id": str(row["span_id"]),
            "trace_id": str(row["trace_id"]),
            "parent_span_id": str(row["parent_span_id"]) if row["parent_span_id"] else None,
            "name": str(row["name"]),
            "kind": str(row["kind"]),
            "status": str(row["status"]),
            "attributes": json.loads(row["attributes_json"]),
            "cycle_id": row["cycle_id"],
            "plan_id": row["plan_id"],
            "action_id": row["action_id"],
            "command_id": row["command_id"],
            "started_at": str(row["started_at"]),
            "ended_at": str(row["ended_at"]) if row["ended_at"] else None,
            "error": str(row["error"]) if row["error"] else None,
            "events": [
                {
                    "event_id": str(item["event_id"]),
                    "name": str(item["name"]),
                    "attributes": json.loads(item["attributes_json"]),
                    "recorded_at": str(item["recorded_at"]),
                }
                for item in events
            ],
        }

    def integrity(self) -> tuple[bool, dict[str, int]]:
        checks = {
            "terminal_without_end": "SELECT COUNT(*) AS n FROM runtime_spans WHERE status<>'RUNNING' AND ended_at IS NULL",
            "running_with_end": "SELECT COUNT(*) AS n FROM runtime_spans WHERE status='RUNNING' AND ended_at IS NOT NULL",
            "orphan_parent": "SELECT COUNT(*) AS n FROM runtime_spans c LEFT JOIN runtime_spans p ON p.span_id=c.parent_span_id WHERE c.parent_span_id IS NOT NULL AND p.span_id IS NULL",
            "trace_mismatch": "SELECT COUNT(*) AS n FROM runtime_spans c JOIN runtime_spans p ON p.span_id=c.parent_span_id WHERE c.trace_id<>p.trace_id",
            "orphan_event": "SELECT COUNT(*) AS n FROM runtime_span_events e LEFT JOIN runtime_spans s ON s.span_id=e.span_id WHERE s.span_id IS NULL",
        }
        counts: dict[str, int] = {}
        for name, sql in checks.items():
            row = self.db.query_one(sql)
            counts[name] = int(row["n"]) if row else 0
        return all(value == 0 for value in counts.values()), counts
