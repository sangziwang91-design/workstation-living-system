from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
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
        for box in ("tasks", "results", "processed", "rejected"):
            (self.root / box).mkdir(parents=True, exist_ok=True)

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

    @staticmethod
    def _verify_payload(payload: dict[str, Any]) -> None:
        expected = str(payload.get("payload_digest", ""))
        actual = digest_json(dict(payload.get("payload", {})))
        if expected != actual:
            raise ValueError("payload digest mismatch")
