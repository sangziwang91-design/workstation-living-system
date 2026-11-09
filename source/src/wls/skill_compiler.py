from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .schemas import RiskLevel, digest_json, new_id, utc_now


@dataclass(slots=True)
class SkillPackage:
    skill_id: str
    name: str
    version: int
    description: str
    applicability: list[str]
    tool_permissions: list[str]
    risk_ceiling: str = RiskLevel.READ.value
    baseline_fixed: bool = False
    status: str = "PROPOSED"
    rollback_target: str | None = None
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "applicability": self.applicability,
            "tool_permissions": self.tool_permissions,
            "risk_ceiling": self.risk_ceiling,
            "baseline_fixed": self.baseline_fixed,
            "status": self.status,
        }


@dataclass(slots=True)
class CompiledSkill:
    package: SkillPackage
    body_checksum: str
    test_manifest: list[str]
    acceptance_evidence: list[str]
    # Failure receipts remain available; they must never count as validation.
    validation_results: dict[str, bool] = field(default_factory=dict)

    def is_validated(self) -> bool:
        required = self.test_manifest
        observed = self.validation_results
        return (
            bool(required)
            and len(set(required)) == len(required)
            and set(observed) == set(required)
            and all(type(observed[name]) is bool and observed[name] for name in required)
            and len(self.acceptance_evidence) == len(required)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.package.to_dict(),
            "body_checksum": self.body_checksum,
            "test_manifest": self.test_manifest,
            "acceptance_evidence": self.acceptance_evidence,
            "validation_results": dict(self.validation_results),
            "validated": self.is_validated(),
        }


class SkillCompiler:
    """Packages WLS procedures as reusable skill bundles while preserving
    the existing growth lifecycle. Generated skills remain PROPOSED until
    sandbox validation, owner approval, and reuse.
    """

    def propose(
        self,
        name: str,
        description: str,
        *,
        applicability: list[str] | None = None,
        permissions: list[str] | None = None,
        risk: str = RiskLevel.READ.value,
        body: dict[str, Any] | None = None,
    ) -> SkillPackage:
        return SkillPackage(
            skill_id=new_id("skill"),
            name=name,
            version=1,
            description=description,
            applicability=list(applicability or []),
            tool_permissions=list(permissions or []),
            risk_ceiling=risk,
            baseline_fixed=True,
            status="PROPOSED",
        )

    def compile(
        self,
        package: SkillPackage,
        body: dict[str, Any],
        tests: list[str] | None = None,
    ) -> CompiledSkill:
        body_digest = digest_json(body)
        return CompiledSkill(
            package=package,
            body_checksum=body_digest,
            test_manifest=list(tests or []),
            acceptance_evidence=[],
        )

    def validate(
        self,
        compiled: CompiledSkill,
        test_results: dict[str, bool],
    ) -> CompiledSkill:
        required = compiled.test_manifest
        if (not isinstance(test_results, dict)
                or not required
                or len(set(required)) != len(required)
                or any(not isinstance(name, str) or not name for name in required)
                or set(test_results) != set(required)
                or any(type(passed) is not bool for passed in test_results.values())):
            raise ValueError("skill validation requires exact frozen boolean test results")
        # Both PASS and FAIL are recorded. Only complete all-PASS is validated.
        observed = {name: test_results[name] for name in required}
        evidence = [
            digest_json({"test": name, "passed": observed[name]}) for name in required
        ]
        return CompiledSkill(
            package=compiled.package,
            body_checksum=compiled.body_checksum,
            test_manifest=list(required),
            acceptance_evidence=evidence,
            validation_results=observed,
        )

    def reject(
        self,
        compiled: CompiledSkill,
        reason: str,
    ) -> SkillPackage:
        pkg = compiled.package
        return SkillPackage(
            skill_id=pkg.skill_id,
            name=pkg.name,
            version=pkg.version,
            description=f"REJECTED: {reason}",
            applicability=[],
            tool_permissions=[],
            risk_ceiling=pkg.risk_ceiling,
            baseline_fixed=False,
            status="REJECTED",
            rollback_target=pkg.skill_id,
        )
