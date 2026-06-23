from __future__ import annotations

from pathlib import Path
from typing import Any
import hashlib
import json
import os

from .base import Sensor
from ..schemas import EvidenceKind, Observation, VerificationStatus


class InboxSensor(Sensor):
    def poll(
        self, previous_state: dict[str, Any]
    ) -> tuple[list[Observation], dict[str, Any]]:
        inbox = Path(self.settings.get("path") or (Path(self.home) / "inbox"))
        processed = inbox / "processed"
        rejected = inbox / "rejected"
        inbox.mkdir(parents=True, exist_ok=True)
        processed.mkdir(parents=True, exist_ok=True)
        rejected.mkdir(parents=True, exist_ok=True)
        observations: list[Observation] = []
        max_files = int(self.settings.get("max_files_per_poll", 100))
        for path in sorted(inbox.glob("*.json"))[:max_files]:
            try:
                raw_bytes = path.read_bytes()
                raw = json.loads(raw_bytes.decode("utf-8"))
                required = {"subject", "predicate", "value"}
                if not required.issubset(raw):
                    raise ValueError(f"missing keys: {sorted(required - set(raw))}")
                file_hash = hashlib.sha256(raw_bytes).hexdigest()
                observations.append(
                    Observation(
                        source=str(raw.get("source", self.name)),
                        kind=str(raw.get("kind", "external_event")),
                        subject=str(raw["subject"]),
                        predicate=str(raw["predicate"]),
                        value=raw["value"],
                        confidence=float(raw.get("confidence", 1.0)),
                        evidence_kind=EvidenceKind(
                            str(raw.get("evidence_kind", "USER_REPORTED"))
                        ),
                        verification=VerificationStatus(
                            str(raw.get("verification", "UNKNOWN"))
                        ),
                        metadata={
                            **dict(raw.get("metadata", {})),
                            "inbox_file": path.name,
                            "inbox_path": str(path),
                            "dedupe_key": f"inbox:{path.name}:{file_hash}",
                        },
                    )
                )
            except Exception as exc:
                error_target = rejected / path.name
                os.replace(path, error_target)
                error_target.with_suffix(error_target.suffix + ".error.txt").write_text(
                    str(exc), encoding="utf-8"
                )
        return observations, {"last_count": len(observations)}

    def ack(self, observation: Observation) -> None:
        raw_path = observation.metadata.get("inbox_path")
        if not raw_path:
            return
        path = Path(str(raw_path))
        if not path.exists():
            return
        processed = path.parent / "processed"
        processed.mkdir(parents=True, exist_ok=True)
        target = processed / path.name
        if target.exists():
            target = (
                processed / f"{path.stem}-{observation.observation_id}{path.suffix}"
            )
        os.replace(path, target)
