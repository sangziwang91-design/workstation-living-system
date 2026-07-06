from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .schemas import digest_json, new_id, utc_now


@dataclass(slots=True)
class WorkerScorecard:
    worker_id: str
    task_class: str
    success_rate: float
    avg_latency_seconds: float
    avg_cost: float
    sample_count: int
    last_updated: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "worker_id": self.worker_id,
            "task_class": self.task_class,
            "success_rate": self.success_rate,
            "avg_latency_seconds": self.avg_latency_seconds,
            "avg_cost": self.avg_cost,
            "sample_count": self.sample_count,
        }


@dataclass(slots=True)
class QoSRouteDecision:
    decision_id: str
    task_id: str
    selected_worker: str
    fallback_workers: list[str]
    reason: str
    budget_remaining: float
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "task_id": self.task_id,
            "selected_worker": self.selected_worker,
            "fallback_workers": self.fallback_workers,
            "reason": self.reason,
            "budget_remaining": self.budget_remaining,
        }


class QoSRouter:
    """Selects workers using measured task-class performance under explicit
    budget, privacy, and latency constraints. Never chooses a provider
    without an eligible privacy ceiling.
    """

    def __init__(self, *, cost_budget: float = 100.0) -> None:
        self.cost_budget = cost_budget
        self.cost_spent: float = 0.0
        self._scorecards: dict[str, list[WorkerScorecard]] = {}

    def record_scorecard(self, card: WorkerScorecard) -> None:
        self._scorecards.setdefault(card.task_class, []).append(card)

    def route(
        self,
        task_id: str,
        task_class: str,
        *,
        candidates: list[dict[str, Any]],
        privacy_ceiling: str = "local",
        max_latency: float | None = None,
    ) -> QoSRouteDecision:
        available: list[dict[str, Any]] = []
        for c in candidates:
            if c.get("requires_remote") and privacy_ceiling == "local":
                continue
            if max_latency is not None and c.get("avg_latency_seconds", 0) > max_latency:
                continue
            if c.get("estimated_cost", 0) > (self.cost_budget - self.cost_spent):
                continue
            available.append(c)

        if not available:
            return QoSRouteDecision(
                decision_id=new_id("qos"),
                task_id=task_id,
                selected_worker="",
                fallback_workers=[],
                reason="no eligible workers within budget/privacy/latency constraints",
                budget_remaining=self.cost_budget - self.cost_spent,
            )

        scored = self._scorecards.get(task_class, [])
        scores_by_worker: dict[str, float] = {}
        for sc in scored:
            scores_by_worker[sc.worker_id] = sc.success_rate * 10 - sc.avg_cost

        available.sort(
            key=lambda c: scores_by_worker.get(c.get("worker_id", ""), -1),
            reverse=True,
        )

        selected = available[0]["worker_id"]
        fallback = [c["worker_id"] for c in available[1:3]]

        return QoSRouteDecision(
            decision_id=new_id("qos"),
            task_id=task_id,
            selected_worker=str(selected),
            fallback_workers=fallback,
            reason=f"selected by task-class performance score",
            budget_remaining=self.cost_budget - self.cost_spent,
        )

    def circuit_break(self, worker_id: str) -> str:
        return f"circuit_open:{worker_id}"

    def exhaust_budget_response(self) -> QoSRouteDecision:
        return QoSRouteDecision(
            decision_id=new_id("qos"),
            task_id="",
            selected_worker="",
            fallback_workers=[],
            reason="budget exhausted",
            budget_remaining=0.0,
        )
