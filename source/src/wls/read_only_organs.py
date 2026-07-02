from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .channel_gateway import ChannelMessage
from .schemas import Event, digest_json, utc_now
from .stores import EventStore
from .workbench import WorkbenchTemplate


SUPPORTED_READ_ONLY_ORGANS = {
    "research",
    "browser",
    "file",
    "coding",
    "content",
    "social_research",
    "multimodal",
}


@dataclass(frozen=True, slots=True)
class ReadOnlyTaskReceipt:
    request_id: str
    organ_id: str
    status: str
    event_id: str | None
    inserted: bool
    canonical_owner: str = "EventStore"
    allowed_next_authority: str = "Planner"
    writes_canonical_state: bool = False
    direct_tool_execution: bool = False
    risk_ceiling: str = "READ"
    side_effect_class: str = "none"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ReadOnlyTaskRequest:
    request_id: str
    organ_id: str
    owner_intent: str
    source: str = "owner_console"
    inputs: dict[str, Any] = field(default_factory=dict)
    evidence_required: list[str] = field(default_factory=lambda: ["event_receipt"])
    received_at: str = field(default_factory=utc_now)
    risk_ceiling: str = "READ"
    side_effect_class: str = "none"

    def validate(self) -> None:
        if self.organ_id not in SUPPORTED_READ_ONLY_ORGANS:
            raise ValueError(f"unsupported read-only organ: {self.organ_id}")
        if not self.request_id.strip() or not self.owner_intent.strip():
            raise ValueError("request_id and owner_intent are required")
        if self.risk_ceiling != "READ" or self.side_effect_class != "none":
            raise PermissionError("read-only organs cannot request side effects")
        if not self.evidence_required:
            raise ValueError("read-only task requires evidence")

    def to_event(self) -> Event:
        self.validate()
        payload = {
            "request_id": self.request_id,
            "organ_id": self.organ_id,
            "owner_intent": self.owner_intent,
            "inputs": dict(self.inputs),
            "risk_ceiling": self.risk_ceiling,
            "side_effect_class": self.side_effect_class,
            "evidence_required": list(self.evidence_required),
            "writes_canonical_state": False,
            "direct_tool_execution": False,
            "allowed_next_authority": "Planner",
        }
        return Event(
            event_type="read_only_task.requested",
            source=f"organ:{self.organ_id}",
            payload=payload,
            salience_hint=0.7,
            occurred_at=self.received_at,
            dedupe_key=digest_json(
                {
                    "request_id": self.request_id,
                    "organ_id": self.organ_id,
                    "source": self.source,
                }
            ),
        )

    def to_workbench_template(self) -> dict[str, Any]:
        self.validate()
        return WorkbenchTemplate(
            template_id=f"read_only_{self.organ_id}_task",
            canonical_owner="planning",
            steps=[
                {
                    "action": "ingest_owner_intent",
                    "uses_authority": "events",
                    "output": "canonical_event",
                },
                {
                    "action": "prepare_read_only_plan_candidate",
                    "uses_authority": "planning",
                    "output": "candidate_plan",
                },
                {
                    "action": "request_evidence_receipts",
                    "uses_authority": "evidence",
                    "output": "receipt_requirements",
                },
            ],
            evidence_required=list(self.evidence_required),
        ).to_dict()

    def submit(self, events: EventStore) -> ReadOnlyTaskReceipt:
        event_id, inserted = events.add_event(self.to_event())
        return ReadOnlyTaskReceipt(
            request_id=self.request_id,
            organ_id=self.organ_id,
            status="QUEUED_EVENT_ONLY",
            event_id=event_id,
            inserted=inserted,
        )

    @classmethod
    def from_channel_message(
        cls, organ_id: str, message: ChannelMessage
    ) -> ReadOnlyTaskRequest:
        return cls(
            request_id=message.message_id,
            organ_id=organ_id,
            owner_intent=message.content,
            source=message.channel,
            inputs={"sender_id": message.sender_id, "metadata": dict(message.metadata)},
            received_at=message.received_at,
        )
