from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .schemas import RiskLevel, digest_json, new_id, utc_now


@dataclass(slots=True)
class SecurityFinding:
    finding_id: str
    rule: str
    severity: str
    detail: str
    evidence_digest: str = ""
    found_at: str = field(default_factory=utc_now)


@dataclass(slots=True)
class SecurityAudit:
    audit_id: str
    target: str
    findings: list[SecurityFinding] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now)

    def critical_findings(self) -> list[SecurityFinding]:
        return [f for f in self.findings if f.severity == "critical"]

    def has_critical(self) -> bool:
        return any(f.severity == "critical" for f in self.findings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "audit_id": self.audit_id,
            "target": self.target,
            "findings": [
                {
                    "finding_id": f.finding_id,
                    "rule": f.rule,
                    "severity": f.severity,
                    "detail": f.detail,
                    "evidence_digest": f.evidence_digest,
                    "found_at": f.found_at,
                }
                for f in self.findings
            ],
            "created_at": self.created_at,
        }


class SecurityFirewall:
    """Deterministic security gate applied at boundaries: provider, channel,
    tool, file, and action surface.

    All checks execute locally; no LLM assertion is treated as a security
    verdict. A critical finding blocks execution.
    """

    def __init__(self) -> None:
        self._rules: dict[str, Callable[[dict[str, Any]], SecurityFinding | None]] = {}

    def register_rule(
        self, name: str, check_fn: Callable[[dict[str, Any]], SecurityFinding | None]
    ) -> None:
        self._rules[name] = check_fn

    def audit(self, target: str, context: dict[str, Any] | None = None) -> SecurityAudit:
        audit = SecurityAudit(audit_id=new_id("sec"), target=target)
        ctx = context or {}
        for rule_name, check_fn in self._rules.items():
            try:
                finding = check_fn(ctx)
            except Exception as exc:
                finding = SecurityFinding(
                    finding_id=new_id("secf"),
                    rule=rule_name,
                    severity="critical",
                    detail=f"rule raised: {exc}",
                )
            if finding is not None:
                audit.findings.append(finding)
        return audit


def path_escape_check(allowed_roots: list[str]) -> Callable[[dict[str, Any]], SecurityFinding | None]:
    def _check(ctx: dict[str, Any]) -> SecurityFinding | None:
        target = ctx.get("resolved_path", "")
        if not target:
            return SecurityFinding(
                finding_id=new_id("secf"),
                rule="path_escape",
                severity="warning",
                detail="no resolved_path in context",
            )
        tp = Path(target).resolve()
        for root in allowed_roots:
            rp = Path(root).resolve()
            try:
                tp.relative_to(rp)
                return None
            except ValueError:
                continue
        return SecurityFinding(
            finding_id=new_id("secf"),
            rule="path_escape",
            severity="critical",
            detail=f"path {target} is outside allowed roots: {allowed_roots}",
            evidence_digest=digest_json({"path": target, "roots": allowed_roots}),
        )
    return _check


def host_allowlist_check(allowed: list[str]) -> Callable[[dict[str, Any]], SecurityFinding | None]:
    def _check(ctx: dict[str, Any]) -> SecurityFinding | None:
        host = ctx.get("host", "")
        if not host:
            return None
        if host in allowed:
            return None
        return SecurityFinding(
            finding_id=new_id("secf"),
            rule="host_allowlist",
            severity="critical",
            detail=f"host {host} not in allowlist: {allowed}",
        )
    return _check


def credential_leak_check(value: str, field_name: str = "value") -> SecurityFinding | None:
    patterns = [
        "sk-",
        "api_key",
        "Bearer ",
        "ghp_",
        "gho_",
        "ghu_",
        "ghs_",
        "github_pat_",
        "-----BEGIN",
        "PRIVATE KEY",
    ]
    for pattern in patterns:
        if pattern in value:
            return SecurityFinding(
                finding_id=new_id("secf"),
                rule="credential_leak",
                severity="critical",
                detail=f"potential credential pattern in {field_name}",
                evidence_digest=digest_json({"pattern": pattern, "field": field_name}),
            )
    return None


def risk_ceiling_check(
    requested: str, allowed_ceiling: str = "READ"
) -> SecurityFinding | None:
    order = {RiskLevel.READ: 0, RiskLevel.REVERSIBLE_WRITE: 1, RiskLevel.HIGH: 2, RiskLevel.IRREVERSIBLE: 3}
    try:
        req_level = order[RiskLevel(requested)]
    except (ValueError, KeyError):
        return SecurityFinding(
            finding_id=new_id("secf"),
            rule="risk_ceiling",
            severity="critical",
            detail=f"unknown risk level: {requested}",
        )
    allow_level = order[RiskLevel(allowed_ceiling)]
    if req_level > allow_level:
        return SecurityFinding(
            finding_id=new_id("secf"),
            rule="risk_ceiling",
            severity="critical",
            detail=f"requested risk {requested} exceeds ceiling {allowed_ceiling}",
        )
    return None


def command_injection_check(command: str) -> SecurityFinding | None:
    dangerous = [";", "&&", "|", "`", "$(", "rm -rf", "del /", "format "]
    lower = command.lower()
    for pattern in dangerous:
        if pattern in lower:
            return SecurityFinding(
                finding_id=new_id("secf"),
                rule="command_injection",
                severity="critical",
                detail=f"suspected injection pattern: {pattern}",
                evidence_digest=digest_json({"pattern": pattern, "command": command}),
            )
    return None


def prompt_injection_check(text: str) -> SecurityFinding | None:
    triggers = [
        "ignore previous instructions",
        "ignore all previous",
        "system:",
        "you are now",
        "new instructions:",
    ]
    lower = text.lower()
    for trigger in triggers:
        if trigger in lower:
            return SecurityFinding(
                finding_id=new_id("secf"),
                rule="prompt_injection",
                severity="critical",
                detail=f"suspected prompt injection: {trigger}",
                evidence_digest=digest_json({"trigger": trigger}),
            )
    return None
