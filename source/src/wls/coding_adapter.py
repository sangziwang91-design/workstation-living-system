from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from pathlib import Path, PureWindowsPath
from typing import Any, TypedDict


class ChangedFileReceipt(TypedDict):
    path: str
    sha256: str
    bytes: int


@dataclass(frozen=True, slots=True)
class CodingCandidateReceipt:
    task_id: str
    base_sha: str
    worktree: str
    changed_files: list[ChangedFileReceipt]
    tests: list[str]
    rollback: list[str]
    status: str = "CANDIDATE_ONLY"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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
        if not self.worktree.exists() or not self.worktree.is_dir():
            raise ValueError("worktree must exist")
        if not self.changed_files:
            raise ValueError("coding task requires changed files")
        if not self.tests:
            raise ValueError("coding task requires tests")
        if not self.rollback:
            raise ValueError("coding task requires rollback")
        for relative_file in self.changed_files:
            self._resolve_changed_file(relative_file)

    def candidate_artifact(self) -> dict[str, Any]:
        return self.candidate_receipt().to_dict()

    def candidate_receipt(self) -> CodingCandidateReceipt:
        self.validate()
        files: list[ChangedFileReceipt] = []
        for relative_file in sorted(self.changed_files):
            path = self._resolve_changed_file(relative_file)
            if not path.is_file():
                raise ValueError("changed file must exist")
            content = path.read_bytes()
            files.append(
                {
                    "path": relative_file.replace("\\", "/"),
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "bytes": len(content),
                }
            )
        return CodingCandidateReceipt(
            task_id=self.task_id,
            base_sha=self.base_sha,
            worktree=str(self.worktree),
            changed_files=files,
            tests=list(self.tests),
            rollback=list(self.rollback),
        )

    def _resolve_changed_file(self, relative_file: str) -> Path:
        # Accept a Windows-style relative source path on both Linux CI and
        # Windows. Without normalization, '..\\secret.py' is a *literal
        # filename* on Linux, so an unsafe Windows traversal silently passes
        # the containment check and is only rejected as a missing file.
        if (
            not relative_file
            or ('\x00' in relative_file)
            or ('\n' in relative_file or '\r' in relative_file)
            or (':' in relative_file)
            or Path(relative_file).is_absolute()
            or PureWindowsPath(relative_file).drive
        ):
            raise ValueError("changed files must be relative paths")
        canonical_relative = relative_file.replace("\\", "/")
        root = self.worktree.resolve()
        candidate = (root / canonical_relative).resolve()
        if candidate == root or root not in candidate.parents:
            raise ValueError("changed file escapes worktree")
        return candidate
