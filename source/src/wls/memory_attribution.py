from __future__ import annotations

from typing import Any, Iterable
import json

from .db import Database
from .evidence import EvidenceLedger
from .memory_index import CausalMemoryIndex
from .schemas import utc_now


def ensure_memory_attribution_tables(db: Database) -> None:
    statements = [
        """
        CREATE TABLE IF NOT EXISTS memory_decision_attributions (
            decision_trace_id TEXT PRIMARY KEY,
            cycle_id TEXT NOT NULL UNIQUE,
            selected_memory_ids_json TEXT NOT NULL,
            suppressed_memory_ids_json TEXT NOT NULL,
            counterfactual_without_memory_json TEXT NOT NULL,
            memory_changed_decision INTEGER NOT NULL,
            memory_delta REAL NOT NULL,
            causal_reason TEXT NOT NULL,
            retrieval_json TEXT NOT NULL,
            outcome_json TEXT,
            created_at TEXT NOT NULL,
            resolved_at TEXT,
            FOREIGN KEY(decision_trace_id) REFERENCES cognitive_traces(trace_id)
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_memory_attribution_cycle
        ON memory_decision_attributions(cycle_id, created_at DESC)
        """,
    ]
    with db.transaction() as connection:
        for statement in statements:
            connection.execute(statement)


class MemoryAttributionStore:
    """Persist which memories changed a bounded cognition decision and its outcome."""

    def __init__(
        self,
        db: Database,
        ledger: EvidenceLedger,
        memory_index: CausalMemoryIndex,
    ) -> None:
        self.db = db
        self.ledger = ledger
        self.memory_index = memory_index
        ensure_memory_attribution_tables(db)

    def record(
        self,
        cycle_id: str,
        selected_memory_ids: Iterable[str],
        retrieval: dict[str, Any],
    ) -> dict[str, Any] | None:
        trace = self.db.query_one(
            "SELECT * FROM cognitive_traces WHERE cycle_id=?", (cycle_id,)
        )
        if trace is None:
            return None
        selected_ids = list(
            dict.fromkeys(str(value) for value in selected_memory_ids if str(value))
        )
        suppressed = [
            dict(item)
            for item in retrieval.get("suppressed", [])
            if isinstance(item, dict)
        ]
        suppressed_ids = list(
            dict.fromkeys(
                str(item.get("memory_id"))
                for item in suppressed
                if item.get("memory_id")
            )
        )
        selected_hypothesis = self.db.query_one(
            "SELECT * FROM cognitive_hypotheses WHERE hypothesis_id=?",
            (trace["selected_hypothesis_id"],),
        )
        counterfactual = self.db.query_one(
            """
            SELECT * FROM cognitive_hypotheses
            WHERE trace_id=? AND hypothesis_key=?
            ORDER BY score DESC LIMIT 1
            """,
            (trace["trace_id"], trace["counterfactual_key"]),
        )
        counterfactual_payload: dict[str, Any] = {
            "key": str(trace["counterfactual_key"]),
            "persisted_hypothesis": counterfactual is not None,
        }
        if counterfactual is not None:
            counterfactual_payload.update(
                {
                    "claim": str(counterfactual["claim"]),
                    "rationale": str(counterfactual["rationale"]),
                    "score": float(counterfactual["score"]),
                    "actions": json.loads(counterfactual["actions_json"]),
                }
            )
        selected_lookup = {
            str(item.get("memory_id")): item
            for item in retrieval.get("selected", [])
            if isinstance(item, dict) and item.get("memory_id")
        }
        retrieval_payload = {
            "mode": retrieval.get("mode"),
            "query_context": retrieval.get("query_context", {}),
            "selected": [
                {
                    "memory_id": memory_id,
                    "retrieval_reasons": selected_lookup.get(memory_id, {}).get(
                        "retrieval_reasons", []
                    ),
                    "causal_score": selected_lookup.get(memory_id, {}).get(
                        "causal_score", 0.0
                    ),
                    "validity_state": selected_lookup.get(memory_id, {}).get(
                        "validity_state"
                    ),
                }
                for memory_id in selected_ids
            ],
            "suppressed": suppressed,
        }
        selected_key = str(trace["selected_key"])
        selected_rationale = (
            str(selected_hypothesis["rationale"])
            if selected_hypothesis is not None
            else "selected hypothesis row unavailable"
        )
        reason_parts = [
            f"selected={selected_key}",
            f"counterfactual={trace['counterfactual_key']}",
            selected_rationale,
        ]
        for memory_id in selected_ids:
            reasons = selected_lookup.get(memory_id, {}).get("retrieval_reasons", [])
            if reasons:
                reason_parts.append(
                    f"{memory_id} matched {','.join(str(value) for value in reasons)}"
                )
        causal_reason = "; ".join(reason_parts)[:4000]
        now = utc_now()
        with self.db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO memory_decision_attributions(
                    decision_trace_id,cycle_id,selected_memory_ids_json,
                    suppressed_memory_ids_json,counterfactual_without_memory_json,
                    memory_changed_decision,memory_delta,causal_reason,
                    retrieval_json,outcome_json,created_at,resolved_at
                ) VALUES (?,?,?,?,?,?,?,?,?,NULL,?,NULL)
                ON CONFLICT(decision_trace_id) DO UPDATE SET
                    selected_memory_ids_json=excluded.selected_memory_ids_json,
                    suppressed_memory_ids_json=excluded.suppressed_memory_ids_json,
                    counterfactual_without_memory_json=excluded.counterfactual_without_memory_json,
                    memory_changed_decision=excluded.memory_changed_decision,
                    memory_delta=excluded.memory_delta,
                    causal_reason=excluded.causal_reason,
                    retrieval_json=excluded.retrieval_json
                """,
                (
                    trace["trace_id"],
                    cycle_id,
                    json.dumps(selected_ids, ensure_ascii=False),
                    json.dumps(suppressed_ids, ensure_ascii=False),
                    json.dumps(counterfactual_payload, ensure_ascii=False, sort_keys=True),
                    int(bool(trace["memory_changed_decision"])),
                    float(trace["memory_delta"]),
                    causal_reason,
                    json.dumps(retrieval_payload, ensure_ascii=False, sort_keys=True),
                    now,
                ),
            )
            self.ledger.append(
                "memory_decision_attributed",
                {
                    "decision_trace_id": trace["trace_id"],
                    "cycle_id": cycle_id,
                    "selected_memory_ids": selected_ids,
                    "suppressed_memory_ids": suppressed_ids,
                    "counterfactual_without_memory": counterfactual_payload,
                    "memory_changed_decision": bool(trace["memory_changed_decision"]),
                    "memory_delta": float(trace["memory_delta"]),
                    "causal_reason": causal_reason,
                },
                connection,
            )
        return self.get(cycle_id)

    def resolve(
        self,
        cycle_id: str,
        outcomes: list[dict[str, Any]],
        cognition_result: dict[str, Any] | None,
        *,
        frozen: bool = False,
    ) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT * FROM memory_decision_attributions WHERE cycle_id=?", (cycle_id,)
        )
        if row is None:
            return None
        observed = [
            bool(item.get("success"))
            for item in outcomes
            if isinstance(item, dict) and item.get("action_id")
        ]
        task_success = all(observed) if observed else None
        predictions = (
            cognition_result.get("predictions", [])
            if isinstance(cognition_result, dict)
            else []
        )
        prediction_statuses = [
            str(item.get("status", "UNKNOWN"))
            for item in predictions
            if isinstance(item, dict)
        ]
        selected_ids = json.loads(row["selected_memory_ids_json"])
        attributable = bool(row["memory_changed_decision"]) and bool(selected_ids)
        source_ids = [
            str(item.get("action_id"))
            for item in outcomes
            if isinstance(item, dict) and item.get("action_id")
        ]
        source_ids.append(str(row["decision_trace_id"]))
        transitions = (
            self.memory_index.record_outcome(
                selected_ids,
                success=task_success,
                prediction_statuses=prediction_statuses,
                source_ids=source_ids,
            )
            if attributable and not frozen
            else []
        )
        outcome = {
            "task_success": task_success,
            "prediction_statuses": prediction_statuses,
            "attributable": attributable,
            "frozen": frozen,
            "memory_state_transitions": transitions,
        }
        now = utc_now()
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE memory_decision_attributions SET outcome_json=?,resolved_at=? WHERE cycle_id=?",
                (json.dumps(outcome, ensure_ascii=False, sort_keys=True), now, cycle_id),
            )
            self.ledger.append(
                "memory_decision_outcome_resolved",
                {
                    "cycle_id": cycle_id,
                    "decision_trace_id": row["decision_trace_id"],
                    "outcome": outcome,
                },
                connection,
            )
        return {**self.get(cycle_id), "memory_state_transitions": transitions}

    def get(self, cycle_id: str) -> dict[str, Any]:
        row = self.db.query_one(
            "SELECT * FROM memory_decision_attributions WHERE cycle_id=?", (cycle_id,)
        )
        if row is None:
            raise KeyError(cycle_id)
        return self._render(row)

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.db.query_all(
            "SELECT * FROM memory_decision_attributions ORDER BY created_at DESC LIMIT ?",
            (max(1, min(500, int(limit))),),
        )
        return [self._render(row) for row in rows]

    def summary(self) -> dict[str, Any]:
        row = self.db.query_one(
            """
            SELECT COUNT(*) AS n,
                   SUM(memory_changed_decision) AS changed,
                   SUM(CASE WHEN resolved_at IS NOT NULL THEN 1 ELSE 0 END) AS resolved
            FROM memory_decision_attributions
            """
        )
        return {
            "attributions": int(row["n"] or 0) if row else 0,
            "memory_changed_decisions": int(row["changed"] or 0) if row else 0,
            "resolved": int(row["resolved"] or 0) if row else 0,
        }

    def integrity(self) -> tuple[bool, dict[str, int]]:
        checks = {
            "orphan_attributions": """
                SELECT COUNT(*) AS n FROM memory_decision_attributions a
                LEFT JOIN cognitive_traces t ON t.trace_id=a.decision_trace_id
                WHERE t.trace_id IS NULL
            """,
            "resolved_without_outcome": """
                SELECT COUNT(*) AS n FROM memory_decision_attributions
                WHERE resolved_at IS NOT NULL AND outcome_json IS NULL
            """,
        }
        counts: dict[str, int] = {}
        for name, sql in checks.items():
            row = self.db.query_one(sql)
            counts[name] = int(row["n"]) if row else 0
        return all(value == 0 for value in counts.values()), counts

    @staticmethod
    def _render(row: Any) -> dict[str, Any]:
        return {
            "decision_trace_id": row["decision_trace_id"],
            "cycle_id": row["cycle_id"],
            "selected_memory_ids": json.loads(row["selected_memory_ids_json"]),
            "suppressed_memory_ids": json.loads(row["suppressed_memory_ids_json"]),
            "counterfactual_without_memory": json.loads(
                row["counterfactual_without_memory_json"]
            ),
            "memory_changed_decision": bool(row["memory_changed_decision"]),
            "memory_delta": float(row["memory_delta"]),
            "causal_reason": row["causal_reason"],
            "retrieval": json.loads(row["retrieval_json"]),
            "outcome": json.loads(row["outcome_json"]) if row["outcome_json"] else None,
            "created_at": row["created_at"],
            "resolved_at": row["resolved_at"],
        }
