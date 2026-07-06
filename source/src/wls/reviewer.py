from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .schemas import digest_json, new_id, utc_now


@dataclass(slots=True)
class ReviewOpinion:
    reviewer_id: str
    verdict: str
    confidence: float
    rationale: str
    evidence_ids: list[str]
    timestamp: str = field(default_factory=utc_now)


@dataclass(slots=True)
class DisagreementRecord:
    record_id: str
    target_id: str
    opinions: list[ReviewOpinion]
    disagreement_map: dict[str, list[str]]
    consensus_verdict: str | None
    escalated: bool
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "target_id": self.target_id,
            "opinion_count": len(self.opinions),
            "verdicts": [o.verdict for o in self.opinions],
            "disagreement_map": self.disagreement_map,
            "consensus_verdict": self.consensus_verdict,
            "escalated": self.escalated,
            "created_at": self.created_at,
        }


class ReviewerDiversityPolicy:
    def check(self, reviewer_ids: list[str]) -> dict[str, Any]:
        unique = len(set(reviewer_ids))
        total = len(reviewer_ids)
        return {
            "unique_reviewers": unique,
            "total_opinions": total,
            "diverse": unique >= 2 and unique == total,
            "same_provider_risk": unique < total,
        }


class HeterogeneousReviewer:
    """Uses independent providers/agents for review, preserving disagreements
    instead of averaging them away. Deterministic oracle outranks review.
    """

    def __init__(self) -> None:
        self._reviewers: dict[str, Callable[[dict[str, Any]], ReviewOpinion]] = {}
        self._diversity = ReviewerDiversityPolicy()

    def register(self, reviewer_id: str, fn: Callable[[dict[str, Any]], ReviewOpinion]) -> None:
        self._reviewers[reviewer_id] = fn

    def review(
        self,
        target_id: str,
        context: dict[str, Any],
        *,
        deterministic_verdict: str | None = None,
    ) -> DisagreementRecord:
        opinions: list[ReviewOpinion] = []
        for rid, fn in self._reviewers.items():
            try:
                opinion = fn(context)
            except Exception as exc:
                opinion = ReviewOpinion(
                    reviewer_id=rid,
                    verdict="ERROR",
                    confidence=0.0,
                    rationale=f"reviewer error: {exc}",
                    evidence_ids=[],
                )
            opinions.append(opinion)

        if deterministic_verdict:
            return DisagreementRecord(
                record_id=new_id("disagree"),
                target_id=target_id,
                opinions=opinions,
                disagreement_map=self._build_disagreement(opinions),
                consensus_verdict=deterministic_verdict,
                escalated=False,
            )

        verdicts = [o.verdict for o in opinions]
        unique_verdicts = set(verdicts)
        consensus = None
        escalated = False

        if len(unique_verdicts) == 1:
            consensus = verdicts[0]
        elif len(opinions) >= 2:
            escalated = True

        return DisagreementRecord(
            record_id=new_id("disagree"),
            target_id=target_id,
            opinions=opinions,
            disagreement_map=self._build_disagreement(opinions),
            consensus_verdict=consensus,
            escalated=escalated,
        )

    def advisory_review(
        self,
        target_id: str,
        context: dict[str, Any],
    ) -> DisagreementRecord:
        return self.review(target_id, context)

    def _build_disagreement(self, opinions: list[ReviewOpinion]) -> dict[str, list[str]]:
        groups: dict[str, list[str]] = {}
        for o in opinions:
            groups.setdefault(o.verdict, []).append(o.reviewer_id)
        return groups
