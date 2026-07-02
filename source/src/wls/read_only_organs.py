from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .channel_gateway import ChannelMessage
from .schemas import Event, RiskLevel, digest_json, utc_now
from .stores import EventStore
from .workbench import WorkbenchTemplate


READ_ONLY_TOOL_HINTS = {
    "http_get",
    "inspect_asset",
    "inspect_coding_candidate",
    "list_directory",
    "noop",
    "read_file",
}
FORBIDDEN_TOOL_HINTS = {
    "delete_file",
    "deploy",
    "merge",
    "promote_skill",
    "send_payment",
    "write_file",
}


@dataclass(frozen=True, slots=True)
class ReadOnlyOrganProfile:
    organ_id: str
    canonical_owner: str
    tool_hints: tuple[str, ...]
    evidence_required: tuple[str, ...]
    planner_contract: str
    blocked_operations: tuple[str, ...] = (
        "canonical_write",
        "direct_tool_execution",
        "goal_completion",
        "memory_write",
        "skill_promotion",
        "deployment",
        "payment",
        "secret_access",
    )

    def validate(self) -> None:
        if self.canonical_owner != "planning":
            raise ValueError("read-only organ profiles must bind to Planner")
        if not self.tool_hints:
            raise ValueError("read-only organ profile requires tool hints")
        if not self.evidence_required:
            raise ValueError("read-only organ profile requires evidence")
        forbidden = sorted(set(self.tool_hints) & FORBIDDEN_TOOL_HINTS)
        if forbidden:
            raise PermissionError(f"forbidden tool hints: {', '.join(forbidden)}")
        unsupported = sorted(set(self.tool_hints) - READ_ONLY_TOOL_HINTS)
        if unsupported:
            raise PermissionError(f"unsupported read-only tool hints: {', '.join(unsupported)}")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        data = asdict(self)
        data["status"] = "READ_ONLY_PROFILE"
        return data


ORGAN_PROFILES: dict[str, ReadOnlyOrganProfile] = {
    "research": ReadOnlyOrganProfile(
        organ_id="research",
        canonical_owner="planning",
        tool_hints=("read_file", "list_directory", "http_get"),
        evidence_required=("source_hashes", "claim_trace", "citation_audit"),
        planner_contract="prepare traceable research plan candidate",
    ),
    "browser": ReadOnlyOrganProfile(
        organ_id="browser",
        canonical_owner="planning",
        tool_hints=("http_get",),
        evidence_required=("allowed_host", "navigation_log", "content_hash"),
        planner_contract="prepare allowlisted browser inspection plan candidate",
    ),
    "file": ReadOnlyOrganProfile(
        organ_id="file",
        canonical_owner="planning",
        tool_hints=("list_directory", "read_file"),
        evidence_required=("path_scope", "file_hash_or_listing"),
        planner_contract="prepare path-scoped file inspection plan candidate",
    ),
    "coding": ReadOnlyOrganProfile(
        organ_id="coding",
        canonical_owner="planning",
        tool_hints=("inspect_coding_candidate",),
        evidence_required=("worktree_path", "base_sha", "focused_tests"),
        planner_contract="prepare disposable coding analysis plan candidate",
    ),
    "content": ReadOnlyOrganProfile(
        organ_id="content",
        canonical_owner="planning",
        tool_hints=("read_file", "noop"),
        evidence_required=("source_hashes", "fact_audit"),
        planner_contract="prepare content workbench plan candidate",
    ),
    "social_research": ReadOnlyOrganProfile(
        organ_id="social_research",
        canonical_owner="planning",
        tool_hints=("read_file", "http_get"),
        evidence_required=("raw_data", "interpretation_boundary", "alternatives"),
        planner_contract="prepare social research plan candidate",
    ),
    "multimodal": ReadOnlyOrganProfile(
        organ_id="multimodal",
        canonical_owner="planning",
        tool_hints=("inspect_asset", "noop"),
        evidence_required=("asset_hashes", "model_or_source_provenance"),
        planner_contract="prepare multimodal artifact review plan candidate",
    ),
}
SUPPORTED_READ_ONLY_ORGANS = set(ORGAN_PROFILES)


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


@dataclass(frozen=True, slots=True)
class ReadOnlyPlanCandidate:
    request_id: str
    organ_id: str
    status: str
    canonical_owner: str
    rationale: str
    candidate_actions: list[dict[str, Any]]
    evidence_required: list[str]
    blocked_operations: list[str]
    requires_planner_admission: bool = True
    writes_canonical_state: bool = False
    direct_tool_execution: bool = False
    risk_ceiling: str = "READ"
    side_effect_class: str = "none"

    def __post_init__(self) -> None:
        if self.status != "PLAN_CANDIDATE_ONLY":
            raise ValueError("read-only plan candidates cannot be admitted directly")
        if self.canonical_owner != "Planner":
            raise ValueError("read-only plan candidates must be owned by Planner")
        for action in self.candidate_actions:
            if action.get("risk") != RiskLevel.READ.value:
                raise PermissionError("read-only plan candidate action must be READ")
            if action.get("tool") in FORBIDDEN_TOOL_HINTS:
                raise PermissionError("read-only plan candidate includes forbidden tool")

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

    def profile(self) -> ReadOnlyOrganProfile:
        self.validate()
        return ORGAN_PROFILES[self.organ_id]

    def to_plan_candidate(self) -> dict[str, Any]:
        profile = self.profile()
        profile.validate()
        actions = [
            {
                "tool": tool,
                "arguments": {
                    "request_id": self.request_id,
                    "organ_id": self.organ_id,
                    "inputs": dict(self.inputs),
                },
                "purpose": profile.planner_contract,
                "expected_result": "bounded read-only evidence receipt",
                "risk": RiskLevel.READ.value,
                "candidate_only": True,
            }
            for tool in profile.tool_hints
        ]
        candidate = ReadOnlyPlanCandidate(
            request_id=self.request_id,
            organ_id=self.organ_id,
            status="PLAN_CANDIDATE_ONLY",
            canonical_owner="Planner",
            rationale=profile.planner_contract,
            candidate_actions=actions,
            evidence_required=list(
                dict.fromkeys([*self.evidence_required, *profile.evidence_required])
            ),
            blocked_operations=list(profile.blocked_operations),
        )
        return candidate.to_dict()

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
