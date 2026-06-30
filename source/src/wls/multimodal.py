from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
import hashlib


@dataclass(slots=True)
class MultimodalArtifactEnvelope:
    artifact_id: str
    media_type: str
    content: bytes
    metadata: dict[str, Any] = field(default_factory=dict)
    candidate_only: bool = True

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["content"] = None
        data["sha256"] = hashlib.sha256(self.content).hexdigest()
        return data
