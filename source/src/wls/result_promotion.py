from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .schemas import digest_json, new_id, utc_now


@dataclass(slots=True)
class ResultCandidate:
    candidate_id: str
    source_worker: str
    source_scope: str
    content: dict[str, Any]
    content_digest: str
    evidence_ids: list[str]
    created_at: str = field(default_factory=utc_now)


@dataclass(slots=True)
class PromotionDecision:
    decision_id: str
    candidate_id: str
    admitted: bool
    reason: str
    admitted_to: str = ""
    contamination_check: dict[str, bool] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "candidate_id": self.candidate_id,
            "admitted": self.admitted,
            "reason": self.reason,
            "admitted_to": self.admitted_to,
            "contamination_check": self.contamination_check,
        }


class ResultPromotionGate:
    """Prevents worker outputs from silently becoming shared truth.
    Enforces scope, provenance, contamination checks, and delivery
    acknowledgement before admitting results to canonical memory.
    """

    def review(
        self,
        candidate: ResultCandidate,
        *,
        allowed_scopes: list[str] | None = None,
        target_scope: str = "project",
        require_delivery_ack: bool = True,
    ) -> PromotionDecision:
        scopes = allowed_scopes or [target_scope]
        effective_target = target_scope
        if allowed_scopes and target_scope == "project":
            effective_target = allowed_scopes[0]

        if candidate.source_scope not in scopes:
            return PromotionDecision(
                decision_id=new_id("promo"),
                candidate_id=candidate.candidate_id,
                admitted=False,
                reason=f"source scope {candidate.source_scope} not in allowed scopes: {scopes}",
            )

        if not candidate.source_worker:
            return PromotionDecision(
                decision_id=new_id("promo"),
                candidate_id=candidate.candidate_id,
                admitted=False,
                reason="missing source worker identity",
            )

        if not candidate.content_digest:
            return PromotionDecision(
                decision_id=new_id("promo"),
                candidate_id=candidate.candidate_id,
                admitted=False,
                reason="missing content digest",
            )

        if not candidate.evidence_ids:
            return PromotionDecision(
                decision_id=new_id("promo"),
                candidate_id=candidate.candidate_id,
                admitted=False,
                reason="missing evidence provenance",
            )

        contamination = {
            "memory_id_field_present": "memory_id" in candidate.content,
            "active_field_present": "active" in candidate.content,
            "canonical_write_attempt": bool(
                candidate.content.get("_canonical_write")
            ),
        }

        if any(contamination.values()):
            return PromotionDecision(
                decision_id=new_id("promo"),
                candidate_id=candidate.candidate_id,
                admitted=False,
                reason="content contains canonical authority fields",
                contamination_check=contamination,
            )

        if require_delivery_ack and not candidate.content.get("_delivery_ack"):
            return PromotionDecision(
                decision_id=new_id("promo"),
                candidate_id=candidate.candidate_id,
                admitted=False,
                reason="delivery acknowledgement required but not present",
            )

        candidate.content["_promotion_source"] = candidate.source_worker
        candidate.content["_promotion_scope"] = candidate.source_scope
        candidate.content["_promotion_digest"] = candidate.content_digest
        candidate.content["_promoted_at"] = utc_now()

        return PromotionDecision(
            decision_id=new_id("promo"),
            candidate_id=candidate.candidate_id,
            admitted=True,
            reason="passed provenance, scope, and contamination checks",
            admitted_to=effective_target,
            contamination_check=contamination,
        )
