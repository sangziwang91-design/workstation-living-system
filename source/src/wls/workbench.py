from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class WorkbenchTemplate:
    template_id: str
    canonical_owner: str
    steps: list[dict[str, Any]]
    evidence_required: list[str] = field(default_factory=list)

    def validate(self) -> None:
        if self.canonical_owner not in {"planning", "evolution", "skills"}:
            raise ValueError("workbench must bind to existing WLS authority")
        for step in self.steps:
            if step.get("direct_db_write"):
                raise PermissionError("workbench cannot write canonical DB directly")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)
