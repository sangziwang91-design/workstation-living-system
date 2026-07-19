from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .schemas import digest_json, new_id, utc_now


@dataclass(slots=True)
class MergeArtifact:
    artifact_id: str
    source_node: str
    content_digest: str
    content_type: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MergeConflict:
    conflict_id: str
    artifact_a: str
    artifact_b: str
    field: str
    detail: str
    resolution: str = "UNRESOLVED"

    def to_dict(self) -> dict[str, Any]:
        return {
            "conflict_id": self.conflict_id,
            "artifact_a": self.artifact_a,
            "artifact_b": self.artifact_b,
            "field": self.field,
            "detail": self.detail,
            "resolution": self.resolution,
        }


@dataclass(slots=True)
class MergedResult:
    merge_id: str
    artifacts_merged: list[str]
    result_digest: str
    conflicts: list[MergeConflict] = field(default_factory=list)
    passed: bool = True
    merged_at: str = field(default_factory=utc_now)

    def has_conflicts(self) -> bool:
        return any(c.resolution == "UNRESOLVED" for c in self.conflicts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "merge_id": self.merge_id,
            "artifacts_merged": self.artifacts_merged,
            "result_digest": self.result_digest,
            "conflict_count": len(self.conflicts),
            "unresolved_conflicts": sum(1 for c in self.conflicts if c.resolution == "UNRESOLVED"),
            "passed": self.passed,
            "conflicts": [c.to_dict() for c in self.conflicts],
        }


class MergeNode:
    """Merge verified child artifact outputs with conflict detection.
    Text: concatenates. JSON/dict: shallow merge, flag overlapping keys.
    """

    def merge(self, artifacts: list[MergeArtifact]) -> MergedResult:
        merge_id = new_id("merge")
        conflicts: list[MergeConflict] = []
        ids = [a.artifact_id for a in artifacts]

        if not artifacts:
            return MergedResult(
                merge_id=merge_id,
                artifacts_merged=ids,
                result_digest=digest_json({}),
                passed=True,
            )

        if len(artifacts) == 1:
            return MergedResult(
                merge_id=merge_id,
                artifacts_merged=ids,
                result_digest=artifacts[0].content_digest,
                passed=True,
            )

        first = artifacts[0]
        merged: str | dict[str, Any]
        if first.content_type == "text":
            merged = self._merge_text(artifacts)
        elif first.content_type in ("json", "dict"):
            merged, conflicts = self._merge_dict(artifacts)
        else:
            merged = {"merged": [a.content_digest for a in artifacts]}

        return MergedResult(
            merge_id=merge_id,
            artifacts_merged=ids,
            result_digest=digest_json(merged),
            conflicts=conflicts,
            passed=not any(c.resolution == "UNRESOLVED" for c in conflicts),
        )

    def _merge_text(self, artifacts: list[MergeArtifact]) -> str:
        parts: list[str] = []
        for a in artifacts:
            parts.append(f"--- {a.source_node} ---\n{a.content_digest}")
        return "\n\n".join(parts)

    def _merge_dict(
        self, artifacts: list[MergeArtifact]
    ) -> tuple[dict[str, Any], list[MergeConflict]]:
        import json

        parsed: list[dict[str, Any]] = []
        for a in artifacts:
            try:
                parsed.append(json.loads(a.content_digest) if a.content_digest.startswith("{") else a.metadata)
            except Exception:
                parsed.append({})

        merged: dict[str, Any] = {}
        conflicts: list[MergeConflict] = []
        all_keys: set[str] = set()
        for d in parsed:
            all_keys.update(d.keys())

        for key in all_keys:
            values: list[Any] = []
            for i, d in enumerate(parsed):
                if key in d:
                    values.append((artifacts[i].artifact_id, d[key]))
            if len(values) == 1:
                merged[key] = values[0][1]
            else:
                unique = {repr(v) for _, v in values}
                if len(unique) == 1:
                    merged[key] = values[0][1]
                else:
                    merged[key] = [v for _, v in values]
                    conflicts.append(MergeConflict(
                        conflict_id=new_id("conflict"),
                        artifact_a=values[0][0],
                        artifact_b=values[1][0] if len(values) > 1 else "",
                        field=key,
                        detail=f"divergent values: {unique}",
                    ))

        return merged, conflicts
