from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Iterable, Mapping
import json

from .models import CandidateChange, digest_json


@dataclass(frozen=True, slots=True)
class Constitution:
    constitution_id: str
    version: str
    protected_paths: tuple[str, ...]
    forbidden_actions: tuple[str, ...]
    immutable_rules: tuple[str, ...]
    owner_approval_actions: tuple[str, ...]
    max_changed_files_without_explicit_scope: int
    raw: Mapping[str, Any]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Constitution":
        limits = dict(data.get("limits", {}))
        return cls(
            constitution_id=str(data["constitution_id"]),
            version=str(data["version"]),
            protected_paths=tuple(str(item) for item in data.get("protected_paths", [])),
            forbidden_actions=tuple(str(item) for item in data.get("forbidden_actions", [])),
            immutable_rules=tuple(str(item) for item in data.get("immutable_rules", [])),
            owner_approval_actions=tuple(
                str(item) for item in data.get("owner_approval_actions", [])
            ),
            max_changed_files_without_explicit_scope=int(
                limits.get("max_changed_files_without_explicit_scope", 12)
            ),
            raw=dict(data),
        )

    @classmethod
    def load(cls, path: str) -> "Constitution":
        with open(path, "r", encoding="utf-8") as handle:
            return cls.from_dict(json.load(handle))

    @property
    def digest(self) -> str:
        return digest_json(dict(self.raw))

    def is_protected(self, path: str) -> bool:
        normalized = _normalize(path)
        return any(_matches(normalized, pattern) for pattern in self.protected_paths)

    def protected_touches(self, candidate: CandidateChange) -> list[str]:
        touched = [*candidate.changed_paths, *candidate.deleted_paths]
        return sorted(path for path in touched if self.is_protected(path))

    def forbidden_requested_actions(self, candidate: CandidateChange) -> list[str]:
        forbidden = set(self.forbidden_actions)
        return sorted(action for action in candidate.requested_actions if action in forbidden)

    def owner_gated_actions(self, candidate: CandidateChange) -> list[str]:
        gated = set(self.owner_approval_actions)
        return sorted(action for action in candidate.requested_actions if action in gated)


def _normalize(path: str) -> str:
    return str(PurePosixPath(path.replace("\\", "/"))).lstrip("./")


def _matches(path: str, pattern: str) -> bool:
    pattern = _normalize(pattern)
    if pattern.endswith("/**"):
        prefix = pattern[:-3].rstrip("/")
        return path == prefix or path.startswith(prefix + "/")
    if pattern.endswith("/*"):
        prefix = pattern[:-2].rstrip("/")
        if not path.startswith(prefix + "/"):
            return False
        return "/" not in path[len(prefix) + 1 :]
    return path == pattern


def path_allowed(path: str, allowed_patterns: Iterable[str]) -> bool:
    normalized = _normalize(path)
    return any(_matches(normalized, pattern) for pattern in allowed_patterns)
