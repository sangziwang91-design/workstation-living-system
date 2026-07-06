from __future__ import annotations

from wls.graph_recovery import (
    GraphRecoveryEngine,
    RecoveryAction,
    SideEffectRecord,
)
from wls.schemas import TaskNodeStatus


class TestGraphRecovery:
    def test_succeeded_node_skipped(self):
        engine = GraphRecoveryEngine()
        d = engine.assess_node("n1", TaskNodeStatus.SUCCEEDED.value)
        assert d.action == RecoveryAction.SKIP_RECOVERED

    def test_leased_with_unknown_side_effect_quarantined(self):
        engine = GraphRecoveryEngine()
        d = engine.assess_node(
            "n1", TaskNodeStatus.LEASED.value,
            has_side_effect=True, side_effect_status="UNKNOWN",
        )
        assert d.action == RecoveryAction.QUARANTINE

    def test_leased_confirmed_side_effect_replayed(self):
        engine = GraphRecoveryEngine()
        d = engine.assess_node(
            "n1", TaskNodeStatus.LEASED.value,
            has_side_effect=True, side_effect_status="CONFIRMED",
        )
        assert d.action == RecoveryAction.REPLAY

    def test_leased_no_side_effect_replayed(self):
        engine = GraphRecoveryEngine()
        d = engine.assess_node("n1", TaskNodeStatus.LEASED.value)
        assert d.action == RecoveryAction.REPLAY

    def test_failed_escalated(self):
        engine = GraphRecoveryEngine()
        d = engine.assess_node("n1", TaskNodeStatus.FAILED.value)
        assert d.action == RecoveryAction.ESCALATE

    def test_pending_replayed(self):
        engine = GraphRecoveryEngine()
        d = engine.assess_node("n1", TaskNodeStatus.PENDING.value)
        assert d.action == RecoveryAction.REPLAY

    def test_recover_graph_full(self):
        engine = GraphRecoveryEngine()
        se = SideEffectRecord(
            record_id="se1", graph_id="g1", node_id="n2",
            side_effect_class="write", idempotency_key="ik1",
            status="UNKNOWN", dispatched_at="2026-01-01T00:00:00Z",
        )
        decisions = engine.recover_graph(
            "g1",
            {"n1": "SUCCEEDED", "n2": "LEASED", "n3": "FAILED", "n4": "PENDING"},
            {"n2": se},
        )
        assert len(decisions) == 4
        actions = {d.node_id: d.action for d in decisions}
        assert actions["n1"] == RecoveryAction.SKIP_RECOVERED
        assert actions["n2"] == RecoveryAction.QUARANTINE
        assert actions["n3"] == RecoveryAction.ESCALATE
        assert actions["n4"] == RecoveryAction.REPLAY

    def test_side_effect_is_unknown(self):
        se = SideEffectRecord(
            record_id="se1", graph_id="g1", node_id="n1",
            side_effect_class="write", idempotency_key="ik1",
            status="UNKNOWN", dispatched_at="2026-01-01T00:00:00Z",
        )
        assert se.is_unknown
