from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
import json

from .db import Database
from .schemas import digest_json, new_id, utc_now
from .task_admission import TaskIntent
from .task_graph import TaskGraph


@dataclass(frozen=True, slots=True)
class ContextRecord:
    record_id: str
    record_type: str
    source: str
    digest: str
    summary: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ContextManifestBuilder:
    """Deterministic context selector for the canonical agentic harness."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def build(
        self,
        intent: TaskIntent,
        graph: TaskGraph,
        *,
        limit: int = 6,
    ) -> dict[str, Any]:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        records = [
            self._intent_record(intent),
            self._graph_record(graph),
            *self._memory_records(limit=max(0, limit - 2)),
        ]
        payload = {
            "manifest_id": new_id("ctx"),
            "intent_id": intent.intent_id,
            "graph_id": graph.graph_id,
            "created_at": utc_now(),
            "selection_policy": {
                "name": "CONTEXT_MANIFEST_V1",
                "deterministic": True,
                "authority": "LivingSystem.MemoryStore/EvidenceLedger",
                "memory_order": "active DESC, importance DESC, created_at DESC, memory_id ASC",
                "limit": limit,
            },
            "records": [record.to_dict() for record in records],
            "claim_ceiling": (
                "deterministic scoped context manifest only; no new memory "
                "authority, retrieval model, or external source access"
            ),
        }
        payload["manifest_digest"] = digest_json(
            {
                "intent_id": payload["intent_id"],
                "graph_id": payload["graph_id"],
                "records": payload["records"],
                "selection_policy": payload["selection_policy"],
            }
        )
        return payload

    def persist(
        self,
        manifest: dict[str, Any],
        *,
        connection: Any,
    ) -> None:
        connection.execute(
            """
            INSERT INTO agentic_context_manifests(
                manifest_id,graph_id,intent_id,manifest_json,manifest_digest,created_at
            ) VALUES (?,?,?,?,?,?)
            """,
            (
                str(manifest["manifest_id"]),
                str(manifest["graph_id"]),
                str(manifest["intent_id"]),
                json.dumps(manifest, ensure_ascii=False, sort_keys=True),
                str(manifest["manifest_digest"]),
                str(manifest["created_at"]),
            ),
        )

    def latest_receipts(self, *, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.db.query_all(
            """
            SELECT manifest_id,graph_id,intent_id,manifest_digest,created_at
            FROM agentic_context_manifests
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (max(0, int(limit)),),
        )
        return [dict(row) for row in rows]

    @staticmethod
    def _intent_record(intent: TaskIntent) -> ContextRecord:
        intent_payload = intent.to_dict()
        return ContextRecord(
            record_id=intent.intent_id,
            record_type="task_intent",
            source="agentic_task_intents",
            digest=digest_json(intent_payload),
            summary=f"{intent.operation.value}:{intent.domain.value}:{intent.risk_floor.value}",
            metadata={
                "owner_gate_required": intent.owner_gate_required,
                "parallelism": intent.parallelism.value,
                "horizon": intent.horizon.value,
            },
        )

    @staticmethod
    def _graph_record(graph: TaskGraph) -> ContextRecord:
        snapshot = graph.snapshot()
        return ContextRecord(
            record_id=graph.graph_id,
            record_type="task_graph",
            source="agentic_task_graphs",
            digest=str(snapshot["graph_digest"]),
            summary=f"{len(graph.nodes)} nodes / terminal={graph.terminal()}",
            metadata={"node_ids": sorted(graph.nodes)},
        )

    def _memory_records(self, *, limit: int) -> list[ContextRecord]:
        if limit <= 0:
            return []
        rows = self.db.query_all(
            """
            SELECT memory_id,memory_type,content_json,importance,confidence,
                   source_ids_json,tags_json,created_at
            FROM memories
            WHERE active=1
            ORDER BY importance DESC, created_at DESC, memory_id ASC
            LIMIT ?
            """,
            (limit,),
        )
        records: list[ContextRecord] = []
        for row in rows:
            content_json = str(row["content_json"])
            records.append(
                ContextRecord(
                    record_id=str(row["memory_id"]),
                    record_type=str(row["memory_type"]),
                    source="memories",
                    digest=digest_json(json.loads(content_json)),
                    summary=content_json[:160],
                    metadata={
                        "importance": float(row["importance"]),
                        "confidence": float(row["confidence"]),
                        "source_ids_json": row["source_ids_json"],
                        "tags_json": row["tags_json"],
                        "created_at": row["created_at"],
                    },
                )
            )
        return records
