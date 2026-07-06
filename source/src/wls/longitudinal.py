from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .schemas import digest_json, new_id, utc_now


@dataclass(slots=True)
class LongitudinalProtocol:
    protocol_id: str
    host_id: str
    baseline_commit: str
    config_digest: str
    started_at: str
    duration_days: int = 30
    measurement_interval_hours: int = 24
    frozen_baseline: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_id": self.protocol_id,
            "host_id": self.host_id,
            "baseline_commit": self.baseline_commit,
            "config_digest": self.config_digest,
            "duration_days": self.duration_days,
            "frozen_baseline": self.frozen_baseline,
        }


@dataclass(slots=True)
class MeasurementPoint:
    point_id: str
    protocol_id: str
    task_class: str
    success: bool
    correction_count: int
    cost: float
    latency_seconds: float
    memory_benefit: bool
    skill_reuse: bool
    recorded_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "point_id": self.point_id,
            "task_class": self.task_class,
            "success": self.success,
            "correction_count": self.correction_count,
            "cost": self.cost,
            "latency_seconds": self.latency_seconds,
            "memory_benefit": self.memory_benefit,
            "skill_reuse": self.skill_reuse,
            "recorded_at": self.recorded_at,
        }


@dataclass(slots=True)
class LongitudinalReport:
    report_id: str
    protocol: LongitudinalProtocol
    measurements: list[MeasurementPoint]
    success_rate: float
    avg_correction: float
    total_cost: float
    skill_reuse_rate: float
    memory_benefit_rate: float
    regressions: list[str]
    claim_ceiling: str
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "protocol_id": self.protocol.protocol_id,
            "host": self.protocol.host_id,
            "baseline_commit": self.protocol.baseline_commit,
            "success_rate": self.success_rate,
            "avg_correction": self.avg_correction,
            "total_cost": self.total_cost,
            "skill_reuse_rate": self.skill_reuse_rate,
            "memory_benefit_rate": self.memory_benefit_rate,
            "regressions": self.regressions,
            "measurement_count": len(self.measurements),
            "claim_ceiling": self.claim_ceiling,
            "created_at": self.created_at,
        }


class LongitudinalEvaluator:
    """Owner-host longitudinal proof engine: runs genuine owner-authorized
    missions over weeks, compares against frozen baselines, and releases
    only workload-specific claims.
    """

    def start_protocol(
        self,
        host_id: str,
        baseline_commit: str,
        config: dict[str, Any],
        *,
        duration_days: int = 30,
    ) -> LongitudinalProtocol:
        return LongitudinalProtocol(
            protocol_id=new_id("long"),
            host_id=host_id,
            baseline_commit=baseline_commit,
            config_digest=digest_json(config),
            started_at=utc_now(),
            duration_days=duration_days,
        )

    def record_measurement(
        self,
        protocol: LongitudinalProtocol,
        task_class: str,
        success: bool,
        *,
        corrections: int = 0,
        cost: float = 0.0,
        latency: float = 0.0,
        memory_benefit: bool = False,
        skill_reuse: bool = False,
    ) -> MeasurementPoint:
        return MeasurementPoint(
            point_id=new_id("meas"),
            protocol_id=protocol.protocol_id,
            task_class=task_class,
            success=success,
            correction_count=corrections,
            cost=cost,
            latency_seconds=latency,
            memory_benefit=memory_benefit,
            skill_reuse=skill_reuse,
        )

    def compile_report(
        self,
        protocol: LongitudinalProtocol,
        measurements: list[MeasurementPoint],
        *,
        baseline_success_rate: float = 1.0,
        max_regression: float = 0.1,
        claim_ceiling: str = "",
    ) -> LongitudinalReport:
        total = len(measurements)
        if total == 0:
            return LongitudinalReport(
                report_id=new_id("long_report"),
                protocol=protocol,
                measurements=[],
                success_rate=0.0,
                avg_correction=0.0,
                total_cost=0.0,
                skill_reuse_rate=0.0,
                memory_benefit_rate=0.0,
                regressions=[],
                claim_ceiling="no measurements recorded",
            )

        success_rate = sum(1 for m in measurements if m.success) / total
        avg_correction = sum(m.correction_count for m in measurements) / total
        total_cost = sum(m.cost for m in measurements)
        skill_reuse_rate = sum(1 for m in measurements if m.skill_reuse) / total
        memory_benefit_rate = sum(1 for m in measurements if m.memory_benefit) / total

        regressions: list[str] = []
        if success_rate < baseline_success_rate - max_regression:
            regressions.append(
                f"success_rate regression: {success_rate:.2f} vs baseline {baseline_success_rate:.2f}"
            )

        return LongitudinalReport(
            report_id=new_id("long_report"),
            protocol=protocol,
            measurements=measurements,
            success_rate=round(success_rate, 4),
            avg_correction=round(avg_correction, 4),
            total_cost=round(total_cost, 4),
            skill_reuse_rate=round(skill_reuse_rate, 4),
            memory_benefit_rate=round(memory_benefit_rate, 4),
            regressions=regressions,
            claim_ceiling=claim_ceiling or "claims limited to observed workloads",
        )
