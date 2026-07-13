from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import mean
from typing import Any


DEFAULT_PERFORMANCE_BUDGETS_SECONDS = {
    "health_snapshot": 0.75,
    "runtime_init": 1.5,
    "garbage_audit": 5.0,
    "self_check": 10.0,
    "ui_api_health": 1.0,
    "ui_api_bootstrap": 2.0,
    "ui_api_product": 15.0,
    "ui_api_garbage_scan": 5.0,
    "ui_api_garbage_cleanup": 5.0,
    "ui_api_garbage_clear": 5.0,
    "rollback_disposable_clone": 30.0,
}


@dataclass(slots=True)
class PerformanceMeasurement:
    name: str
    samples_seconds: list[float]
    budget_seconds: float

    def summary(self) -> dict[str, Any]:
        samples = [float(item) for item in self.samples_seconds]
        observed_max = max(samples) if samples else 0.0
        observed_mean = mean(samples) if samples else 0.0
        return {
            "name": self.name,
            "samples_seconds": [round(item, 4) for item in samples],
            "mean_seconds": round(observed_mean, 4),
            "max_seconds": round(observed_max, 4),
            "budget_seconds": float(self.budget_seconds),
            "passed": observed_max <= float(self.budget_seconds),
        }

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_performance_budget(
    measurements: list[PerformanceMeasurement],
) -> dict[str, Any]:
    results = [measurement.summary() for measurement in measurements]
    failures = [
        {
            "name": result["name"],
            "max_seconds": result["max_seconds"],
            "budget_seconds": result["budget_seconds"],
        }
        for result in results
        if result["passed"] is not True
    ]
    return {
        "status": "PASSED" if not failures else "FAILED",
        "passed": not failures,
        "results": results,
        "failures": failures,
    }
