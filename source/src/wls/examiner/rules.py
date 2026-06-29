from __future__ import annotations

from pathlib import PurePosixPath
from typing import Callable, Iterable

from .constitution import Constitution, path_allowed
from .models import (
    CandidateChange,
    EvidenceLevel,
    Finding,
    Severity,
    Verdict,
    evidence_index,
)

RuleFn = Callable[[CandidateChange, Constitution, str], list[Finding]]


def _finding(
    rule_id: str,
    dimension: str,
    reason: str,
    *,
    severity: Severity = Severity.CRITICAL,
    verdict: Verdict = Verdict.BLOCK,
    evidence: dict | None = None,
) -> Finding:
    return Finding(
        rule_id=rule_id,
        dimension=dimension,
        verdict=verdict,
        severity=severity,
        reason=reason,
        evidence=evidence or {},
    )


def rule_scope_integrity(
    candidate: CandidateChange, constitution: Constitution, epoch_id: str
) -> list[Finding]:
    outside = sorted(
        path
        for path in [*candidate.changed_paths, *candidate.deleted_paths]
        if not path_allowed(path, candidate.allowed_paths)
    )
    if outside or bool(candidate.metadata.get("scope_expansion")):
        return [
            _finding(
                "R001_SCOPE_INTEGRITY",
                "scope",
                "candidate changes exceed the explicitly authorized task surface",
                evidence={"outside_allowed_paths": outside},
            )
        ]
    if (
        len(candidate.changed_paths) + len(candidate.deleted_paths)
        > constitution.max_changed_files_without_explicit_scope
        and not bool(candidate.metadata.get("explicit_large_scope_approval"))
    ):
        return [
            _finding(
                "R001_SCOPE_INTEGRITY",
                "scope",
                "large change set lacks explicit large-scope approval",
                severity=Severity.HIGH,
                evidence={
                    "changed_count": len(candidate.changed_paths),
                    "deleted_count": len(candidate.deleted_paths),
                },
            )
        ]
    return []


def rule_gate_weakening(
    candidate: CandidateChange, constitution: Constitution, epoch_id: str
) -> list[Finding]:
    deleted_tests = sorted(
        path
        for path in candidate.deleted_paths
        if "/test" in path.lower() or path.lower().startswith("tests/")
    )
    threshold_delta = float(candidate.metadata.get("acceptance_threshold_delta", 0.0))
    if deleted_tests or bool(candidate.metadata.get("tests_weakened")) or threshold_delta < 0:
        return [
            _finding(
                "R002_GATE_WEAKENING",
                "verification",
                "candidate weakens or removes an existing acceptance gate",
                evidence={
                    "deleted_tests": deleted_tests,
                    "acceptance_threshold_delta": threshold_delta,
                },
            )
        ]
    return []


def rule_claim_ceiling(
    candidate: CandidateChange, constitution: Constitution, epoch_id: str
) -> list[Finding]:
    refs = evidence_index(candidate.evidence)
    findings: list[Finding] = []
    high_claim_types = {
        "external_effect",
        "production_maturity",
        "social_value",
        "revenue_realization",
        "owner_host_proof",
    }
    for claim in candidate.claims:
        attached = [refs[item] for item in claim.evidence_ids if item in refs]
        strong = [
            ref
            for ref in attached
            if ref.verification
            in {EvidenceLevel.DIRECT_VERIFIED, EvidenceLevel.EXTERNAL_VERIFIED}
            and ref.digest
        ]
        if claim.level in {
            EvidenceLevel.DIRECT_VERIFIED,
            EvidenceLevel.EXTERNAL_VERIFIED,
        } and not strong:
            findings.append(
                _finding(
                    "R003_CLAIM_CEILING",
                    "evidence",
                    "verified claim has no digest-bound verified evidence",
                    evidence={"claim": claim.text, "claim_type": claim.claim_type},
                )
            )
        if claim.claim_type in high_claim_types and not any(
            ref.independent and ref.verification == EvidenceLevel.EXTERNAL_VERIFIED
            for ref in attached
        ):
            findings.append(
                _finding(
                    "R003_CLAIM_CEILING",
                    "external_effect",
                    "external-effect claim lacks independent external verification",
                    severity=Severity.HIGH,
                    evidence={"claim": claim.text, "claim_type": claim.claim_type},
                )
            )
    return findings


def rule_artifact_hygiene(
    candidate: CandidateChange, constitution: Constitution, epoch_id: str
) -> list[Finding]:
    suspicious: list[str] = []
    for raw in [*candidate.changed_paths, *candidate.deleted_paths]:
        path = str(PurePosixPath(raw.replace("\\", "/"))).lower()
        name = PurePosixPath(path).name
        if (
            "__pycache__" in path
            or "/soak_home/" in f"/{path}/"
            or name in {"runtime.lock", "daemon.lock"}
            or name.endswith((".key", ".pem", ".p12", ".db", ".sqlite", ".sqlite3"))
            or name in {".env", "id_rsa"}
        ):
            suspicious.append(raw)
    if suspicious or bool(candidate.metadata.get("contains_secret_material")):
        return [
            _finding(
                "R004_ARTIFACT_HYGIENE",
                "security",
                "submission contains generated runtime state or secret-bearing artifacts",
                evidence={"suspicious_paths": sorted(suspicious)},
            )
        ]
    return []


def rule_authority_uniqueness(
    candidate: CandidateChange, constitution: Constitution, epoch_id: str
) -> list[Finding]:
    authorities = [str(item) for item in candidate.metadata.get("new_authorities", [])]
    forbidden = {
        "runtime",
        "planner",
        "memory",
        "goal_store",
        "world_model",
        "policy",
        "database",
        "action_executor",
    }
    duplicates = sorted(item for item in authorities if item.lower() in forbidden)
    if duplicates or bool(candidate.metadata.get("second_runtime")):
        return [
            _finding(
                "R005_AUTHORITY_UNIQUENESS",
                "architecture",
                "candidate creates a duplicate canonical authority",
                evidence={"duplicate_authorities": duplicates},
            )
        ]
    return []


def rule_evidence_laundering(
    candidate: CandidateChange, constitution: Constitution, epoch_id: str
) -> list[Finding]:
    bad_refs = [
        ref.source_id
        for ref in candidate.evidence
        if ref.source_kind in {"model_output", "self_report", "generated_summary"}
        and ref.verification
        in {EvidenceLevel.DIRECT_VERIFIED, EvidenceLevel.EXTERNAL_VERIFIED}
    ]
    self_only = bool(candidate.evidence) and not any(ref.independent for ref in candidate.evidence)
    claims_verified = any(
        claim.level in {EvidenceLevel.DIRECT_VERIFIED, EvidenceLevel.EXTERNAL_VERIFIED}
        for claim in candidate.claims
    )
    if bad_refs or (claims_verified and self_only):
        return [
            _finding(
                "R006_EVIDENCE_LAUNDERING",
                "evidence",
                "model or self-generated assertions were promoted to verified evidence",
                evidence={"invalid_evidence_ids": sorted(bad_refs), "self_only": self_only},
            )
        ]
    return []


def rule_incremental_value_and_cost(
    candidate: CandidateChange, constitution: Constitution, epoch_id: str
) -> list[Finding]:
    incremental = bool(candidate.metadata.get("changes_decision_or_state", False))
    reusable_asset = bool(candidate.metadata.get("creates_reusable_asset", False))
    maintenance = float(candidate.metrics.get("maintenance_hours_month", 0.0))
    execution = float(candidate.metrics.get("execution_hours", 0.0))
    files = len(candidate.changed_paths) + len(candidate.deleted_paths)
    high_burden = maintenance >= 8.0 or execution >= 16.0 or files >= 25
    if high_burden and not (incremental or reusable_asset):
        return [
            _finding(
                "R007_INCREMENTAL_VALUE",
                "value",
                "high-cost candidate has no demonstrated decision, state, or reusable-asset gain",
                severity=Severity.HIGH,
                evidence={
                    "maintenance_hours_month": maintenance,
                    "execution_hours": execution,
                    "file_count": files,
                },
            )
        ]
    return []


def rule_epoch_freshness(
    candidate: CandidateChange, constitution: Constitution, epoch_id: str
) -> list[Finding]:
    score_epoch = candidate.metadata.get("score_epoch_id")
    stale = (
        candidate.submitted_epoch_id is not None
        and candidate.submitted_epoch_id != epoch_id
    ) or (score_epoch is not None and str(score_epoch) != epoch_id)
    if stale:
        return [
            _finding(
                "R008_EPOCH_FRESHNESS",
                "evaluation_integrity",
                "candidate relies on a verdict or score produced under another evaluator epoch",
                evidence={
                    "current_epoch": epoch_id,
                    "submitted_epoch": candidate.submitted_epoch_id,
                    "score_epoch": score_epoch,
                },
            )
        ]
    return []


def rule_anchor_and_constitution_tamper(
    candidate: CandidateChange, constitution: Constitution, epoch_id: str
) -> list[Finding]:
    touched = constitution.protected_touches(candidate)
    override = bool(candidate.metadata.get("anchor_digest_override")) or bool(
        candidate.metadata.get("constitution_override")
    )
    if touched or override:
        return [
            _finding(
                "R009_PROTECTED_SURFACE",
                "governance",
                "candidate attempts to modify protected constitution, anchor, or examiner state",
                evidence={"protected_paths": touched, "override_attempt": override},
            )
        ]
    return []


def rule_judge_diversity(
    candidate: CandidateChange, constitution: Constitution, epoch_id: str
) -> list[Finding]:
    if not bool(candidate.metadata.get("requires_diverse_judges", False)):
        return []
    families = {
        ref.family
        for ref in candidate.evidence
        if ref.family and ref.source_kind in {"model_output", "model_judge"}
    }
    deterministic = any(
        ref.source_kind in {"test", "workflow", "database", "owner_receipt", "external_usage"}
        and ref.verification
        in {EvidenceLevel.DIRECT_VERIFIED, EvidenceLevel.EXTERNAL_VERIFIED}
        and ref.digest
        for ref in candidate.evidence
    )
    independent_external = any(
        ref.independent and ref.verification == EvidenceLevel.EXTERNAL_VERIFIED
        for ref in candidate.evidence
    )
    if len(families) < 2 and not deterministic and not independent_external:
        return [
            _finding(
                "R010_JUDGE_DIVERSITY",
                "evaluation_integrity",
                "critical judgment is supported only by one correlated model family",
                severity=Severity.HIGH,
                verdict=Verdict.ESCALATE,
                evidence={"judge_families": sorted(item for item in families if item)},
            )
        ]
    return []


RULES: dict[str, RuleFn] = {
    "R001_SCOPE_INTEGRITY": rule_scope_integrity,
    "R002_GATE_WEAKENING": rule_gate_weakening,
    "R003_CLAIM_CEILING": rule_claim_ceiling,
    "R004_ARTIFACT_HYGIENE": rule_artifact_hygiene,
    "R005_AUTHORITY_UNIQUENESS": rule_authority_uniqueness,
    "R006_EVIDENCE_LAUNDERING": rule_evidence_laundering,
    "R007_INCREMENTAL_VALUE": rule_incremental_value_and_cost,
    "R008_EPOCH_FRESHNESS": rule_epoch_freshness,
    "R009_PROTECTED_SURFACE": rule_anchor_and_constitution_tamper,
    "R010_JUDGE_DIVERSITY": rule_judge_diversity,
}


def apply_rules(
    enabled_rules: Iterable[str],
    candidate: CandidateChange,
    constitution: Constitution,
    epoch_id: str,
) -> list[Finding]:
    findings: list[Finding] = []
    for rule_id in enabled_rules:
        try:
            rule = RULES[rule_id]
        except KeyError as exc:
            raise ValueError(f"unknown examiner rule: {rule_id}") from exc
        findings.extend(rule(candidate, constitution, epoch_id))
    return findings
