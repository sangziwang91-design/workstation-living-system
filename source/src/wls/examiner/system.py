from __future__ import annotations

from datetime import UTC, datetime
from itertools import count
from pathlib import PurePosixPath
import re
from typing import Callable, Iterable
import uuid

from .anchors import AnchorBank
from .constitution import Constitution
from .integrity import current_implementation_digest
from .metrics import calculate_metrics, one_sided_sign_test
from .models import (
    CandidateChange,
    ExamResult,
    ExaminerVersion,
    Finding,
    PromotionDecision,
    Severity,
    Verdict,
)
from .rules import RULES, apply_rules
from .store import ExaminerStore


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class IdFactory:
    """Generate restart-safe identifiers by default.

    Deterministic mode is reserved for reproducible sandbox evidence. Runtime
    mode uses UUID entropy so process restarts cannot reuse primary keys.
    """

    def __init__(self, prefix: str = "exam", *, deterministic: bool = False) -> None:
        self.prefix = prefix
        self.deterministic = deterministic
        self._counter = count(1)

    def __call__(self, kind: str) -> str:
        if self.deterministic:
            suffix = f"{next(self._counter):08d}"
        else:
            suffix = uuid.uuid4().hex
        return f"{self.prefix}-{kind}-{suffix}"


class ExaminerSystem:
    """Epoch-frozen, evidence-bound evaluator evolution for WLS.

    The system is advisory by default. It can record verdicts and prepare promotion
    decisions, but evaluator replacement requires an explicit owner approval.
    """

    def __init__(
        self,
        constitution: Constitution,
        store: ExaminerStore,
        active_version: ExaminerVersion,
        *,
        clock: Callable[[], str] = utc_now,
        id_factory: Callable[[str], str] | None = None,
    ) -> None:
        self.constitution = constitution
        self.store = store
        self.active_version = active_version
        self.clock = clock
        self.id_factory = id_factory or IdFactory()
        self._validate_version(active_version)
        self.store.register_version(active_version, status="ACTIVE")
        active = self.store.active_epoch()
        if active is None:
            self.store.open_epoch(active_version, self.clock())
        else:
            self.store.assert_epoch_frozen(active_version)

    def _validate_version(self, version: ExaminerVersion) -> None:
        if version.constitution_digest != self.constitution.digest:
            raise RuntimeError("examiner version constitution digest mismatch")
        if version.implementation_digest != current_implementation_digest():
            raise RuntimeError("examiner version implementation digest mismatch")
        unknown = sorted(set(version.enabled_rules) - set(RULES))
        if unknown:
            raise ValueError(f"unknown examiner rules: {unknown}")
        if len(version.enabled_rules) != len(set(version.enabled_rules)):
            raise ValueError("duplicate examiner rules are not allowed")
        if not version.anchor_manifest_digest:
            raise ValueError("anchor manifest digest is required")

    def evaluate(
        self,
        candidate: CandidateChange,
        *,
        version: ExaminerVersion | None = None,
        record: bool = True,
    ) -> ExamResult:
        selected = version or self.active_version
        self._validate_version(selected)
        if selected.version_id == self.active_version.version_id:
            self.store.assert_epoch_frozen(self.active_version)
        findings = self._invariant_findings(candidate)
        findings.extend(
            apply_rules(
                selected.enabled_rules,
                candidate,
                self.constitution,
                selected.epoch_id,
            )
        )
        verdict = self._aggregate(findings)
        dimensions: dict[str, Verdict] = {}
        for finding in findings:
            current = dimensions.get(finding.dimension, Verdict.ALLOW)
            dimensions[finding.dimension] = self._stronger(current, finding.verdict)
        claim_ceiling = self._claim_ceiling(candidate, findings, verdict)
        result = ExamResult(
            verdict_id=self.id_factory("verdict"),
            candidate_id=candidate.candidate_id,
            candidate_digest=candidate.digest,
            version_id=selected.version_id,
            version_digest=selected.digest,
            epoch_id=selected.epoch_id,
            verdict=verdict,
            findings=tuple(findings),
            dimension_verdicts=dimensions,
            created_at=self.clock(),
            claim_ceiling=claim_ceiling,
            nonce=candidate.submission_nonce,
        )
        if record:
            self.store.record_verdict(result)
        return result

    def _invariant_findings(self, candidate: CandidateChange) -> list[Finding]:
        findings: list[Finding] = []
        invalid_paths = self._invalid_paths(candidate)
        if invalid_paths:
            findings.append(
                Finding(
                    rule_id="C003_CANONICAL_PATHS",
                    dimension="input_integrity",
                    verdict=Verdict.BLOCK,
                    severity=Severity.CRITICAL,
                    reason="candidate contains absolute, traversing, or non-canonical paths",
                    evidence={"invalid_paths": invalid_paths},
                )
            )
        evidence_ids = [item.source_id for item in candidate.evidence]
        duplicate_evidence_ids = sorted(
            source_id for source_id in set(evidence_ids) if evidence_ids.count(source_id) > 1
        )
        if duplicate_evidence_ids:
            findings.append(
                Finding(
                    rule_id="C004_EVIDENCE_ID_UNIQUENESS",
                    dimension="evidence",
                    verdict=Verdict.BLOCK,
                    severity=Severity.CRITICAL,
                    reason="candidate contains duplicate evidence identifiers",
                    evidence={"duplicate_evidence_ids": duplicate_evidence_ids},
                )
            )
        malformed_verified = sorted(
            item.source_id
            for item in candidate.evidence
            if item.verification.value in {"DIRECT_VERIFIED", "EXTERNAL_VERIFIED"}
            and not (item.digest and re.fullmatch(r"[0-9a-fA-F]{64}", item.digest))
        )
        if malformed_verified:
            findings.append(
                Finding(
                    rule_id="C005_VERIFIED_DIGEST_FORMAT",
                    dimension="evidence",
                    verdict=Verdict.BLOCK,
                    severity=Severity.CRITICAL,
                    reason="verified evidence must carry a canonical SHA-256 digest",
                    evidence={"malformed_evidence_ids": malformed_verified},
                )
            )
        forbidden = self.constitution.forbidden_requested_actions(candidate)
        if forbidden:
            findings.append(
                Finding(
                    rule_id="C001_FORBIDDEN_ACTION",
                    dimension="governance",
                    verdict=Verdict.BLOCK,
                    severity=Severity.CRITICAL,
                    reason="candidate requests a constitutionally forbidden action",
                    evidence={"actions": forbidden},
                )
            )
        owner_gated = self.constitution.owner_gated_actions(candidate)
        approved_actions = set(candidate.metadata.get("owner_approved_actions", []))
        missing = sorted(action for action in owner_gated if action not in approved_actions)
        if missing:
            findings.append(
                Finding(
                    rule_id="C002_OWNER_GATE",
                    dimension="governance",
                    verdict=Verdict.ESCALATE,
                    severity=Severity.HIGH,
                    reason="candidate requests an owner-gated action without owner evidence",
                    evidence={"actions": missing},
                )
            )
        return findings

    @staticmethod
    def _invalid_paths(candidate: CandidateChange) -> list[str]:
        invalid: list[str] = []
        for raw in [
            *candidate.allowed_paths,
            *candidate.changed_paths,
            *candidate.deleted_paths,
        ]:
            if not raw or "\x00" in raw or "\\" in raw:
                invalid.append(raw)
                continue
            path = PurePosixPath(raw)
            if path.is_absolute() or ".." in path.parts or "." in path.parts:
                invalid.append(raw)
                continue
            canonical = path.as_posix()
            if canonical != raw or raw.startswith("/") or "//" in raw:
                invalid.append(raw)
        return sorted(set(invalid))

    @staticmethod
    def _stronger(left: Verdict, right: Verdict) -> Verdict:
        priority = {
            Verdict.ALLOW: 0,
            Verdict.UNKNOWN: 1,
            Verdict.ESCALATE: 2,
            Verdict.BLOCK: 3,
        }
        return right if priority[right] > priority[left] else left

    @classmethod
    def _aggregate(cls, findings: Iterable[Finding]) -> Verdict:
        items = list(findings)
        # Constitutional/hard failures dominate all aggregate utility. They can
        # never be averaged away by a large number of passing dimensions.
        if any(
            item.verdict == Verdict.BLOCK and item.severity == Severity.CRITICAL
            for item in items
        ):
            return Verdict.BLOCK
        verdict = Verdict.ALLOW
        for item in items:
            verdict = cls._stronger(verdict, item.verdict)
        return verdict

    @staticmethod
    def _claim_ceiling(
        candidate: CandidateChange, findings: list[Finding], verdict: Verdict
    ) -> str:
        if verdict == Verdict.BLOCK:
            return "NO_PROMOTION_OR_VALUE_CLAIM"
        if verdict == Verdict.ESCALATE:
            return "CANDIDATE_ONLY_OWNER_REVIEW_REQUIRED"
        if any(item.dimension == "external_effect" for item in findings):
            return "ENGINEERING_ONLY_EXTERNAL_EFFECT_UNVERIFIED"
        if any(
            claim.claim_type
            in {"external_effect", "social_value", "revenue_realization"}
            for claim in candidate.claims
        ):
            return "ENGINEERING_VALIDATED_EXTERNAL_EFFECT_REQUIRES_RECEIPT"
        return "ENGINEERING_CANDIDATE"

    def compare_versions(
        self,
        incumbent: ExaminerVersion,
        challenger: ExaminerVersion,
        holdout: AnchorBank,
        *,
        alpha: float = 0.05,
        minimum_accuracy_gain: float = 0.0,
    ) -> PromotionDecision:
        self._validate_version(incumbent)
        self._validate_version(challenger)
        if incumbent.anchor_manifest_digest != holdout.digest:
            raise RuntimeError("incumbent anchor manifest does not match sealed holdout")
        if challenger.anchor_manifest_digest != holdout.digest:
            raise RuntimeError("challenger anchor manifest does not match sealed holdout")
        incumbent_predictions = [
            self.evaluate(case.candidate, version=incumbent, record=False).verdict
            for case in holdout.cases
        ]
        challenger_predictions = [
            self.evaluate(case.candidate, version=challenger, record=False).verdict
            for case in holdout.cases
        ]
        incumbent_metrics = calculate_metrics(holdout.cases, incumbent_predictions)
        challenger_metrics = calculate_metrics(holdout.cases, challenger_predictions)
        wins = 0
        losses = 0
        ties = 0
        for case, old, new in zip(
            holdout.cases, incumbent_predictions, challenger_predictions, strict=True
        ):
            old_ok = old == case.expected
            new_ok = new == case.expected
            if new_ok and not old_ok:
                wins += 1
            elif old_ok and not new_ok:
                losses += 1
            else:
                ties += 1
        one_sided_p = one_sided_sign_test(wins, losses)
        reasons: list[str] = []
        if challenger_metrics.critical_false_passes > 0:
            reasons.append("CRITICAL_FALSE_PASS")
        if challenger_metrics.false_passes > incumbent_metrics.false_passes:
            reasons.append("FALSE_PASS_REGRESSION")
        if challenger_metrics.false_rejects > incumbent_metrics.false_rejects:
            reasons.append("FALSE_REJECT_REGRESSION")
        if challenger_metrics.accuracy < incumbent_metrics.accuracy + minimum_accuracy_gain:
            reasons.append("NO_ACCURACY_GAIN")
        if challenger_metrics.lower_bound < incumbent_metrics.lower_bound:
            reasons.append("LOWER_BOUND_REGRESSION")
        if one_sided_p > alpha:
            reasons.append("INSUFFICIENT_PAIRED_EVIDENCE")
        if challenger.constitution_digest != incumbent.constitution_digest:
            reasons.append("CONSTITUTION_CHANGED")
        if challenger.anchor_manifest_digest != incumbent.anchor_manifest_digest:
            reasons.append("ANCHOR_MANIFEST_CHANGED")
        eligible = not reasons and wins > losses
        return PromotionDecision(
            incumbent_version_id=incumbent.version_id,
            incumbent_version_digest=incumbent.digest,
            challenger_version_id=challenger.version_id,
            challenger_version_digest=challenger.digest,
            holdout_digest=holdout.digest,
            eligible=eligible,
            status="AWAITING_OWNER" if eligible else "REJECTED",
            reason_codes=tuple(reasons),
            incumbent_metrics=incumbent_metrics,
            challenger_metrics=challenger_metrics,
            wins=wins,
            losses=losses,
            ties=ties,
            one_sided_p=one_sided_p,
            alpha=alpha,
            minimum_accuracy_gain=minimum_accuracy_gain,
            requires_owner_approval=True,
        )

    def request_promotion(
        self, decision: PromotionDecision, *, promotion_id: str | None = None
    ) -> str:
        promotion_id = promotion_id or self.id_factory("promotion")
        self.store.record_promotion(
            promotion_id,
            decision,
            status=decision.status,
            created_at=self.clock(),
        )
        return promotion_id

    def apply_promotion(
        self,
        challenger: ExaminerVersion,
        decision: PromotionDecision,
        *,
        owner_actor: str,
        owner_approval_ref: str,
        promotion_id: str,
        holdout: AnchorBank,
    ) -> int:
        if not decision.eligible:
            raise PermissionError("ineligible challenger cannot be promoted")
        if not owner_actor or not owner_approval_ref:
            raise PermissionError("explicit owner actor and approval reference are required")
        if decision.incumbent_version_id != self.active_version.version_id:
            raise RuntimeError("promotion incumbent does not match active evaluator")
        if decision.challenger_version_id != challenger.version_id:
            raise RuntimeError("promotion challenger mismatch")
        self._validate_version(challenger)
        if decision.incumbent_version_digest != self.active_version.digest:
            raise RuntimeError("promotion incumbent digest mismatch")
        if decision.challenger_version_digest != challenger.digest:
            raise RuntimeError("promotion challenger digest mismatch")
        if decision.holdout_digest != holdout.digest:
            raise RuntimeError("promotion holdout digest mismatch")
        replay = self.compare_versions(
            self.active_version,
            challenger,
            holdout,
            alpha=decision.alpha,
            minimum_accuracy_gain=decision.minimum_accuracy_gain,
        )
        if replay.digest != decision.digest:
            raise RuntimeError("promotion decision failed deterministic replay")
        now = self.clock()
        stale_count = self.store.apply_promotion_atomic(
            promotion_id,
            decision,
            incumbent=self.active_version,
            challenger=challenger,
            applied_at=now,
            owner_actor=owner_actor,
            owner_approval_ref=owner_approval_ref,
        )
        self.active_version = challenger
        return stale_count

    def challenger(
        self,
        *,
        version_id: str,
        epoch_id: str,
        add_rules: Iterable[str],
        created_at: str | None = None,
    ) -> ExaminerVersion:
        enabled = tuple(dict.fromkeys([*self.active_version.enabled_rules, *add_rules]))
        return ExaminerVersion(
            version_id=version_id,
            epoch_id=epoch_id,
            constitution_digest=self.active_version.constitution_digest,
            anchor_manifest_digest=self.active_version.anchor_manifest_digest,
            implementation_digest=self.active_version.implementation_digest,
            enabled_rules=enabled,
            parent_version_id=self.active_version.version_id,
            created_at=created_at or self.clock(),
        )
