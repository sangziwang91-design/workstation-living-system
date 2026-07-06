from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Iterable
import hashlib
import json
import uuid


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class EvidenceKind(StrEnum):
    DIRECT = "DIRECT"
    EXTERNAL = "EXTERNAL"
    USER_REPORTED = "USER_REPORTED"
    INFERENCE = "INFERENCE"
    NONE = "NONE"


class VerificationStatus(StrEnum):
    VERIFIED = "VERIFIED"
    INFERENCE = "INFERENCE"
    UNKNOWN = "UNKNOWN"
    REFUTED = "REFUTED"


class RiskLevel(StrEnum):
    READ = "READ"
    REVERSIBLE_WRITE = "REVERSIBLE_WRITE"
    HIGH = "HIGH"
    IRREVERSIBLE = "IRREVERSIBLE"


class EventStatus(StrEnum):
    PENDING = "PENDING"
    RESERVED = "RESERVED"
    PROCESSED = "PROCESSED"
    DEAD_LETTER = "DEAD_LETTER"


class ActionStatus(StrEnum):
    PLANNED = "PLANNED"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    APPROVED = "APPROVED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    REJECTED = "REJECTED"
    UNKNOWN_SIDE_EFFECT = "UNKNOWN_SIDE_EFFECT"
    CANCELLED = "CANCELLED"


class GoalStatus(StrEnum):
    ACTIVE = "ACTIVE"
    BLOCKED = "BLOCKED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    PAUSED = "PAUSED"
    CANCELLED = "CANCELLED"


class CandidateStatus(StrEnum):
    PROPOSED = "PROPOSED"
    SANDBOXED = "SANDBOXED"
    VALIDATED = "VALIDATED"
    APPROVED = "APPROVED"
    PROMOTED = "PROMOTED"
    REJECTED = "REJECTED"
    ROLLED_BACK = "ROLLED_BACK"


class TaskOperation(StrEnum):
    ANSWER = "ANSWER"
    INSPECT = "INSPECT"
    CHANGE = "CHANGE"
    EXECUTE = "EXECUTE"
    PUBLISH = "PUBLISH"


class TaskDomain(StrEnum):
    CODE = "CODE"
    RESEARCH = "RESEARCH"
    DOCUMENT = "DOCUMENT"
    OPERATIONS = "OPERATIONS"
    PERSONAL = "PERSONAL"
    SYSTEM = "SYSTEM"
    MIXED = "MIXED"


class TaskHorizon(StrEnum):
    SHORT = "SHORT"
    SESSION = "SESSION"
    LONG = "LONG"


class TaskParallelism(StrEnum):
    SERIAL = "SERIAL"
    PARALLEL_READ_ONLY = "PARALLEL_READ_ONLY"
    PARALLEL_MIXED = "PARALLEL_MIXED"


class TaskNodeStatus(StrEnum):
    PENDING = "PENDING"
    READY = "READY"
    LEASED = "LEASED"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"


@dataclass(slots=True)
class Observation:
    source: str
    kind: str
    subject: str
    predicate: str
    value: Any
    confidence: float = 1.0
    evidence_kind: EvidenceKind = EvidenceKind.DIRECT
    verification: VerificationStatus = VerificationStatus.VERIFIED
    observed_at: str = field(default_factory=utc_now)
    expires_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    observation_id: str = field(default_factory=lambda: new_id("obs"))

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be within [0, 1]")
        if (
            not self.source.strip()
            or not self.subject.strip()
            or not self.predicate.strip()
        ):
            raise ValueError("source, subject, and predicate are required")

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["evidence_kind"] = self.evidence_kind.value
        data["verification"] = self.verification.value
        return data


@dataclass(slots=True)
class Event:
    event_type: str
    source: str
    payload: dict[str, Any]
    salience_hint: float = 0.5
    occurred_at: str = field(default_factory=utc_now)
    dedupe_key: str | None = None
    event_id: str = field(default_factory=lambda: new_id("evt"))
    status: EventStatus = EventStatus.PENDING
    attempts: int = 0

    def __post_init__(self) -> None:
        if not 0.0 <= self.salience_hint <= 1.0:
            raise ValueError("salience_hint must be within [0, 1]")
        if not self.event_type.strip() or not self.source.strip():
            raise ValueError("event_type and source are required")
        if self.dedupe_key is None:
            stable = {
                "event_type": self.event_type,
                "source": self.source,
                "payload": self.payload,
            }
            self.dedupe_key = digest_json(stable)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data


@dataclass(slots=True)
class Goal:
    title: str
    description: str
    priority: float = 0.5
    success_criteria: list[str] = field(default_factory=list)
    source: str = "user"
    autonomous: bool = False
    parent_goal_id: str | None = None
    deadline: str | None = None
    goal_id: str = field(default_factory=lambda: new_id("goal"))
    status: GoalStatus = GoalStatus.ACTIVE
    progress: float = 0.0
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    rationale: str = ""
    origin: str = "owner"
    task_spec: dict[str, Any] = field(default_factory=dict)
    risk: RiskLevel = RiskLevel.READ

    def __post_init__(self) -> None:
        if not 0.0 <= self.priority <= 1.0:
            raise ValueError("priority must be within [0, 1]")
        if not 0.0 <= self.progress <= 1.0:
            raise ValueError("progress must be within [0, 1]")
        if not self.title.strip():
            raise ValueError("goal title is required")
        if not isinstance(self.risk, RiskLevel):
            self.risk = RiskLevel(str(self.risk))

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        data["risk"] = self.risk.value
        return data


@dataclass(slots=True)
class MemoryItem:
    memory_type: str
    content: dict[str, Any]
    importance: float
    source_ids: list[str]
    confidence: float = 0.5
    created_at: str = field(default_factory=utc_now)
    last_accessed_at: str = field(default_factory=utc_now)
    access_count: int = 0
    memory_id: str = field(default_factory=lambda: new_id("mem"))
    tags: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not 0.0 <= self.importance <= 1.0:
            raise ValueError("importance must be within [0, 1]")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be within [0, 1]")
        if not self.source_ids:
            raise ValueError("memory must reference at least one source")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class DriveState:
    continuity: float = 0.5
    reality_coherence: float = 0.5
    curiosity: float = 0.5
    competence: float = 0.5
    resource_balance: float = 0.5
    relationship_continuity: float = 0.5
    safety: float = 0.8
    value_output: float = 0.5

    def clamp(self) -> None:
        for name in self.__dataclass_fields__:
            setattr(self, name, max(0.0, min(1.0, float(getattr(self, name)))))

    def to_dict(self) -> dict[str, float]:
        return asdict(self)


@dataclass(slots=True)
class AffectState:
    valence: float = 0.0
    arousal: float = 0.2
    control: float = 0.5
    curiosity: float = 0.5
    frustration: float = 0.0
    confidence: float = 0.5
    fatigue: float = 0.0
    social_trust: float = 0.5
    safety_tension: float = 0.0

    def clamp(self) -> None:
        self.valence = max(-1.0, min(1.0, self.valence))
        for name in self.__dataclass_fields__:
            if name == "valence":
                continue
            setattr(self, name, max(0.0, min(1.0, float(getattr(self, name)))))

    def to_dict(self) -> dict[str, float]:
        return asdict(self)


@dataclass(slots=True)
class WorkspaceItem:
    item_type: str
    reference_id: str
    summary: str
    salience: float
    reasons: list[str] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 0.0 <= self.salience <= 1.0:
            raise ValueError("salience must be within [0, 1]")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ActionSpec:
    tool: str
    arguments: dict[str, Any]
    purpose: str
    expected_result: str
    risk: RiskLevel = RiskLevel.READ
    goal_id: str | None = None
    skill_id: str | None = None
    action_id: str = field(default_factory=lambda: new_id("act"))
    idempotency_key: str | None = None
    acceptance: list[str] = field(default_factory=list)
    status: ActionStatus = ActionStatus.PLANNED

    def __post_init__(self) -> None:
        if not self.tool.strip() or not self.purpose.strip():
            raise ValueError("tool and purpose are required")
        if self.idempotency_key is None:
            stable = {
                "tool": self.tool,
                "arguments": self.arguments,
                "purpose": self.purpose,
            }
            self.idempotency_key = digest_json(stable)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["risk"] = self.risk.value
        data["status"] = self.status.value
        return data


@dataclass(slots=True)
class Plan:
    rationale: str
    actions: list[ActionSpec]
    memory_ids: list[str] = field(default_factory=list)
    world_fact_ids: list[str] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)
    plan_id: str = field(default_factory=lambda: new_id("plan"))
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "rationale": self.rationale,
            "actions": [a.to_dict() for a in self.actions],
            "memory_ids": self.memory_ids,
            "world_fact_ids": self.world_fact_ids,
            "unknowns": self.unknowns,
            "created_at": self.created_at,
        }


@dataclass(slots=True)
class ActionResult:
    action_id: str
    success: bool
    output: dict[str, Any]
    started_at: str
    finished_at: str = field(default_factory=utc_now)
    error: str | None = None
    observed_side_effect: bool = False
    result_id: str = field(default_factory=lambda: new_id("result"))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SkillDefinition:
    name: str
    description: str
    trigger_terms: list[str]
    steps: list[dict[str, Any]]
    version: int = 1
    risk: RiskLevel = RiskLevel.READ
    success_rate: float = 0.0
    use_count: int = 0
    status: CandidateStatus = CandidateStatus.PROPOSED
    source_episode_ids: list[str] = field(default_factory=list)
    skill_id: str = field(default_factory=lambda: new_id("skill"))
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["risk"] = self.risk.value
        data["status"] = self.status.value
        return data


def ensure_unique_ids(items: Iterable[Any], attribute: str) -> None:
    seen: set[str] = set()
    for item in items:
        value = getattr(item, attribute)
        if value in seen:
            raise ValueError(f"duplicate {attribute}: {value}")
        seen.add(value)
