from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class CodingTaskContract:
    task_id: str
    base_sha: str
    worktree: Path
    changed_files: list[str] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)
    rollback: list[str] = field(default_factory=list)

    def validate(self) -> None:
        if not self.task_id or not self.base_sha:
            raise ValueError("task_id and base_sha are required")
        if not self.worktree.is_absolute():
            raise ValueError("worktree must be absolute")
        if not self.rollback:
            raise ValueError("coding task requires rollback")

    def candidate_artifact(self) -> dict[str, Any]:
        self.validate()
        return {"status": "CANDIDATE_ONLY", "contract": asdict(self)}
