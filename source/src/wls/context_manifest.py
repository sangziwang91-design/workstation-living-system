from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
import json

from .db import Database
from .schemas import digest_json, new_id, utc_now
from .task_admission import TaskIntent
from .task_graph import TaskGraph
from .text import tokens


_ROLE_RECORD_TYPES = {
    "planner": {"task_intent", "task_graph", "project_note", "skill", "episode"},
    "executor": {"task_intent", "task_graph", "project_note", "skill"},
    "reviewer": {"task_intent", "task_graph", "project_note", "episode"},
    "researcher": {"task_intent", "task_graph", "project_note", "source_note"},
}
_SECRET_TERMS = ("secret", "credential", "password", "token", "api_key", "private_key")


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

    def load_latest_for_graph(self, graph_id: str) -> dict[str, Any]:
        row = self.db.query_one(
            """
            SELECT manifest_json FROM agentic_context_manifests
            WHERE graph_id=?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (graph_id,),
        )
        if row is None:
            raise KeyError(f"unknown context manifest for graph: {graph_id}")
        manifest = json.loads(str(row["manifest_json"]))
        if not isinstance(manifest, dict):
            raise ValueError("stored context manifest must be a JSON object")
        return manifest

    def build_role_packet(
        self,
        manifest: dict[str, Any],
        *,
        role: str,
        token_budget: int = 1200,
    ) -> dict[str, Any]:
        normalized_role = role.strip().lower()
        if normalized_role not in _ROLE_RECORD_TYPES:
            raise ValueError(f"unsupported context role: {role}")
        if token_budget < 64:
            raise ValueError("token_budget must be >= 64")
        allowed_types = _ROLE_RECORD_TYPES[normalized_role]
        included: list[dict[str, Any]] = []
        suppressed: list[dict[str, str]] = []
        used_tokens = 0
        for record in list(manifest.get("records", [])):
            record_type = str(record.get("record_type", ""))
            record_id = str(record.get("record_id", ""))
            if record_type not in allowed_types:
                suppressed.append(
                    {
                        "record_id": record_id,
                        "reason": f"role_filter:{normalized_role}",
                    }
                )
                continue
            if self._looks_sensitive(record):
                suppressed.append(
                    {"record_id": record_id, "reason": "sensitive_context_filter"}
                )
                continue
            rendered = self._packet_record(record)
            estimated = len(tokens(json.dumps(rendered, sort_keys=True)))
            if used_tokens + estimated > token_budget:
                suppressed.append({"record_id": record_id, "reason": "token_budget"})
                continue
            included.append(rendered)
            used_tokens += estimated
        packet = {
            "packet_id": new_id("ctxpkt"),
            "manifest_id": str(manifest["manifest_id"]),
            "graph_id": str(manifest["graph_id"]),
            "intent_id": str(manifest["intent_id"]),
            "role": normalized_role,
            "created_at": utc_now(),
            "token_budget": token_budget,
            "estimated_tokens": used_tokens,
            "records": included,
            "included_record_ids": [str(item["record_id"]) for item in included],
            "suppressed_records": suppressed,
            "secret_material_present": False,
            "raw_database_export": False,
            "claim_ceiling": (
                "role-scoped context packet rendered from an existing manifest only; "
                "no new memory authority, secret access, worker execution, or "
                "external source access"
            ),
        }
        packet["packet_digest"] = digest_json(
            {
                "manifest_id": packet["manifest_id"],
                "role": packet["role"],
                "records": packet["records"],
                "suppressed_records": packet["suppressed_records"],
                "token_budget": packet["token_budget"],
            }
        )
        return packet

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

    @staticmethod
    def _packet_record(record: dict[str, Any]) -> dict[str, Any]:
        return {
            "record_id": str(record.get("record_id", "")),
            "record_type": str(record.get("record_type", "")),
            "source": str(record.get("source", "")),
            "digest": str(record.get("digest", "")),
            "summary": str(record.get("summary", ""))[:240],
            "metadata": dict(record.get("metadata", {})),
        }

    @staticmethod
    def _looks_sensitive(record: dict[str, Any]) -> bool:
        haystack = json.dumps(record, ensure_ascii=False, sort_keys=True).lower()
        return any(term in haystack for term in _SECRET_TERMS)
