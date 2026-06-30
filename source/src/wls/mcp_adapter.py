from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class McpCandidate:
    server_id: str
    identity_digest: str | None
    transport: str
    executable: str | None = None
    allowed_paths: list[str] = field(default_factory=list)
    allowed_hosts: list[str] = field(default_factory=list)
    side_effect_class: str = "none"
    review_status: str = "DISCOVERED"


class McpTrustGate:
    def admit(self, candidate: McpCandidate) -> dict[str, Any]:
        if not candidate.identity_digest:
            raise PermissionError("MCP server identity must be pinned")
        if candidate.side_effect_class not in {"none", "reversible", "external"}:
            raise PermissionError("MCP side effect class is not admitted")
        if candidate.review_status not in {"REVIEWED", "SHADOW"}:
            raise PermissionError("MCP server must be reviewed before admission")
        return {"status": "VALIDATED_CANDIDATE", "candidate": asdict(candidate)}
