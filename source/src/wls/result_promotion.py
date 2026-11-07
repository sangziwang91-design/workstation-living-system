"""Fail-closed, evidence-bound review of untrusted worker results.

Review never mutates worker content. A decision supplies metadata separately;
the caller remains responsible for any authorized persistent write.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from typing import Any

from .schemas import digest_json, new_id, utc_now

IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
MAX_CONTENT_BYTES = 1_000_000
RESERVED = frozenset({
    "memory_id", "active", "_canonical_write", "_promotion_source",
    "_promotion_scope", "_promotion_digest", "_promoted_at",
})


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
    promotion_metadata: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "candidate_id": self.candidate_id,
            "admitted": self.admitted,
            "reason": self.reason,
            "admitted_to": self.admitted_to,
            "contamination_check": self.contamination_check,
            "promotion_metadata": self.promotion_metadata,
        }


def _valid_identifier(value: object) -> bool:
    return isinstance(value, str) and bool(IDENTIFIER.fullmatch(value))


class ResultPromotionGate:
    """Review provenance, target scope and frozen bytes before promotion."""

    def review(
        self,
        candidate: ResultCandidate,
        *,
        allowed_scopes: list[str] | None = None,
        target_scope: str = "project",
        require_delivery_ack: bool = True,
    ) -> PromotionDecision:
        def reject(reason: str, contamination: dict[str, bool] | None = None
                   ) -> PromotionDecision:
            return PromotionDecision(
                decision_id=new_id("promo"), candidate_id=candidate.candidate_id,
                admitted=False, reason=reason,
                contamination_check=contamination or {},
            )

        if not _valid_identifier(candidate.candidate_id):
            return reject("invalid candidate identifier")
        if not _valid_identifier(candidate.source_worker):
            return reject("missing or invalid source worker identity")
        if not _valid_identifier(candidate.source_scope):
            return reject("missing or invalid source scope")
        if not _valid_identifier(target_scope):
            return reject("invalid target scope")
        if allowed_scopes is None:
            scopes = [target_scope]
        elif (not isinstance(allowed_scopes, list) or not allowed_scopes
              or any(not _valid_identifier(scope) for scope in allowed_scopes)
              or len(set(allowed_scopes)) != len(allowed_scopes)):
            return reject("allowed scopes must be explicit, nonempty and unique")
        else:
            scopes = allowed_scopes
        if target_scope not in scopes:
            return reject("target scope not in explicitly allowed scopes")
        if candidate.source_scope not in scopes:
            return reject("source scope not in explicitly allowed scopes")
        if not isinstance(candidate.content, dict):
            return reject("worker content must be a JSON object")
        if any(not isinstance(key, str) for key in candidate.content):
            return reject("worker content contains non-string keys")
        contamination = {
            "memory_id_field_present": "memory_id" in candidate.content,
            "active_field_present": "active" in candidate.content,
            "canonical_write_attempt": "_canonical_write" in candidate.content,
            "promotion_metadata_injection": any(
                key in candidate.content for key in RESERVED
                if key.startswith("_promotion_") or key == "_promoted_at"
            ),
        }
        if any(contamination.values()):
            return reject("content contains canonical authority fields", contamination)
        if require_delivery_ack and candidate.content.get("_delivery_ack") is not True:
            return reject("delivery acknowledgement required but not present")
        if (not isinstance(candidate.evidence_ids, list)
            or not candidate.evidence_ids
            or any(not _valid_identifier(x) for x in candidate.evidence_ids)
            or len(set(candidate.evidence_ids)) != len(candidate.evidence_ids)):
            return reject("missing, duplicate or invalid evidence provenance")
        if not isinstance(candidate.content_digest, str) or not DIGEST.fullmatch(
            candidate.content_digest
        ):
            return reject("missing or malformed content digest")
        try:
            raw = json.dumps(
                candidate.content, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"), allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError, UnicodeError, OverflowError):
            return reject("worker content is not canonical JSON")
        if len(raw) > MAX_CONTENT_BYTES:
            return reject("worker content exceeds bounded review size")
        if digest_json(candidate.content) != candidate.content_digest:
            return reject("content digest does not match frozen worker bytes")
        metadata = {
            "_promotion_source": candidate.source_worker,
            "_promotion_scope": candidate.source_scope,
            "_promotion_digest": candidate.content_digest,
            "_promoted_at": utc_now(),
        }
        return PromotionDecision(
            decision_id=new_id("promo"), candidate_id=candidate.candidate_id,
            admitted=True, reason="passed provenance, scope, and contamination checks",
            admitted_to=target_scope, contamination_check=contamination,
            promotion_metadata=metadata,
        )
