from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
import hashlib
import json
import os

from .schemas import digest_json


@dataclass(frozen=True, slots=True)
class TaskEnvelope:
    protocol_version: str
    message_id: str
    graph_id: str
    node_id: str
    lease_id: str
    sender: str
    recipient: str
    payload: dict[str, Any]
    payload_digest: str

    @classmethod
    def create(
        cls,
        *,
        message_id: str,
        graph_id: str,
        node_id: str,
        lease_id: str,
        sender: str,
        recipient: str,
        payload: dict[str, Any],
    ) -> "TaskEnvelope":
        return cls(
            protocol_version="1.0",
            message_id=message_id,
            graph_id=graph_id,
            node_id=node_id,
            lease_id=lease_id,
            sender=sender,
            recipient=recipient,
            payload=payload,
            payload_digest=digest_json(payload),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ResultEnvelope:
    protocol_version: str
    message_id: str
    in_reply_to: str
    graph_id: str
    node_id: str
    lease_id: str
    sender: str
    recipient: str
    status: str
    payload: dict[str, Any]
    payload_digest: str

    @classmethod
    def create(
        cls,
        *,
        message_id: str,
        in_reply_to: str,
        graph_id: str,
        node_id: str,
        lease_id: str,
        sender: str,
        recipient: str,
        status: str,
        payload: dict[str, Any],
    ) -> "ResultEnvelope":
        return cls(
            protocol_version="1.0",
            message_id=message_id,
            in_reply_to=in_reply_to,
            graph_id=graph_id,
            node_id=node_id,
            lease_id=lease_id,
            sender=sender,
            recipient=recipient,
            status=status,
            payload=payload,
            payload_digest=digest_json(payload),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AgenticFileMailbox:
    """Local file transport for agentic handoffs, not canonical runtime state."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).expanduser().resolve()
        for box in ("tasks", "results", "processed", "rejected", "artifacts"):
            (self.root / box).mkdir(parents=True, exist_ok=True)
        (self.root / "artifact_chunks").mkdir(parents=True, exist_ok=True)

    def write_task(self, envelope: TaskEnvelope) -> Path:
        return self._write("tasks", envelope.message_id, envelope.to_dict())

    def write_result(self, envelope: ResultEnvelope) -> Path:
        return self._write("results", envelope.message_id, envelope.to_dict())

    def read_task(self, message_id: str) -> TaskEnvelope:
        payload = self._read("tasks", message_id)
        self._verify_payload(payload)
        return TaskEnvelope(**payload)

    def read_result(self, message_id: str) -> ResultEnvelope:
        payload = self._read("results", message_id)
        self._verify_payload(payload)
        return ResultEnvelope(**payload)

    def mark_processed(self, message_id: str, *, result: bool = False) -> Path:
        source = self._path("results" if result else "tasks", message_id)
        target = self._path("processed", message_id)
        os.replace(source, target)
        return target

    def reject(self, message_id: str, *, result: bool = False) -> Path:
        source = self._path("results" if result else "tasks", message_id)
        target = self._path("rejected", message_id)
        os.replace(source, target)
        return target

    def write_artifact_chunk(
        self,
        artifact_id: str,
        sequence: int,
        data: bytes,
    ) -> Path:
        if sequence < 0:
            raise ValueError("artifact chunk sequence must be >= 0")
        chunk_dir = self._artifact_chunk_dir(artifact_id)
        chunk_dir.mkdir(parents=True, exist_ok=True)
        target = chunk_dir / f"{sequence:08d}.chunk"
        if target.exists():
            if target.read_bytes() == data:
                return target
            raise FileExistsError(f"artifact chunk collision: {artifact_id}:{sequence}")
        temporary = target.with_suffix(".chunk.tmp")
        temporary.write_bytes(data)
        os.replace(temporary, target)
        return target

    def finalize_artifact(
        self,
        artifact_id: str,
        *,
        chunk_count: int,
        expected_sha256: str,
    ) -> dict[str, Any]:
        if chunk_count < 1:
            raise ValueError("artifact finalization requires at least one chunk")
        expected = expected_sha256.lower()
        chunk_dir = self._artifact_chunk_dir(artifact_id)
        artifact_path = self._artifact_path(artifact_id)
        hasher = hashlib.sha256()
        temporary = artifact_path.with_suffix(".bin.tmp")
        with temporary.open("wb") as output:
            for sequence in range(chunk_count):
                chunk = chunk_dir / f"{sequence:08d}.chunk"
                if not chunk.is_file():
                    raise ValueError(f"missing artifact chunk: {artifact_id}:{sequence}")
                data = chunk.read_bytes()
                hasher.update(data)
                output.write(data)
        actual = hasher.hexdigest()
        if actual != expected:
            temporary.unlink(missing_ok=True)
            raise ValueError(f"artifact digest mismatch: actual={actual} expected={expected}")
        os.replace(temporary, artifact_path)
        manifest = {
            "artifact_id": artifact_id,
            "chunk_count": chunk_count,
            "sha256": actual,
            "path": str(artifact_path),
            "finalized": True,
        }
        manifest_path = artifact_path.with_suffix(".manifest.json")
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return {**manifest, "manifest_path": str(manifest_path)}

    def _write(self, box: str, message_id: str, payload: dict[str, Any]) -> Path:
        target = self._path(box, message_id)
        serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        if target.exists():
            if target.read_text(encoding="utf-8") == serialized:
                return target
            raise FileExistsError(f"message_id collision: {message_id}")
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(serialized + "\n", encoding="utf-8")
        os.replace(temporary, target)
        return target

    def _read(self, box: str, message_id: str) -> dict[str, Any]:
        payload = json.loads(self._path(box, message_id).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("mailbox message must be a JSON object")
        return payload

    def _path(self, box: str, message_id: str) -> Path:
        if box not in {"tasks", "results", "processed", "rejected"}:
            raise ValueError("unknown mailbox box")
        if not message_id or any(
            char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
            for char in message_id
        ):
            raise ValueError("unsafe message_id")
        path = (self.root / box / f"{message_id}.json").resolve()
        if self.root not in path.parents:
            raise ValueError("mailbox path escape")
        return path

    def _artifact_chunk_dir(self, artifact_id: str) -> Path:
        safe = self._safe_artifact_id(artifact_id)
        path = (self.root / "artifact_chunks" / safe).resolve()
        if self.root not in path.parents:
            raise ValueError("artifact chunk path escape")
        return path

    def _artifact_path(self, artifact_id: str) -> Path:
        safe = self._safe_artifact_id(artifact_id)
        path = (self.root / "artifacts" / f"{safe}.bin").resolve()
        if self.root not in path.parents:
            raise ValueError("artifact path escape")
        return path

    @staticmethod
    def _safe_artifact_id(artifact_id: str) -> str:
        if not artifact_id or any(
            char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
            for char in artifact_id
        ):
            raise ValueError("unsafe artifact_id")
        return artifact_id

    @staticmethod
    def _verify_payload(payload: dict[str, Any]) -> None:
        expected = str(payload.get("payload_digest", ""))
        actual = digest_json(dict(payload.get("payload", {})))
        if expected != actual:
            raise ValueError("payload digest mismatch")
