from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
from typing import Any


@dataclass(frozen=True, slots=True)
class ExternalMemoryCandidate:
    source_id: str
    source_digest: str
    content: dict[str, Any]
    evidence_hashes: list[str]
    candidate_only: bool = True
    tags: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ExternalMemoryReceipt:
    source_id: str
    source_digest: str
    content_sha256: str
    evidence_hashes: list[str]
    tags: list[str]
    status: str = "CANDIDATE_ONLY"
    canonical_owner: str = "MemoryStore"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ExternalMemoryProjection:
    def admit(self, candidate: ExternalMemoryCandidate) -> ExternalMemoryReceipt:
        if not candidate.source_id:
            raise ValueError("external memory source_id is required")
        if not candidate.source_digest.startswith("sha256:"):
            raise PermissionError("external memory source digest must be pinned")
        if not candidate.evidence_hashes:
            raise ValueError("external memory candidate requires evidence hashes")
        if not candidate.candidate_only:
            raise PermissionError("external memory cannot claim canonical memory")
        if candidate.content.get("memory_id") or candidate.content.get("active") is True:
            raise PermissionError("external memory cannot write MemoryStore fields")
        return ExternalMemoryReceipt(
            source_id=candidate.source_id,
            source_digest=candidate.source_digest,
            content_sha256=external_memory_digest(candidate.content),
            evidence_hashes=list(candidate.evidence_hashes),
            tags=list(candidate.tags),
        )


def external_memory_digest(content: dict[str, Any]) -> str:
    encoded = json.dumps(content, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
