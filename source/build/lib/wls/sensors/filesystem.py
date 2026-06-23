from __future__ import annotations

from pathlib import Path
from typing import Any
import fnmatch
import hashlib

from .base import Sensor
from ..schemas import Observation


def file_signature(path: Path, hash_limit: int) -> dict[str, Any]:
    stat = path.stat()
    result: dict[str, Any] = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    if stat.st_size <= hash_limit:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        result["sha256"] = digest.hexdigest()
    return result


class FileSystemSensor(Sensor):
    def poll(
        self, previous_state: dict[str, Any]
    ) -> tuple[list[Observation], dict[str, Any]]:
        roots = [
            Path(item).expanduser().resolve() for item in self.settings.get("roots", [])
        ]
        recursive = bool(self.settings.get("recursive", True))
        max_files = int(self.settings.get("max_files", 5000))
        hash_limit = int(self.settings.get("hash_limit_bytes", 1024 * 1024))
        excludes = list(
            self.settings.get(
                "exclude", [".git/*", "*/__pycache__/*", "*.tmp", "*.lock"]
            )
        )
        current: dict[str, Any] = {}
        truncated = False
        for root in roots:
            if not root.exists():
                current[str(root)] = {"missing_root": True}
                continue
            iterator = root.rglob("*") if recursive else root.glob("*")
            for path in iterator:
                if len(current) >= max_files:
                    truncated = True
                    break
                try:
                    if not path.is_file():
                        continue
                    rel = str(path.relative_to(root)).replace("\\", "/")
                    if any(fnmatch.fnmatch(rel, pattern) for pattern in excludes):
                        continue
                    current[str(path)] = file_signature(path, hash_limit)
                except (OSError, ValueError):
                    continue
            if truncated:
                break
        previous_files = dict(previous_state.get("files", {}))
        observations: list[Observation] = []
        for path_text, signature in current.items():
            if path_text not in previous_files:
                observations.append(self._obs(path_text, "created", signature))
            elif previous_files[path_text] != signature:
                observations.append(
                    self._obs(
                        path_text,
                        "modified",
                        {"before": previous_files[path_text], "after": signature},
                    )
                )
        for path_text, signature in previous_files.items():
            if path_text not in current:
                observations.append(self._obs(path_text, "deleted", signature))
        if truncated:
            observations.append(
                Observation(
                    source=self.name,
                    kind="sensor_warning",
                    subject=self.name,
                    predicate="scan_truncated",
                    value=True,
                    confidence=1.0,
                    metadata={
                        "max_files": max_files,
                        "dedupe_key": f"filesystem:{self.name}:truncated:{max_files}",
                    },
                )
            )
        return observations, {"files": current, "truncated": truncated}

    def _obs(self, path: str, change: str, details: Any) -> Observation:
        return Observation(
            source=self.name,
            kind="filesystem_change",
            subject=path,
            predicate="file_state",
            value=change,
            metadata={
                "details": details,
                "dedupe_key": f"filesystem:{path}:{change}:{hashlib.sha256(repr(details).encode()).hexdigest()}",
            },
        )
