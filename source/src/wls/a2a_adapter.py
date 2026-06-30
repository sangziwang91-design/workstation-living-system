from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class TaskContract:
    task_id: str
    objective: str
    scope: dict[str, Any]
    allowed_outputs: list[str]
    expires_at: str


@dataclass(slots=True)
class ArtifactEnvelope:
    task_id: str
    artifact_type: str
    payload: dict[str, Any]
    candidate_only: bool = True
    hashes: dict[str, str] = field(default_factory=dict)


class A2AAdapter:
    def receive(self, contract: TaskContract, envelope: ArtifactEnvelope) -> dict[str, Any]:
        if envelope.task_id != contract.task_id:
            raise ValueError("artifact does not match task")
        if envelope.artifact_type not in contract.allowed_outputs:
            raise PermissionError("artifact type not allowed")
        if not envelope.candidate_only:
            raise PermissionError("external artifact cannot claim canonical truth")
        return {"status": "CANDIDATE_ONLY", "artifact": asdict(envelope)}
