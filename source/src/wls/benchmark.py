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
        self._cases.append((case, runner))

    def run(self, *, stop_on_failure: bool = False) -> BenchmarkReport:
        from time import monotonic

        report = BenchmarkReport(
            report_id=new_id("bench"),
            suite_name=f"{self.name}{'_' + self.discriminator if self.discriminator else ''}",
        )
        for case, runner in self._cases:
            start = monotonic()
            try:
                output = runner(case.input_data)
                elapsed = monotonic() - start
                passed = bool(output.get("passed", True))
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
    """Compare two benchmark reports and detect regressions."""
    baseline_map = {r.case_id: r for r in baseline.results}
    current_map = {r.case_id: r for r in current.results}

    regressions: list[dict[str, Any]] = []
    improvements: list[dict[str, Any]] = []
    new_cases: list[str] = []
    removed_cases: list[str] = []

    for cid, cur in current_map.items():
        if cid not in baseline_map:
            new_cases.append(cid)
            continue
        bl = baseline_map[cid]
        delta = cur.success_rate_delta(bl)
        if delta < -max_regression:
            regressions.append({
                "case_id": cid,
                "baseline_passed": bl.passed,
                "current_passed": cur.passed,
                "delta": delta,
            })
        elif delta > 0:
            improvements.append({
                "case_id": cid,
                "baseline_passed": bl.passed,
                "current_passed": cur.passed,
                "delta": delta,
            })

    for cid in baseline_map:
        if cid not in current_map:
            removed_cases.append(cid)

    return {
        "baseline_name": baseline.suite_name,
        "current_name": current.suite_name,
        "baseline_rate": baseline.success_rate(),
        "current_rate": current.success_rate(),
        "rate_delta": current.success_rate() - baseline.success_rate(),
        "regressions": regressions,
        "improvements": improvements,
        "new_cases": new_cases,
        "removed_cases": removed_cases,
        "has_regression": len(regressions) > 0,
    }
