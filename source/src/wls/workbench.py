from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


FORBIDDEN_STEP_FLAGS = {
    "direct_db_write",
    "mark_goal_complete",
    "promote_skill",
    "deploy",
    "merge",
}


@dataclass(slots=True)
class WorkbenchTemplate:
    template_id: str
    canonical_owner: str
    steps: list[dict[str, Any]]
    evidence_required: list[str] = field(default_factory=list)

    def validate(self) -> None:
        if not self.template_id:
            raise ValueError("workbench template_id is required")
        if self.canonical_owner not in {"planning", "evolution", "skills"}:
            raise ValueError("workbench must bind to existing WLS authority")
        if not self.steps:
            raise ValueError("workbench requires steps")
        if not self.evidence_required:
            raise ValueError("workbench requires evidence")
        for step in self.steps:
            forbidden = sorted(flag for flag in FORBIDDEN_STEP_FLAGS if step.get(flag))
            if forbidden:
                raise PermissionError(
                    f"workbench cannot claim canonical authority: {', '.join(forbidden)}"
                )

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        data = asdict(self)
        data["status"] = "TEMPLATE_ONLY"
        return data
