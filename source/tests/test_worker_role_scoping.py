"""Role-specific worker authorization through WLS's existing canonical harness."""
from __future__ import annotations

import pytest

from wls.config import default_config
from wls.runtime import LivingSystem
from wls.schemas import RiskLevel, TaskDomain, TaskNodeStatus, digest_json
from wls.worker_registry import WorkerProfile


def test_specialist_role_is_enforced_at_arbitration_and_lease(tmp_path):
    runtime = LivingSystem(default_config(tmp_path / "home"))
    try:
        admitted = runtime.agentic.admit_and_compile(
            "Inspect source code and prepare an evidence-backed audit",
            acceptance=["inspection outcome is recorded"],
            evidence_required=["source evidence is recorded"],
        )
        graph_id = admitted["graph"]["graph_id"]
        graph = runtime.agentic.load_graph(graph_id)
        node = graph.ready_frontier()[0]
        other_role = "reviewer" if node.role != "reviewer" else "planner"
        registry = runtime.agentic.worker_registry
        domains = [domain.value for domain in TaskDomain]

        wrong = WorkerProfile(
            worker_id="review-only", worker_type="LOCAL_SHADOW",
            label="Review-only specialist", allowed_domains=domains,
            max_risk=RiskLevel.IRREVERSIBLE, allowed_roles=[other_role],
        )
        correct = WorkerProfile(
            worker_id="role-matched", worker_type="LOCAL_SHADOW",
            label="Role-matched specialist", allowed_domains=domains,
            max_risk=RiskLevel.IRREVERSIBLE, allowed_roles=[node.role],
        )
        registry.register_profile(wrong)
        registry.register_profile(correct)
        assert wrong.to_dict()["profile_digest"] != correct.to_dict()["profile_digest"]
        assert registry.profile("role-matched").allowed_roles == [node.role]

        with runtime.db.transaction() as connection:
            report = registry.propose_candidates_for_node(
                graph=graph, node=node,
                reason="require role-qualified independent worker",
                connection=connection,
            )
        rejected = {r["worker_id"]: r for r in report["rejected_workers"]}
        assert "role_not_allowed=" + node.role in rejected["review-only"]["reasons"]
        assert "role-matched" in {
            r["worker_id"] for r in report["eligible_workers"]
        }
        with pytest.raises(PermissionError, match="not allowed for role"):
            runtime.agentic.acquire_ready_leases(
                graph_id, worker_id="review-only",
                owner_authorized_node_ids={node.node_id},
            )
        assert runtime.agentic.load_graph(graph_id).nodes[node.node_id].status is (
            TaskNodeStatus.READY
        )

        leases = runtime.agentic.acquire_ready_leases(
            graph_id, worker_id="role-matched",
            owner_authorized_node_ids={node.node_id}, limit=1,
        )
        assert len(leases) == 1
        assert leases[0].worker_id == "role-matched"
        assert leases[0].node_id == node.node_id
        assert runtime.ledger.verify()[0]
    finally:
        runtime.db.close_all()


def test_legacy_wildcard_profile_digest_is_unchanged():
    profile = WorkerProfile(
        worker_id="legacy", worker_type="LOCAL_SHADOW",
        label="existing-worker", allowed_domains=["CODE"],
    )
    expected = digest_json({
        "worker_id": "legacy", "worker_type": "LOCAL_SHADOW",
        "allowed_domains": ["CODE"], "max_risk": RiskLevel.READ.value,
        "external_tools_enabled": False, "writes_enabled": False,
    })
    assert profile.to_dict()["profile_digest"] == expected
    assert profile.allowed_roles == []


@pytest.mark.parametrize("roles", [
    ["reviewer", "reviewer"], [""], ["role with spaces"], ["reviewer", 4],
])
def test_invalid_specialist_role_contract_fails_closed(roles):
    with pytest.raises(ValueError, match="allowed_roles"):
        WorkerProfile(
            worker_id="bad-role", worker_type="LOCAL_SHADOW",
            label="bad role", allowed_domains=["CODE"],
            allowed_roles=roles,
        )
