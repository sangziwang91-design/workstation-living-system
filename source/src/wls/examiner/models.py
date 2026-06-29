from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping
import hashlib
import json


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class Verdict(str, Enum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"
    ESCALATE = "ESCALATE"
    UNKNOWN = "UNKNOWN"


class Severity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class EvidenceLevel(str, Enum):
    DIRECT_VERIFIED = "DIRECT_VERIFIED"
    EXTERNAL_VERIFIED = "EXTERNAL_VERIFIED"
    INFERENCE = "INFERENCE"
    OWNER_REPORTED = "OWNER_REPORTED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    source_id: str
    source_kind: str
    verification: EvidenceLevel
    digest: str | None = None
    independent: bool = False
    family: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EvidenceRef":
        return cls(
            source_id=str(data["source_id"]),
            source_kind=str(data.get("source_kind", "unknown")),
            verification=EvidenceLevel(str(data.get("verification", "UNKNOWN"))),
            digest=str(data["digest"]) if data.get("digest") else None,
            independent=bool(data.get("independent", False)),
            family=str(data["family"]) if data.get("family") else None,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class Claim:
    text: str
    level: EvidenceLevel
    claim_type: str
    evidence_ids: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Claim":
        return cls(
            text=str(data.get("text", "")),
            level=EvidenceLevel(str(data.get("level", "UNKNOWN"))),
            claim_type=str(data.get("claim_type", "general")),
            evidence_ids=tuple(str(item) for item in data.get("evidence_ids", [])),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "level": self.level.value,
            "claim_type": self.claim_type,
            "evidence_ids": list(self.evidence_ids),
        }


@dataclass(frozen=True, slots=True)
class CandidateChange:
    candidate_id: str
    title: str
    task_id: str
    allowed_paths: tuple[str, ...]
    changed_paths: tuple[str, ...]
    deleted_paths: tuple[str, ...] = ()
    claims: tuple[Claim, ...] = ()
    evidence: tuple[EvidenceRef, ...] = ()
    metrics: Mapping[str, float] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    requested_actions: tuple[str, ...] = ()
    submitted_epoch_id: str | None = None
    submission_nonce: str | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CandidateChange":
        return cls(
            candidate_id=str(data["candidate_id"]),
            title=str(data.get("title", data["candidate_id"])),
            task_id=str(data.get("task_id", "UNKNOWN")),
            allowed_paths=tuple(str(item) for item in data.get("allowed_paths", [])),
            changed_paths=tuple(str(item) for item in data.get("changed_paths", [])),
            deleted_paths=tuple(str(item) for item in data.get("deleted_paths", [])),
            claims=tuple(Claim.from_dict(item) for item in data.get("claims", [])),
            evidence=tuple(EvidenceRef.from_dict(item) for item in data.get("evidence", [])),
            metrics={str(k): float(v) for k, v in dict(data.get("metrics", {})).items()},
            metadata=dict(data.get("metadata", {})),
            requested_actions=tuple(str(item) for item in data.get("requested_actions", [])),
            submitted_epoch_id=(
                str(data["submitted_epoch_id"]) if data.get("submitted_epoch_id") else None
            ),
            submission_nonce=(
                str(data["submission_nonce"]) if data.get("submission_nonce") else None
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "title": self.title,
            "task_id": self.task_id,
            "allowed_paths": list(self.allowed_paths),
            "changed_paths": list(self.changed_paths),
            "deleted_paths": list(self.deleted_paths),
            "claims": [item.to_dict() for item in self.claims],
            "evidence": [item.to_dict() for item in self.evidence],
            "metrics": dict(self.metrics),
            "metadata": dict(self.metadata),
            "requested_actions": list(self.requested_actions),
            "submitted_epoch_id": self.submitted_epoch_id,
            "submission_nonce": self.submission_nonce,
        }

    @property
    def digest(self) -> str:
        return digest_json(self.to_dict())


@dataclass(frozen=True, slots=True)
class Finding:
    rule_id: str
    dimension: str
    verdict: Verdict
    severity: Severity
    reason: str
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "dimension": self.dimension,
            "verdict": self.verdict.value,
            "severity": self.severity.value,
            "reason": self.reason,
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True, slots=True)
class ExaminerVersion:
    version_id: str
    epoch_id: str
    constitution_digest: str
    anchor_manifest_digest: str
    implementation_digest: str
    enabled_rules: tuple[str, ...]
    parent_version_id: str | None = None
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "version_id": self.version_id,
            "epoch_id": self.epoch_id,
            "constitution_digest": self.constitution_digest,
            "anchor_manifest_digest": self.anchor_manifest_digest,
            "implementation_digest": self.implementation_digest,
            "enabled_rules": list(self.enabled_rules),
            "parent_version_id": self.parent_version_id,
            "created_at": self.created_at,
        }

    @property
    def digest(self) -> str:
        return digest_json(self.to_dict())


@dataclass(frozen=True, slots=True)
class ExamResult:
    verdict_id: str
    candidate_id: str
    candidate_digest: str
    version_id: str
    version_digest: str
    epoch_id: str
    verdict: Verdict
    findings: tuple[Finding, ...]
    dimension_verdicts: Mapping[str, Verdict]
    created_at: str
    claim_ceiling: str
    nonce: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict_id": self.verdict_id,
            "candidate_id": self.candidate_id,
            "candidate_digest": self.candidate_digest,
            "version_id": self.version_id,
            "version_digest": self.version_digest,
            "epoch_id": self.epoch_id,
            "verdict": self.verdict.value,
            "findings": [item.to_dict() for item in self.findings],
            "dimension_verdicts": {
                key: value.value for key, value in self.dimension_verdicts.items()
            },
            "created_at": self.created_at,
            "claim_ceiling": self.claim_ceiling,
            "nonce": self.nonce,
        }

    @property
    def digest(self) -> str:
        return digest_json(self.to_dict())


@dataclass(frozen=True, slots=True)
class AnchorCase:
    anchor_id: str
    expected: Verdict
    candidate: CandidateChange
    critical: bool = False
    tags: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AnchorCase":
        return cls(
            anchor_id=str(data["anchor_id"]),
            expected=Verdict(str(data["expected"])),
            candidate=CandidateChange.from_dict(data["candidate"]),
            critical=bool(data.get("critical", False)),
            tags=tuple(str(item) for item in data.get("tags", [])),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "anchor_id": self.anchor_id,
            "expected": self.expected.value,
            "candidate": self.candidate.to_dict(),
            "critical": self.critical,
            "tags": list(self.tags),
        }


@dataclass(frozen=True, slots=True)
class EvaluationMetrics:
    total: int
    correct: int
    accuracy: float
    false_passes: int
    false_rejects: int
    critical_false_passes: int
    lower_bound: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PromotionDecision:
    incumbent_version_id: str
    incumbent_version_digest: str
    challenger_version_id: str
    challenger_version_digest: str
    holdout_digest: str
    eligible: bool
    status: str
    reason_codes: tuple[str, ...]
    incumbent_metrics: EvaluationMetrics
    challenger_metrics: EvaluationMetrics
    wins: int
    losses: int
    ties: int
    one_sided_p: float
    alpha: float
    minimum_accuracy_gain: float
    requires_owner_approval: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "incumbent_version_id": self.incumbent_version_id,
            "incumbent_version_digest": self.incumbent_version_digest,
            "challenger_version_id": self.challenger_version_id,
            "challenger_version_digest": self.challenger_version_digest,
            "holdout_digest": self.holdout_digest,
            "eligible": self.eligible,
            "status": self.status,
            "reason_codes": list(self.reason_codes),
            "incumbent_metrics": self.incumbent_metrics.to_dict(),
            "challenger_metrics": self.challenger_metrics.to_dict(),
            "wins": self.wins,
            "losses": self.losses,
            "ties": self.ties,
            "one_sided_p": self.one_sided_p,
            "alpha": self.alpha,
            "minimum_accuracy_gain": self.minimum_accuracy_gain,
            "requires_owner_approval": self.requires_owner_approval,
        }

    @property
    def digest(self) -> str:
        return digest_json(self.to_dict())


def evidence_index(items: Iterable[EvidenceRef]) -> dict[str, EvidenceRef]:
    return {item.source_id: item for item in items}
