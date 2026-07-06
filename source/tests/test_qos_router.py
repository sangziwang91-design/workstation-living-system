from __future__ import annotations

from wls.qos_router import QoSRouter, QoSRouteDecision, WorkerScorecard


class TestQoSRouter:
    def test_route_selects_eligible(self):
        router = QoSRouter(cost_budget=10.0)
        router.record_scorecard(WorkerScorecard(
            "w1", "coding", 0.9, 2.0, 0.10, 100,
        ))
        decision = router.route(
            "task-1", "coding",
            candidates=[
                {"worker_id": "w1", "estimated_cost": 0.10, "requires_remote": False},
                {"worker_id": "w2", "estimated_cost": 0.20, "requires_remote": False},
            ],
        )
        assert decision.selected_worker == "w1"
        assert len(decision.fallback_workers) >= 1

    def test_privacy_ceiling_filters_remote(self):
        router = QoSRouter(cost_budget=10.0)
        decision = router.route(
            "task-1", "coding",
            candidates=[
                {"worker_id": "w1", "estimated_cost": 0.10, "requires_remote": True},
            ],
            privacy_ceiling="local",
        )
        assert decision.selected_worker == ""
        assert "no eligible" in decision.reason

    def test_budget_exhausted(self):
        router = QoSRouter(cost_budget=10.0)
        router.cost_spent = 9.9
        decision = router.route(
            "task-1", "coding",
            candidates=[
                {"worker_id": "w1", "estimated_cost": 1.0, "requires_remote": False},
            ],
        )
        assert decision.selected_worker == ""
        assert "no eligible" in decision.reason

    def test_exhaust_budget_response(self):
        router = QoSRouter(cost_budget=10.0)
        resp = router.exhaust_budget_response()
        assert resp.selected_worker == ""
        assert resp.budget_remaining == 0.0

    def test_circuit_break(self):
        router = QoSRouter()
        result = router.circuit_break("w1")
        assert "circuit_open" in result

    def test_latency_filter(self):
        router = QoSRouter(cost_budget=10.0)
        decision = router.route(
            "task-1", "coding",
            candidates=[
                {"worker_id": "slow", "estimated_cost": 0.10, "avg_latency_seconds": 60.0},
            ],
            max_latency=10.0,
        )
        assert decision.selected_worker == ""

    def test_decision_to_dict(self):
        d = QoSRouteDecision("d1", "t1", "w1", ["w2"], "good", 5.0)
        dd = d.to_dict()
        assert dd["selected_worker"] == "w1"
        assert dd["budget_remaining"] == 5.0
