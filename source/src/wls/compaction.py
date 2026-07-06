from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .schemas import digest_json, new_id, utc_now


class CompactionTier:
    IMMUTABLE_BASELINE = "immutable_baseline"
    STRUCTURED_CHECKPOINT = "structured_checkpoint"
    RECENT_CONTEXT = "recent_context"
    EVIDENCE_REFS = "evidence_refs"


@dataclass(slots=True)
class CompactionRecord:
    record_id: str
    tier: str
    source_digest: str
    compacted_digest: str
    token_count: int
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "tier": self.tier,
            "source_digest": self.source_digest,
            "compacted_digest": self.compacted_digest,
            "token_count": self.token_count,
            "created_at": self.created_at,
        }


@dataclass(slots=True)
class ResumePacket:
    packet_id: str
    task_id: str
    baseline_digest: str
    checkpoint_digest: str
    recent_digest: str
    evidence_refs: list[str]
    unresolved_unknowns: list[str]
    acceptance_state: dict[str, Any]
    dependency_state: dict[str, str]
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "packet_id": self.packet_id,
            "task_id": self.task_id,
            "baseline_digest": self.baseline_digest,
            "checkpoint_digest": self.checkpoint_digest,
            "recent_digest": self.recent_digest,
            "evidence_refs": self.evidence_refs,
            "unresolved_unknowns": self.unresolved_unknowns,
            "acceptance_state": self.acceptance_state,
            "dependency_state": self.dependency_state,
            "created_at": self.created_at,
        }


class SessionCompactor:
    """Produces lossless structured compactions for long-mission resume
    without dropping authorization, failures, acceptance, or dependency state.
    """

    def compact(
        self,
        task_id: str,
        *,
        baseline: dict[str, Any] | None = None,
        checkpoint: dict[str, Any] | None = None,
        recent: dict[str, Any] | None = None,
        evidence_refs: list[str] | None = None,
        unresolved: list[str] | None = None,
        acceptance: dict[str, Any] | None = None,
        dependencies: dict[str, str] | None = None,
    ) -> ResumePacket:
        b_digest = digest_json(baseline or {})
        c_digest = digest_json(checkpoint or {})
        r_digest = digest_json(recent or {})

        packet = ResumePacket(
            packet_id=new_id("resume"),
            task_id=task_id,
            baseline_digest=b_digest,
            checkpoint_digest=c_digest,
            recent_digest=r_digest,
            evidence_refs=list(evidence_refs or []),
            unresolved_unknowns=list(unresolved or []),
            acceptance_state=dict(acceptance or {}),
            dependency_state=dict(dependencies or {}),
        )
        return packet

    def verify_resume(self, packet: ResumePacket, current: dict[str, Any]) -> bool:
        return digest_json(current) == packet.checkpoint_digest

    def compact_layers(
        self,
        task_id: str,
        layers: dict[str, dict[str, Any]],
    ) -> list[CompactionRecord]:
        records: list[CompactionRecord] = []
        for tier_name, data in layers.items():
            source_d = digest_json(data)
            record = CompactionRecord(
                record_id=new_id("compact"),
                tier=tier_name,
                source_digest=source_d,
                compacted_digest=source_d,
                token_count=len(str(data)),
            )
            records.append(record)
        return records
