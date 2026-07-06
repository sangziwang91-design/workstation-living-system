from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .db import Database
from .schemas import digest_json, new_id, utc_now
from .text import tokens


@dataclass(slots=True)
class MemoryProjection:
    memory_id: str
    memory_type: str
    summary: str
    importance: float
    confidence: float
    active: bool
    validity: str
    scope: str
    dependencies: list[str]
    provenance_digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "memory_type": self.memory_type,
            "summary": self.summary,
            "importance": self.importance,
            "confidence": self.confidence,
            "active": self.active,
            "validity": self.validity,
            "scope": self.scope,
            "dependencies": self.dependencies,
            "provenance_digest": self.provenance_digest,
        }


@dataclass(slots=True)
class WorkingSetCache:
    cache_id: str
    projections: list[MemoryProjection] = field(default_factory=list)
    cache_digest: str = ""
    token_count: int = 0
    created_at: str = field(default_factory=utc_now)

    def invalidate_on(self, changed_memory_ids: set[str]) -> bool:
        proj_ids = {p.memory_id for p in self.projections}
        return bool(proj_ids & changed_memory_ids)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cache_id": self.cache_id,
            "memory_count": len(self.projections),
            "cache_digest": self.cache_digest,
            "token_count": self.token_count,
            "created_at": self.created_at,
        }


class MemoryProjectionEngine:
    """Projects WLS MemoryStore into task-scoped working sets with provenance,
    validity filtering, and scope isolation. Does not create a second memory
    authority.
    """

    def __init__(self, db: Database) -> None:
        self.db = db

    def project(
        self,
        *,
        memory_type: str | None = None,
        min_importance: float = 0.0,
        limit: int = 20,
        exclude_refuted: bool = True,
        exclude_expired: bool = True,
        scope: str | None = None,
    ) -> list[MemoryProjection]:
        conditions = ["active = 1"]
        params: list[Any] = []

        if memory_type:
            conditions.append("memory_type = ?")
            params.append(memory_type)

        if exclude_refuted:
            conditions.append("(content_json NOT LIKE '%REFUTED%' OR content_json IS NULL)")

        if min_importance > 0:
            conditions.append("importance >= ?")
            params.append(min_importance)

        if scope:
            conditions.append("(tags_json LIKE ? OR tags_json = '[]')")
            params.append(f"%{scope}%")

        where = " AND ".join(conditions)
        rows = self.db.query_all(
            f"""SELECT memory_id, memory_type, content_json, importance, confidence,
                       active, tags_json, source_ids_json, created_at
                FROM memories WHERE {where}
                ORDER BY importance DESC, last_accessed_at DESC
                LIMIT ?""",
            (*params, limit),
        )

        projections: list[MemoryProjection] = []
        import json

        for row in rows:
            content = json.loads(str(row["content_json"]))
            tags = json.loads(str(row["tags_json"]))
            sources = json.loads(str(row["source_ids_json"]))

            summary_text = ""
            if isinstance(content, dict):
                summary_text = str(content.get("summary", content.get("text", "")))[:200]

            deps: list[str] = []
            if isinstance(content, dict):
                deps = list(content.get("memory_dependencies", []) or [])

            projections.append(MemoryProjection(
                memory_id=str(row["memory_id"]),
                memory_type=str(row["memory_type"]),
                summary=summary_text,
                importance=float(row["importance"]),
                confidence=float(row["confidence"]),
                active=bool(row["active"]),
                validity=self._derive_validity(content, str(row["memory_id"])),
                scope=json.dumps(tags) if tags else "",
                dependencies=deps,
                provenance_digest=digest_json({
                    "sources": sources,
                    "created": str(row["created_at"]),
                }),
            ))

        return projections

    def build_working_set(
        self,
        *,
        memory_type: str | None = None,
        min_importance: float = 0.0,
        max_tokens: int = 4000,
        limit: int = 20,
        scope: str | None = None,
    ) -> WorkingSetCache:
        projections = self.project(
            memory_type=memory_type,
            min_importance=min_importance,
            limit=limit,
            scope=scope,
        )

        cache = WorkingSetCache(cache_id=new_id("ws"))
        total_tokens = 0
        for proj in projections:
            proj_tokens = len(tokens(proj.summary)) + 10
            if total_tokens + proj_tokens > max_tokens:
                break
            cache.projections.append(proj)
            total_tokens += proj_tokens

        cache.token_count = total_tokens
        cache.cache_digest = digest_json([p.to_dict() for p in cache.projections])
        return cache

    def _derive_validity(self, content: Any, memory_id: str) -> str:
        if not isinstance(content, dict):
            return "VALID"
        status = str(content.get("validity", content.get("status", ""))).upper()
        if status in ("REFUTED", "EXPIRED", "SUPERSEDED"):
            return status
        if str(content.get("active", "1")) == "0":
            return "INACTIVE"
        return "VALID"
