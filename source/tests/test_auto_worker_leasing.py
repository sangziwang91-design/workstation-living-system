"""Exercise role-aware automatic leasing through existing WLS graph, DB and ledger."""
from __future__ import annotations

import pytest

from wls.config import default_config
from wls.runtime import LivingSystem
from wls.schemas import RiskLevel, TaskDomain, TaskNodeStatus
from wls.worker_registry import WorkerProfile


def _register(runtime, role: str) -> str:
    worker_id = "specialist-" + role
    runtime.agentic.worker_registry.register_profile(WorkerProfile(
        worker_id=worker_id, worker_type="LOCAL_SHADOW",
        label=role + " specialist", allowed_domains=[d.value for d in TaskDomain],
        allowed_roles=[role], max_risk=RiskLevel.IRREVERSIBLE,
        metadata={"execution_mode": "shadow_receipt_only"},
    ))
    return worker_id


def test_auto_routing_selects_role_specialists_across_graph_stages(tmp_path):
    runtime = LivingSystem(default_config(tmp_path / "home"))
    try:
        receipt = runtime.agentic.admit_and_compile(
            "Publish release to an external system",
            acceptance=["owner approval is present"],
            evidence_required=["approval receipt"],
        )
        graph_id = receipt["graph"]["graph_id"]
        for role in ("planner", "executor", "reviewer"):
            _register(runtime, role)
        for role in ("planner", "executor", "reviewer"):
            graph = runtime.agentic.load_graph(graph_id)
            ready = graph.ready_frontier()
            assert len(ready) == 1
            assert ready[0].role == role
            lease = runtime.agentic.acquire_ready_leases_auto(
                graph_id, owner_authorized_node_ids={ready[0].node_id},
            )
            assert len(lease) == 1
            assert lease[0].node_id == ready[0].node_id
            assert lease[0].worker_id == "specialist-" + role
            # A second route attempt cannot acquire the same leased node.
            assert runtime.agentic.acquire_ready_leases_auto(
                graph_id, owner_authorized_node_ids={ready[0].node_id},
            ) == []
            runtime.agentic.complete_node(
                graph_id, lease[0].node_id,
                lease_id=lease[0].lease_id,
                result={"fixture": "node completion by test only"},
            )
        graph = runtime.agentic.load_graph(graph_id)
        assert graph.terminal()
        assert runtime.ledger.verify()[0]
        # A lease is not evidence of actual tool/model execution.
    finally:
        runtime.db.close_all()


def test_auto_routing_honors_approval_gate(tmp_path):
    runtime = LivingSystem(default_config(tmp_path / "home"))
    try:
        receipt = runtime.agentic.admit_and_compile(
            "Publish release to an external system",
            acceptance=["owner approval is present"],
            evidence_required=["approval receipt"],
        )
        graph_id = receipt["graph"]["graph_id"]
        _register(runtime, "planner")
        _register(runtime, "executor")
        plan = runtime.agentic.acquire_ready_leases_auto(graph_id)[0]
        runtime.agentic.complete_node(
            graph_id, plan.node_id, lease_id=plan.lease_id, result={"plan": True},
        )
        assert runtime.agentic.acquire_ready_leases_auto(graph_id) == []
        graph = runtime.agentic.load_graph(graph_id)
        assert graph.nodes["execute"].status is TaskNodeStatus.WAITING_APPROVAL
        assert runtime.ledger.verify()[0]
    finally:
        runtime.db.close_all()


def test_auto_routing_no_eligible_worker_keeps_ready_node(tmp_path):
    runtime = LivingSystem(default_config(tmp_path / "home"))
    try:
        receipt = runtime.agentic.admit_and_compile(
            "Inspect repository docs",
            acceptance=["inspection result is recorded"],
            evidence_required=["agentic_task_graph_compiled"],
        )
        graph_id = receipt["graph"]["graph_id"]
        registry = runtime.agentic.worker_registry
        for profile in registry.default_profiles():
            registry.record_heartbeat(profile.worker_id, status="DISABLED")
        _register(runtime, "a_nonmatching_role")
        assert runtime.agentic.acquire_ready_leases_auto(graph_id) == []
        graph = runtime.agentic.load_graph(graph_id)
        assert all(node.status is TaskNodeStatus.READY
                   for node in graph.ready_frontier())
        assert runtime.ledger.verify()[0]
    finally:
        runtime.db.close_all()


@pytest.mark.parametrize("kwargs", [
    {"limit": 0}, {"ttl_seconds": 0},
])
def test_auto_routing_rejects_invalid_budget_before_state(tmp_path, kwargs):
    runtime = LivingSystem(default_config(tmp_path / "home"))
    try:
        with pytest.raises(ValueError):
            runtime.agentic.acquire_ready_leases_auto("nonexistent", **kwargs)
        assert runtime.ledger.verify()[0]
    finally:
        runtime.db.close_all()
