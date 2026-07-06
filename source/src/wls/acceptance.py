from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable, Iterable

from .schemas import digest_json, new_id, utc_now


class AcceptanceVerdict(StrEnum):
    PASS = "PASS"
    FAIL_CRITICAL = "FAIL_CRITICAL"
    FAIL_ADVISORY = "FAIL_ADVISORY"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"


@dataclass(slots=True)
class CheckResult:
    check_id: str
    name: str
    kind: str
    verdict: AcceptanceVerdict
    detail: str = ""
    evidence_digest: str = ""
    checked_at: str = field(default_factory=utc_now)


@dataclass(slots=True)
class AcceptanceReport:
    report_id: str
    target_id: str
    target_kind: str
    verdict: AcceptanceVerdict
    checks: list[CheckResult] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now)
    evidence_refs: list[str] = field(default_factory=list)

    def critical_failures(self) -> list[CheckResult]:
        return [c for c in self.checks if c.verdict == AcceptanceVerdict.FAIL_CRITICAL]

    def advisory_failures(self) -> list[CheckResult]:
        return [c for c in self.checks if c.verdict == AcceptanceVerdict.FAIL_ADVISORY]

    def all_pass(self) -> bool:
        return all(
            c.verdict in (AcceptanceVerdict.PASS, AcceptanceVerdict.FAIL_ADVISORY)
            for c in self.checks
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "target_id": self.target_id,
            "target_kind": self.target_kind,
            "verdict": self.verdict.value,
            "checks": [
                {
                    "check_id": c.check_id,
                    "name": c.name,
                    "kind": c.kind,
                    "verdict": c.verdict.value,
                    "detail": c.detail,
                    "evidence_digest": c.evidence_digest,
                    "checked_at": c.checked_at,
                }
                for c in self.checks
            ],
            "created_at": self.created_at,
            "evidence_refs": self.evidence_refs,
        }


class AcceptanceOracle:
    """Deterministic acceptance engine.

    Evaluates node/mission outputs using predetermined schemas, hashes,
    tests, metrics and artifact-specific checks. Never uses an LLM
    assertion as the sole evidence for a deterministic claim.
    """

    def __init__(self) -> None:
        self._checks: dict[str, Callable[[Any], CheckResult]] = {}

    def register(
        self, name: str, kind: str, check_fn: Callable[[Any], CheckResult]
    ) -> None:
        self._checks[name] = check_fn

    def evaluate(
        self,
        target_id: str,
        target_kind: str,
        context: dict[str, Any] | None = None,
    ) -> AcceptanceReport:
        report = AcceptanceReport(
            report_id=new_id("accept"),
            target_id=target_id,
            target_kind=target_kind,
            verdict=AcceptanceVerdict.PASS,
        )
        ctx = context or {}
        for name, check_fn in self._checks.items():
            try:
                result = check_fn(ctx)
            except Exception as exc:
                result = CheckResult(
                    check_id=new_id("check"),
                    name=name,
                    kind="oracle_error",
                    verdict=AcceptanceVerdict.BLOCKED,
                    detail=f"check raised: {exc}",
                )
            report.checks.append(result)
        criticals = [c for c in report.checks if c.verdict == AcceptanceVerdict.FAIL_CRITICAL]
        blocked = [c for c in report.checks if c.verdict == AcceptanceVerdict.BLOCKED]
        if blocked:
            report.verdict = AcceptanceVerdict.BLOCKED
        elif criticals:
            report.verdict = AcceptanceVerdict.FAIL_CRITICAL
        elif any(c.verdict == AcceptanceVerdict.FAIL_ADVISORY for c in report.checks):
            report.verdict = AcceptanceVerdict.FAIL_ADVISORY
        return report


def schema_check(
    value: Any,
    required_keys: Iterable[str],
    name: str = "schema_check",
) -> CheckResult:
    if not isinstance(value, dict):
        return CheckResult(
            check_id=new_id("check"),
            name=name,
            kind="schema",
            verdict=AcceptanceVerdict.FAIL_CRITICAL,
            detail="target is not a dict",
        )
    missing = [k for k in required_keys if k not in value]
    if missing:
        return CheckResult(
            check_id=new_id("check"),
            name=name,
            kind="schema",
            verdict=AcceptanceVerdict.FAIL_CRITICAL,
            detail=f"missing required keys: {missing}",
        )
    return CheckResult(
        check_id=new_id("check"),
        name=name,
        kind="schema",
        verdict=AcceptanceVerdict.PASS,
        evidence_digest=digest_json({"required_keys": sorted(required_keys)}),
    )


def hash_check(
    expected_digest: str,
    actual_data: Any,
    name: str = "hash_check",
) -> CheckResult:
    actual = digest_json(actual_data)
    passed = actual == expected_digest
    return CheckResult(
        check_id=new_id("check"),
        name=name,
        kind="hash",
        verdict=AcceptanceVerdict.PASS if passed else AcceptanceVerdict.FAIL_CRITICAL,
        detail=f"expected={expected_digest[:16]}... actual={actual[:16]}..." if not passed else "digest matches",
        evidence_digest=actual,
    )


def threshold_check(
    metric_name: str,
    value: float,
    threshold: float,
    operator: str = "le",
    name: str = "threshold_check",
) -> CheckResult:
    ops: dict[str, Callable[[float, float], bool]] = {
        "lt": lambda v, t: v < t,
        "le": lambda v, t: v <= t,
        "gt": lambda v, t: v > t,
        "ge": lambda v, t: v >= t,
        "eq": lambda v, t: v == t,
    }
    op_fn = ops.get(operator)
    if op_fn is None:
        return CheckResult(
            check_id=new_id("check"),
            name=name,
            kind="threshold",
            verdict=AcceptanceVerdict.BLOCKED,
            detail=f"unknown operator: {operator}",
        )
    passed = op_fn(value, threshold)
    return CheckResult(
        check_id=new_id("check"),
        name=name,
        kind="threshold",
        verdict=AcceptanceVerdict.PASS if passed else AcceptanceVerdict.FAIL_CRITICAL,
        detail=f"{metric_name}={value} vs threshold={threshold} op={operator}",
        evidence_digest=digest_json(
            {"metric": metric_name, "value": value, "threshold": threshold, "op": operator}
        ),
    )


def regression_check(
    current: float,
    baseline: float,
    max_regression: float = 0.0,
    name: str = "regression_check",
) -> CheckResult:
    delta = current - baseline
    passed = delta >= -max_regression
    return CheckResult(
        check_id=new_id("check"),
        name=name,
        kind="regression",
        verdict=AcceptanceVerdict.PASS if passed else AcceptanceVerdict.FAIL_CRITICAL,
        detail=f"current={current} baseline={baseline} delta={delta} max_regression={max_regression}",
        evidence_digest=digest_json(
            {"current": current, "baseline": baseline, "delta": delta}
        ),
    )


def path_existence_check(
    paths: Iterable[str],
    name: str = "path_existence_check",
) -> CheckResult:
    from pathlib import Path

    missing = [p for p in paths if not Path(p).exists()]
    if missing:
        return CheckResult(
            check_id=new_id("check"),
            name=name,
            kind="path",
            verdict=AcceptanceVerdict.FAIL_CRITICAL,
            detail=f"missing paths: {missing}",
        )
    return CheckResult(
        check_id=new_id("check"),
        name=name,
        kind="path",
        verdict=AcceptanceVerdict.PASS,
        evidence_digest=digest_json({"paths": sorted(paths)}),
    )


@dataclass(slots=True)
class MissionAcceptance:
    oracle: AcceptanceOracle
    mission_id: str
    node_results: dict[str, AcceptanceReport] = field(default_factory=dict)

    def add_node_result(self, node_id: str, report: AcceptanceReport) -> None:
        self.node_results[node_id] = report

    def aggregate(self) -> AcceptanceReport:
        report = AcceptanceReport(
            report_id=new_id("mission_accept"),
            target_id=self.mission_id,
            target_kind="mission",
            verdict=AcceptanceVerdict.PASS,
        )
        for node_id, node_report in self.node_results.items():
            report.checks.extend(node_report.checks)
            report.evidence_refs.append(node_report.report_id)
        criticals = [c for c in report.checks if c.verdict == AcceptanceVerdict.FAIL_CRITICAL]
        blocked = [c for c in report.checks if c.verdict == AcceptanceVerdict.BLOCKED]
        if blocked:
            report.verdict = AcceptanceVerdict.BLOCKED
        elif criticals:
            report.verdict = AcceptanceVerdict.FAIL_CRITICAL
        elif any(c.verdict == AcceptanceVerdict.FAIL_ADVISORY for c in report.checks):
            report.verdict = AcceptanceVerdict.FAIL_ADVISORY
        return report
