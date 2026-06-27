from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable
import hashlib
import json
import time

from .db import Database
from .evidence import EvidenceLedger
from .schemas import canonical_json, new_id, utc_now


SHADOW_SCHEMA = """
CREATE TABLE IF NOT EXISTS shadow_causal_runs (
    run_id TEXT PRIMARY KEY,
    cycle_id TEXT NOT NULL UNIQUE,
    plan_id TEXT NOT NULL UNIQUE,
    mode TEXT NOT NULL CHECK(mode='SHADOW_READ_ONLY'),
    status TEXT NOT NULL CHECK(status IN (
        'FROZEN','RESOLVED','ABSTAINED','UNRESOLVED','ABORTED','ERROR'
    )),
    active_spaces_json TEXT NOT NULL,
    activation_reasons_json TEXT NOT NULL,
    source_event_ids_json TEXT NOT NULL,
    proposal_json TEXT NOT NULL,
    proposal_hash TEXT NOT NULL,
    frozen_evidence_id TEXT NOT NULL,
    frozen_evidence_seq INTEGER NOT NULL,
    frozen_at TEXT NOT NULL,
    resolved_evidence_id TEXT,
    resolved_evidence_seq INTEGER,
    resolved_at TEXT,
    error TEXT
);

CREATE TRIGGER IF NOT EXISTS prevent_shadow_proposal_rewrite
BEFORE UPDATE ON shadow_causal_runs
WHEN NEW.proposal_json <> OLD.proposal_json
  OR NEW.proposal_hash <> OLD.proposal_hash
  OR NEW.frozen_evidence_id <> OLD.frozen_evidence_id
  OR NEW.frozen_evidence_seq <> OLD.frozen_evidence_seq
  OR NEW.frozen_at <> OLD.frozen_at
BEGIN
    SELECT RAISE(ABORT, 'frozen shadow proposal is immutable');
END;

CREATE TABLE IF NOT EXISTS shadow_causal_candidates (
    candidate_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    candidate_key TEXT NOT NULL,
    origin TEXT NOT NULL CHECK(origin IN ('REGISTERED','OPEN_SET')),
    status TEXT NOT NULL CHECK(status IN (
        'PROPOSED','HYPOTHESIS','NOT_IDENTIFIABLE','INSUFFICIENT_EVIDENCE'
    )),
    relation_kind TEXT NOT NULL CHECK(relation_kind IN (
        'CORRELATES_WITH','CONTRIBUTES_TO','INFERRED_CAUSE','UNKNOWN'
    )),
    confidence REAL NOT NULL CHECK(confidence >= 0.0 AND confidence < 1.0),
    verification_scope TEXT NOT NULL CHECK(verification_scope='SHADOW_READ_ONLY'),
    evidence_json TEXT NOT NULL,
    mechanism_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(run_id, candidate_key),
    FOREIGN KEY(run_id) REFERENCES shadow_causal_runs(run_id) ON DELETE CASCADE,
    CHECK(NOT(origin='OPEN_SET' AND status NOT IN (
        'PROPOSED','HYPOTHESIS','NOT_IDENTIFIABLE','INSUFFICIENT_EVIDENCE'
    )))
);

CREATE TRIGGER IF NOT EXISTS prevent_shadow_candidate_update
BEFORE UPDATE ON shadow_causal_candidates
BEGIN
    SELECT RAISE(ABORT, 'shadow candidates are immutable');
END;

CREATE TABLE IF NOT EXISTS shadow_causal_resolutions (
    resolution_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE,
    outcome_class TEXT NOT NULL CHECK(outcome_class IN (
        'MATCH','MISS','ABSTAIN','UNRESOLVED'
    )),
    predicted_json TEXT NOT NULL,
    actual_json TEXT NOT NULL,
    first_action_outcome_seq INTEGER,
    frozen_before_outcome INTEGER NOT NULL CHECK(frozen_before_outcome IN (0,1)),
    created_at TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES shadow_causal_runs(run_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS shadow_causal_metrics (
    metric_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    phase TEXT NOT NULL,
    duration_ms REAL NOT NULL CHECK(duration_ms >= 0.0),
    payload_json TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES shadow_causal_runs(run_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_shadow_runs_status
ON shadow_causal_runs(status, frozen_at);

CREATE INDEX IF NOT EXISTS idx_shadow_candidates_run
ON shadow_causal_candidates(run_id, origin, status);
"""


@dataclass(frozen=True, slots=True)
class ShadowFreezeResult:
    run_id: str
    cycle_id: str
    plan_id: str
    proposal_hash: str
    frozen_evidence_seq: int
    active_spaces: tuple[str, ...]
    candidate_count: int
    idempotent: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "cycle_id": self.cycle_id,
            "plan_id": self.plan_id,
            "proposal_hash": self.proposal_hash,
            "frozen_evidence_seq": self.frozen_evidence_seq,
            "active_spaces": list(self.active_spaces),
            "candidate_count": self.candidate_count,
            "idempotent": self.idempotent,
        }


class ShadowCausalAnalyzer:
    """Failure-isolated read-only causal shadow over canonical WLS records."""

    def __init__(
        self,
        db: Database,
        ledger: EvidenceLedger,
        *,
        node_budget: int = 24,
        candidate_limit: int = 6,
    ) -> None:
        if not 4 <= int(node_budget) <= 128:
            raise ValueError("node_budget must be within 4..128")
        if not 1 <= int(candidate_limit) <= 32:
            raise ValueError("candidate_limit must be within 1..32")
        self.db = db
        self.ledger = ledger
        self.node_budget = int(node_budget)
        self.candidate_limit = int(candidate_limit)
        self.ensure_schema()

    def ensure_schema(self) -> None:
        with self.db.transaction() as connection:
            connection.executescript(SHADOW_SCHEMA)

    def freeze(
        self,
        *,
        cycle_id: str,
        plan: Any,
        event_ids: list[str],
        contradictions: list[dict[str, Any]] | None = None,
    ) -> ShadowFreezeResult:
        """Freeze a proposal before canonical action execution."""
        started = time.perf_counter()
        plan_dict = plan.to_dict() if hasattr(plan, "to_dict") else dict(plan)
        plan_id = str(plan_dict["plan_id"])
        existing = self.db.query_one(
            "SELECT * FROM shadow_causal_runs WHERE cycle_id=? OR plan_id=?",
            (cycle_id, plan_id),
        )
        if existing is not None:
            return self._freeze_result(existing, idempotent=True)

        events = self._load_events(event_ids)
        failure_signals = [event for event in events if self._is_failure_event(event)]
        contradictions = list(contradictions or [])
        active_spaces = ["PRESENT", "FUTURE"]
        reasons: dict[str, list[str]] = {
            "PRESENT": ["canonical cycle context available"],
            "FUTURE": ["canonical plan exists and outcomes are not yet available"],
        }
        if failure_signals or contradictions:
            active_spaces.insert(0, "PAST")
            reasons["PAST"] = []
            if failure_signals:
                reasons["PAST"].append(
                    "failure or anomaly signal requires bounded history"
                )
            if contradictions:
                reasons["PAST"].append(
                    "unresolved contradiction requires provenance review"
                )

        candidates = self._candidate_proposals(
            failure_signals=failure_signals,
            contradictions=contradictions,
        )[: self.candidate_limit]
        action_predictions = [
            {
                "action_id": str(action["action_id"]),
                "tool": str(action["tool"]),
                "expected_result": str(action.get("expected_result", "")),
                "predicted_status": "SUCCEEDED",
            }
            for action in plan_dict.get("actions", [])
        ]
        dag = self._analysis_dag(active_spaces, bool(action_predictions))
        self._assert_acyclic(dag)
        proposal = {
            "schema_version": "0.6-shadow.1",
            "mode": "SHADOW_READ_ONLY",
            "cycle_id": cycle_id,
            "plan_id": plan_id,
            "source_event_ids": [str(item["event_id"]) for item in events],
            "active_spaces": active_spaces,
            "activation_reasons": reasons,
            "node_budget": self.node_budget,
            "active_node_count": len(dag["nodes"]),
            "analysis_dag": dag,
            "candidates": candidates,
            "predictions": action_predictions,
            "abstention": None
            if action_predictions
            else "NO_CANONICAL_ACTION_TO_PREDICT",
            "claim_ceiling": {
                "registered": "HYPOTHESIS",
                "open_set": "HYPOTHESIS",
                "scope": "SHADOW_READ_ONLY",
            },
            "prohibitions": [
                "NO_CANONICAL_MUTATION",
                "NO_ACTION_EXECUTION",
                "NO_MEMORY_OR_SKILL_WRITE",
                "NO_OPEN_SET_VERIFICATION",
                "NO_FUTURE_REAL_PROBE",
            ],
        }
        proposal_hash = hashlib.sha256(
            canonical_json(proposal).encode("utf-8")
        ).hexdigest()
        run_id = new_id("shadow")
        frozen_at = utc_now()

        with self.db.transaction() as connection:
            evidence_id = self.ledger.append(
                "shadow_causal_frozen",
                {
                    "run_id": run_id,
                    "cycle_id": cycle_id,
                    "plan_id": plan_id,
                    "proposal_hash": proposal_hash,
                    "active_spaces": active_spaces,
                    "candidate_count": len(candidates),
                },
                connection,
            )
            row = connection.execute(
                "SELECT seq FROM evidence WHERE evidence_id=?", (evidence_id,)
            ).fetchone()
            if row is None:
                raise RuntimeError("frozen evidence sequence missing")
            frozen_seq = int(row["seq"])
            connection.execute(
                """
                INSERT INTO shadow_causal_runs(
                    run_id,cycle_id,plan_id,mode,status,active_spaces_json,
                    activation_reasons_json,source_event_ids_json,proposal_json,
                    proposal_hash,frozen_evidence_id,frozen_evidence_seq,frozen_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    run_id,
                    cycle_id,
                    plan_id,
                    "SHADOW_READ_ONLY",
                    "FROZEN",
                    canonical_json(active_spaces),
                    canonical_json(reasons),
                    canonical_json(proposal["source_event_ids"]),
                    canonical_json(proposal),
                    proposal_hash,
                    evidence_id,
                    frozen_seq,
                    frozen_at,
                ),
            )
            for candidate in candidates:
                connection.execute(
                    """
                    INSERT INTO shadow_causal_candidates(
                        candidate_id,run_id,candidate_key,origin,status,relation_kind,
                        confidence,verification_scope,evidence_json,mechanism_json,created_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        new_id("shadow_candidate"),
                        run_id,
                        candidate["candidate_key"],
                        candidate["origin"],
                        candidate["status"],
                        candidate["relation_kind"],
                        candidate["confidence"],
                        "SHADOW_READ_ONLY",
                        canonical_json(candidate["evidence"]),
                        canonical_json(candidate["mechanism"]),
                        frozen_at,
                    ),
                )
        self._record_metric(
            run_id,
            "FREEZE",
            started,
            {
                "active_spaces": active_spaces,
                "active_node_count": len(dag["nodes"]),
                "candidate_count": len(candidates),
            },
        )
        return ShadowFreezeResult(
            run_id=run_id,
            cycle_id=cycle_id,
            plan_id=plan_id,
            proposal_hash=proposal_hash,
            frozen_evidence_seq=frozen_seq,
            active_spaces=tuple(active_spaces),
            candidate_count=len(candidates),
        )

    def resolve(self, *, plan_id: str, outcomes: list[dict[str, Any]]) -> dict[str, Any]:
        """Resolve a previously frozen proposal without rewriting it."""
        started = time.perf_counter()
        row = self.db.query_one(
            "SELECT * FROM shadow_causal_runs WHERE plan_id=?", (plan_id,)
        )
        if row is None:
            return {"status": "NO_FROZEN_PROPOSAL", "plan_id": plan_id}
        if str(row["status"]) in {"RESOLVED", "ABSTAINED", "UNRESOLVED"}:
            existing = self.db.query_one(
                "SELECT * FROM shadow_causal_resolutions WHERE run_id=?",
                (row["run_id"],),
            )
            return {
                "status": "IDEMPOTENT",
                "run_id": str(row["run_id"]),
                "resolution": dict(existing) if existing else None,
            }

        proposal = json.loads(row["proposal_json"])
        predictions = list(proposal.get("predictions", []))
        predicted_ids = {str(item["action_id"]) for item in predictions}
        relevant = [
            dict(item)
            for item in outcomes
            if str(item.get("action_id", "")) in predicted_ids
        ]
        if not predictions:
            outcome_class = "ABSTAIN"
        elif not relevant:
            outcome_class = "UNRESOLVED"
        elif all(bool(item.get("success")) for item in relevant):
            outcome_class = "MATCH"
        else:
            outcome_class = "MISS"

        first_outcome_seq = self._first_action_outcome_seq(
            predicted_ids, int(row["frozen_evidence_seq"])
        )
        frozen_before = (
            first_outcome_seq is None
            or int(row["frozen_evidence_seq"]) < first_outcome_seq
        )
        if not frozen_before:
            raise AssertionError("shadow proposal was not frozen before action outcome")

        resolution_id = new_id("shadow_resolution")
        resolved_at = utc_now()
        with self.db.transaction() as connection:
            evidence_id = self.ledger.append(
                "shadow_causal_resolved",
                {
                    "run_id": str(row["run_id"]),
                    "cycle_id": str(row["cycle_id"]),
                    "plan_id": plan_id,
                    "outcome_class": outcome_class,
                    "first_action_outcome_seq": first_outcome_seq,
                    "frozen_evidence_seq": int(row["frozen_evidence_seq"]),
                },
                connection,
            )
            evidence_row = connection.execute(
                "SELECT seq FROM evidence WHERE evidence_id=?", (evidence_id,)
            ).fetchone()
            if evidence_row is None:
                raise RuntimeError("resolution evidence sequence missing")
            resolved_seq = int(evidence_row["seq"])
            connection.execute(
                """
                INSERT INTO shadow_causal_resolutions(
                    resolution_id,run_id,outcome_class,predicted_json,actual_json,
                    first_action_outcome_seq,frozen_before_outcome,created_at
                ) VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    resolution_id,
                    str(row["run_id"]),
                    outcome_class,
                    canonical_json(predictions),
                    canonical_json(relevant),
                    first_outcome_seq,
                    int(frozen_before),
                    resolved_at,
                ),
            )
            terminal_status = {
                "MATCH": "RESOLVED",
                "MISS": "RESOLVED",
                "ABSTAIN": "ABSTAINED",
                "UNRESOLVED": "UNRESOLVED",
            }[outcome_class]
            connection.execute(
                """
                UPDATE shadow_causal_runs
                SET status=?,resolved_evidence_id=?,resolved_evidence_seq=?,
                    resolved_at=?,error=NULL
                WHERE run_id=?
                """,
                (
                    terminal_status,
                    evidence_id,
                    resolved_seq,
                    resolved_at,
                    str(row["run_id"]),
                ),
            )
        self._record_metric(
            str(row["run_id"]),
            "RESOLVE",
            started,
            {"outcome_class": outcome_class, "outcomes": len(relevant)},
        )
        return {
            "status": "RESOLVED",
            "run_id": str(row["run_id"]),
            "outcome_class": outcome_class,
            "frozen_evidence_seq": int(row["frozen_evidence_seq"]),
            "first_action_outcome_seq": first_outcome_seq,
            "frozen_before_outcome": frozen_before,
        }

    def mark_aborted(self, *, plan_id: str, reason: str) -> None:
        row = self.db.query_one(
            "SELECT run_id,status FROM shadow_causal_runs WHERE plan_id=?", (plan_id,)
        )
        if row is None or str(row["status"]) != "FROZEN":
            return
        self.db.execute(
            "UPDATE shadow_causal_runs SET status='ABORTED',error=? WHERE run_id=?",
            (reason[:2000], str(row["run_id"])),
        )

    def recover_pending(self) -> list[dict[str, Any]]:
        recovered: list[dict[str, Any]] = []
        rows = self.db.query_all(
            "SELECT plan_id FROM shadow_causal_runs WHERE status='FROZEN' ORDER BY frozen_at"
        )
        terminal = {
            "SUCCEEDED",
            "FAILED",
            "REJECTED",
            "CANCELLED",
            "UNKNOWN_SIDE_EFFECT",
        }
        for row in rows:
            actions = self.db.query_all(
                "SELECT action_id,status,result_json,error FROM actions WHERE plan_id=?",
                (row["plan_id"],),
            )
            if not actions or any(str(item["status"]) not in terminal for item in actions):
                continue
            outcomes = [
                {
                    "action_id": str(item["action_id"]),
                    "success": str(item["status"]) == "SUCCEEDED",
                    "status": str(item["status"]),
                    "recovered_from_db": True,
                }
                for item in actions
            ]
            recovered.append(
                self.resolve(plan_id=str(row["plan_id"]), outcomes=outcomes)
            )
        return recovered

    def safe_record_error(
        self, *, phase: str, error: BaseException, plan_id: str | None = None
    ) -> None:
        try:
            run = (
                self.db.query_one(
                    "SELECT run_id FROM shadow_causal_runs WHERE plan_id=?",
                    (plan_id,),
                )
                if plan_id
                else None
            )
            run_id = str(run["run_id"]) if run else None
            payload = {
                "phase": phase,
                "error": f"{type(error).__name__}: {error}"[:2000],
                "plan_id": plan_id,
            }
            with self.db.transaction() as connection:
                self.ledger.append("shadow_causal_error", payload, connection)
                if run_id:
                    connection.execute(
                        "UPDATE shadow_causal_runs SET status='ERROR',error=? WHERE run_id=?",
                        (payload["error"], run_id),
                    )
        except Exception:
            return

    def summary(self) -> dict[str, Any]:
        counts: dict[str, Any] = {}
        for table in (
            "shadow_causal_runs",
            "shadow_causal_candidates",
            "shadow_causal_resolutions",
            "shadow_causal_metrics",
        ):
            row = self.db.query_one(f"SELECT COUNT(*) AS count FROM {table}")
            counts[table] = int(row["count"]) if row else 0
        status_rows = self.db.query_all(
            "SELECT status,COUNT(*) AS count FROM shadow_causal_runs GROUP BY status"
        )
        counts["run_statuses"] = {
            str(row["status"]): int(row["count"]) for row in status_rows
        }
        return counts

    def _load_events(self, event_ids: list[str]) -> list[dict[str, Any]]:
        if not event_ids:
            return []
        placeholders = ",".join("?" for _ in event_ids)
        rows = self.db.query_all(
            f"""
            SELECT event_id,event_type,source,payload_json,salience_hint,occurred_at,status
            FROM events WHERE event_id IN ({placeholders})
            ORDER BY occurred_at,event_id
            """,
            tuple(event_ids),
        )
        return [
            {
                "event_id": str(row["event_id"]),
                "event_type": str(row["event_type"]),
                "source": str(row["source"]),
                "payload": json.loads(row["payload_json"]),
                "salience_hint": float(row["salience_hint"]),
                "occurred_at": str(row["occurred_at"]),
                "status": str(row["status"]),
            }
            for row in rows
        ]

    def _candidate_proposals(
        self,
        *,
        failure_signals: list[dict[str, Any]],
        contradictions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        for event in failure_signals:
            candidates.append(
                {
                    "candidate_key": f"event_type:{event['event_type']}",
                    "origin": "REGISTERED",
                    "status": "HYPOTHESIS",
                    "relation_kind": "CORRELATES_WITH",
                    "confidence": 0.5,
                    "evidence": [event["event_id"]],
                    "mechanism": {
                        "statement": "Failure event type is associated with the current cycle.",
                        "causal_direction_asserted": False,
                        "required_next_evidence": (
                            "independent intervention or discriminating observation"
                        ),
                    },
                }
            )
            for key, value in self._flatten(event["payload"]):
                if not self._suspicious_field(key, value):
                    continue
                candidates.append(
                    {
                        "candidate_key": (
                            f"observed:{key}={self._bounded_value(value)}"
                        ),
                        "origin": "OPEN_SET",
                        "status": "HYPOTHESIS",
                        "relation_kind": "UNKNOWN",
                        "confidence": 0.4,
                        "evidence": [event["event_id"]],
                        "mechanism": {
                            "statement": "Observed residual feature; association only.",
                            "causal_direction_asserted": False,
                            "prohibited_from_verification": True,
                            "required_next_evidence": (
                                "preregistered discriminating experiment"
                            ),
                        },
                    }
                )
        for item in contradictions:
            subject = str(item.get("subject", "unknown"))
            predicate = str(item.get("predicate", "unknown"))
            candidates.append(
                {
                    "candidate_key": f"contradiction:{subject}:{predicate}",
                    "origin": "REGISTERED",
                    "status": "NOT_IDENTIFIABLE",
                    "relation_kind": "UNKNOWN",
                    "confidence": 0.35,
                    "evidence": [
                        str(value)
                        for value in item.get("source_ids", [])
                        if str(value)
                    ],
                    "mechanism": {
                        "statement": (
                            "Conflicting active facts prevent directional attribution."
                        ),
                        "causal_direction_asserted": False,
                        "required_next_evidence": "resolve provenance conflict",
                    },
                }
            )
        deduped: dict[str, dict[str, Any]] = {}
        for candidate in candidates:
            deduped.setdefault(candidate["candidate_key"], candidate)
        return [deduped[key] for key in sorted(deduped)]

    @staticmethod
    def _flatten(value: Any, prefix: str = "") -> Iterable[tuple[str, Any]]:
        if isinstance(value, dict):
            for key in sorted(value):
                nested = f"{prefix}.{key}" if prefix else str(key)
                yield from ShadowCausalAnalyzer._flatten(value[key], nested)
        elif isinstance(value, list):
            for index, item in enumerate(value[:8]):
                yield from ShadowCausalAnalyzer._flatten(item, f"{prefix}[{index}]")
        else:
            yield prefix, value

    @staticmethod
    def _bounded_value(value: Any) -> str:
        return canonical_json(value)[:160]

    @staticmethod
    def _suspicious_field(key: str, value: Any) -> bool:
        lowered = key.lower()
        signal_key = any(
            token in lowered
            for token in (
                "error",
                "exception",
                "fail",
                "status",
                "signature",
                "offline",
                "timeout",
            )
        )
        if not signal_key:
            return False
        if isinstance(value, bool):
            return not value
        text = str(value).strip().lower()
        return bool(text) and text not in {
            "ok",
            "pass",
            "passed",
            "success",
            "succeeded",
            "online",
        }

    @staticmethod
    def _is_failure_event(event: dict[str, Any]) -> bool:
        event_type = str(event["event_type"]).lower()
        if any(
            token in event_type
            for token in ("fail", "error", "crash", "timeout", "offline")
        ):
            return True
        for key, value in ShadowCausalAnalyzer._flatten(event["payload"]):
            lowered = key.lower()
            if lowered.endswith(("success", "ok", "passed")) and value is False:
                return True
            if any(
                token in lowered for token in ("error", "exception", "failure")
            ):
                if value not in (None, "", False, 0, [], {}):
                    return True
            if lowered.endswith("status") and str(value).lower() in {
                "failed",
                "failure",
                "error",
                "offline",
                "timeout",
                "crashed",
                "invalid",
                "rejected",
            }:
                return True
        return False

    @staticmethod
    def _analysis_dag(active_spaces: list[str], has_actions: bool) -> dict[str, Any]:
        nodes = ["LOAD_CANONICAL_REFERENCES", "ASSESS_PRESENT"]
        edges = [["LOAD_CANONICAL_REFERENCES", "ASSESS_PRESENT"]]
        if "PAST" in active_spaces:
            nodes.append("READ_BOUNDED_PAST")
            edges.extend(
                [
                    ["LOAD_CANONICAL_REFERENCES", "READ_BOUNDED_PAST"],
                    ["READ_BOUNDED_PAST", "ASSESS_PRESENT"],
                ]
            )
        nodes.append("FREEZE_FUTURE_SCENARIO")
        edges.append(["ASSESS_PRESENT", "FREEZE_FUTURE_SCENARIO"])
        if has_actions:
            nodes.append("AWAIT_CANONICAL_OUTCOME")
            edges.append(["FREEZE_FUTURE_SCENARIO", "AWAIT_CANONICAL_OUTCOME"])
        return {"nodes": nodes, "edges": edges}

    @staticmethod
    def _assert_acyclic(dag: dict[str, Any]) -> None:
        nodes = set(str(node) for node in dag["nodes"])
        outgoing: dict[str, set[str]] = {node: set() for node in nodes}
        indegree = {node: 0 for node in nodes}
        for source, target in dag["edges"]:
            source = str(source)
            target = str(target)
            if source not in nodes or target not in nodes:
                raise ValueError("DAG edge references unknown node")
            if target not in outgoing[source]:
                outgoing[source].add(target)
                indegree[target] += 1
        queue = sorted(node for node, degree in indegree.items() if degree == 0)
        visited = 0
        while queue:
            node = queue.pop(0)
            visited += 1
            for target in sorted(outgoing[node]):
                indegree[target] -= 1
                if indegree[target] == 0:
                    queue.append(target)
                    queue.sort()
        if visited != len(nodes):
            raise ValueError("shadow analysis DAG contains a cycle")

    def _first_action_outcome_seq(
        self, action_ids: set[str], frozen_seq: int
    ) -> int | None:
        if not action_ids:
            return None
        rows = self.db.query_all(
            """
            SELECT seq,event_type,payload_json FROM evidence
            WHERE seq>? AND event_type IN (
                'action_completed','action_rejected','action_blocked'
            )
            ORDER BY seq ASC
            """,
            (frozen_seq,),
        )
        for row in rows:
            payload = json.loads(row["payload_json"])
            if str(payload.get("action_id", "")) in action_ids:
                return int(row["seq"])
        return None

    def _record_metric(
        self,
        run_id: str | None,
        phase: str,
        started: float,
        payload: dict[str, Any],
    ) -> None:
        duration_ms = max(0.0, (time.perf_counter() - started) * 1000.0)
        self.db.execute(
            """
            INSERT INTO shadow_causal_metrics(
                run_id,phase,duration_ms,payload_json,recorded_at
            ) VALUES (?,?,?,?,?)
            """,
            (run_id, phase, duration_ms, canonical_json(payload), utc_now()),
        )

    @staticmethod
    def _freeze_result(row: Any, *, idempotent: bool) -> ShadowFreezeResult:
        proposal = json.loads(row["proposal_json"])
        return ShadowFreezeResult(
            run_id=str(row["run_id"]),
            cycle_id=str(row["cycle_id"]),
            plan_id=str(row["plan_id"]),
            proposal_hash=str(row["proposal_hash"]),
            frozen_evidence_seq=int(row["frozen_evidence_seq"]),
            active_spaces=tuple(json.loads(row["active_spaces_json"])),
            candidate_count=len(proposal.get("candidates", [])),
            idempotent=idempotent,
        )
