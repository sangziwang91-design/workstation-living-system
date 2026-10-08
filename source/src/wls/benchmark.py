from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .schemas import digest_json, new_id, utc_now


@dataclass(slots=True)
class BenchmarkCase:
    case_id: str
    name: str
    category: str
    input_data: dict[str, Any]
    expected_success: bool = True
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "name": self.name,
            "category": self.category,
            "expected_success": self.expected_success,
            "tags": self.tags,
            "input_digest": digest_json(self.input_data),
        }


@dataclass(slots=True)
class BenchmarkResult:
    case_id: str
    name: str
    category: str
    passed: bool
    elapsed_seconds: float
    error: str = ""
    metrics: dict[str, float] = field(default_factory=dict)
    evidence_digests: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "name": self.name,
            "category": self.category,
            "passed": self.passed,
            "elapsed_seconds": self.elapsed_seconds,
            "error": self.error,
            "metrics": self.metrics,
            "evidence_digests": self.evidence_digests,
        }

    def success_rate_delta(self, other: BenchmarkResult) -> float:
        return float(self.passed) - float(other.passed)


@dataclass(slots=True)
class BenchmarkReport:
    report_id: str
    suite_name: str
    results: list[BenchmarkResult] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now)
    suite_digest: str = ""  # Frozen task set; not proof of evaluator independence.

    def total_cases(self) -> int:
        return len(self.results)

    def passed_cases(self) -> int:
        return sum(1 for r in self.results if r.passed)

    def failed_cases(self) -> int:
        return sum(1 for r in self.results if not r.passed)

    def success_rate(self) -> float:
        total = self.total_cases()
        return self.passed_cases() / total if total > 0 else 0.0

    def by_category(self) -> dict[str, list[BenchmarkResult]]:
        groups: dict[str, list[BenchmarkResult]] = {}
        for r in self.results:
            groups.setdefault(r.category, []).append(r)
        return groups

    def category_summary(self) -> dict[str, dict[str, float]]:
        summary: dict[str, dict[str, float]] = {}
        for cat, results in self.by_category().items():
            passed = sum(1 for r in results if r.passed)
            summary[cat] = {
                "total": len(results),
                "passed": passed,
                "rate": passed / len(results) if results else 0.0,
            }
        return summary

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "suite_name": self.suite_name,
            "suite_digest": self.suite_digest,
            "total": self.total_cases(),
            "passed": self.passed_cases(),
            "failed": self.failed_cases(),
            "success_rate": self.success_rate(),
            "categories": self.category_summary(),
            "results": [r.to_dict() for r in self.results],
            "created_at": self.created_at,
        }


class BenchmarkSuite:
    """Deterministic benchmark runner for comparing WLS capabilities against
    frozen baselines. Results include elapsed time, pass/fail, metrics, and
    evidence digests.
    """

    def __init__(self, name: str, *, discriminator: str = "") -> None:
        self.name = name
        self.discriminator = discriminator
        self._cases: list[tuple[BenchmarkCase, Callable[[dict[str, Any]], dict[str, Any]]]] = []

    def add(
        self,
        case: BenchmarkCase,
        runner: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> None:
        if not isinstance(case.case_id, str) or not case.case_id.strip():
            raise ValueError("benchmark case_id must be nonempty")
        if any(existing.case_id == case.case_id for existing, _ in self._cases):
            raise ValueError("duplicate benchmark case_id")
        if not isinstance(case.expected_success, bool):
            raise ValueError("expected_success must be boolean")
        self._cases.append((case, runner))

    def run(self, *, stop_on_failure: bool = False) -> BenchmarkReport:
        from time import monotonic

        report = BenchmarkReport(
            report_id=new_id("bench"),
            suite_name=f"{self.name}{'_' + self.discriminator if self.discriminator else ''}",
            suite_digest=digest_json({
                "name": self.name,
                "discriminator": self.discriminator,
                "cases": [case.to_dict() for case, _ in self._cases],
            }),
        )
        for case, runner in self._cases:
            start = monotonic()
            try:
                output = runner(case.input_data)
                elapsed = monotonic() - start
                if not isinstance(output, dict) or type(output.get("passed")) is not bool:
                    raise ValueError("benchmark runner must return an explicit boolean passed")
                passed = output["passed"]
                result = BenchmarkResult(
                    case_id=case.case_id,
                    name=case.name,
                    category=case.category,
                    passed=passed,
                    elapsed_seconds=round(elapsed, 4),
                    metrics=output.get("metrics", {}),
                    evidence_digests=[
                        digest_json({"case": case.case_id, "output_hash": digest_json(output)})
                    ],
                )
            except Exception as exc:
                elapsed = monotonic() - start
                result = BenchmarkResult(
                    case_id=case.case_id,
                    name=case.name,
                    category=case.category,
                    passed=False,
                    elapsed_seconds=round(elapsed, 4),
                    error=str(exc),
                )
            report.results.append(result)
            if stop_on_failure and not result.passed:
                break
        return report


def compare_reports(
    baseline: BenchmarkReport, current: BenchmarkReport, *, max_regression: float = 0.0
) -> dict[str, Any]:
    """Matched-case benchmark comparison; never promote by dropping hard tasks.

    Old reports without a frozen case digest remain readable, but cannot
    establish an improvement claim. A signed external evaluation is still
    required before a live WLS promotion.
    """
    if not 0 <= max_regression <= 1:
        raise ValueError("max_regression must be in [0, 1]")
    bl_ids = [r.case_id for r in baseline.results]
    cur_ids = [r.case_id for r in current.results]
    bl_map = {r.case_id: r for r in baseline.results}
    cur_map = {r.case_id: r for r in current.results}
    duplicate_ids = sorted({
        cid for ids in (bl_ids, cur_ids) for cid in ids if ids.count(cid) > 1
    })
    new_cases = sorted(set(cur_map) - set(bl_map))
    removed_cases = sorted(set(bl_map) - set(cur_map))
    changed_cases = sorted(
        cid for cid in set(bl_map) & set(cur_map)
        if (bl_map[cid].name, bl_map[cid].category)
        != (cur_map[cid].name, cur_map[cid].category)
    )
    comparable = bool(
        bl_ids and cur_ids and baseline.suite_digest
        and baseline.suite_digest == current.suite_digest
        and not duplicate_ids and not new_cases and not removed_cases
        and not changed_cases
    )
    regressions: list[dict[str, Any]] = []
    improvements: list[dict[str, Any]] = []
    for cid in sorted(set(bl_map) & set(cur_map)):
        bl, cur = bl_map[cid], cur_map[cid]
        delta = cur.success_rate_delta(bl)
        if delta < -max_regression:
            regressions.append({
                "case_id": cid, "baseline_passed": bl.passed,
                "current_passed": cur.passed, "delta": delta,
            })
        elif delta > 0:
            improvements.append({
                "case_id": cid, "baseline_passed": bl.passed,
                "current_passed": cur.passed, "delta": delta,
            })
    raw_rate_delta = current.success_rate() - baseline.success_rate()
    return {
        "baseline_name": baseline.suite_name,
        "current_name": current.suite_name,
        "baseline_rate": baseline.success_rate(),
        "current_rate": current.success_rate(),
        "rate_delta": raw_rate_delta,
        "matched_rate_delta": raw_rate_delta if comparable else None,
        "regressions": regressions,
        "improvements": improvements,
        "new_cases": new_cases,
        "removed_cases": removed_cases,
        "changed_cases": changed_cases,
        "duplicate_case_ids": duplicate_ids,
        "measurement_valid": comparable,
        "has_regression": bool(regressions),
        "eligible_for_promotion": bool(
            comparable and raw_rate_delta > 0 and not regressions
        ),
        "authority": "candidate_only",
    }


def paired_rsi_metric(
    baseline: BenchmarkReport,
    candidate: BenchmarkReport,
    *,
    evaluator_digest: str,
) -> "MetricResult":
    """Feed independently run, identical task results to the existing RSI pilot."""
    from .experiment_decision import MetricResult

    comparison = compare_reports(baseline, candidate)
    if not comparison["measurement_valid"]:
        raise ValueError("RSI measurement invalid: changed, missing or duplicate cases")
    return MetricResult(
        primary=candidate.success_rate(),
        gates={"critical_regressions": float(len(comparison["regressions"]))},
        evaluator_digest=evaluator_digest,
    )
