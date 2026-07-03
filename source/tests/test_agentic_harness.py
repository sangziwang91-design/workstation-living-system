from __future__ import annotations

from pathlib import Path

import pytest

from wls.agentic_harness import AgenticHarness
from wls.config import default_config
from wls.evidence import EvidenceLedger
from wls.db import Database
from wls.runtime import LivingSystem
from wls.schemas import MemoryItem, RiskLevel, TaskNodeStatus
from wls.task_admission import TaskAdmissionClassifier
from wls.task_graph import TaskGraph, TaskNode
from wls.ui_projection import OwnerConsoleProductProjection


def _harness(tmp_path: Path) -> AgenticHarness:
    db = Database(tmp_path / "state.sqlite3")
    ledger = EvidenceLedger(db, tmp_path / "evidence.key")
    return AgenticHarness(db, ledger)


def test_task_admission_risk_floor_cannot_be_lowered_by_model_hint() -> None:
    intent = TaskAdmissionClassifier().admit(
        "Delete production records after sending the external release email",
        model_hints={"risk": "READ", "operation": "ANSWER"},
    )

    assert intent.risk_floor is RiskLevel.IRREVERSIBLE
    assert intent.owner_gate_required is True


def test_task_admission_rejects_missing_acceptance() -> None:
    with pytest.raises(ValueError, match="acceptance"):
        TaskAdmissionClassifier().admit(
            "inspect repository state",
            acceptance=[],
            evidence_required=["intent"],
        )


def test_task_graph_rejects_missing_dependencies_and_cycles() -> None:
    graph = TaskGraph(intent_id="intent")
    with pytest.raises(ValueError, match="missing dependencies"):
        graph.add_nodes([TaskNode("a", "A", "planner", ["done"], {"missing"})])

    graph = TaskGraph(intent_id="intent")
    with pytest.raises(ValueError, match="cycle"):
        graph.add_nodes(
            [
                TaskNode("a", "A", "planner", ["done"], {"b"}),
                TaskNode("b", "B", "planner", ["done"], {"a"}),
            ]
        )


def test_agentic_harness_persists_graph_and_reloads_after_restart(
    tmp_path: Path,
) -> None:
    harness = _harness(tmp_path)
    receipt = harness.admit_and_compile(
        "Inspect repository docs in parallel",
        acceptance=["inspection result is recorded"],
        evidence_required=["agentic_task_graph_compiled"],
        model_hints={"allow_parallel": True, "domain": "RESEARCH"},
    )
    graph_id = receipt["graph"]["graph_id"]

    restarted = AgenticHarness(harness.db, harness.ledger)
    graph = restarted.load_graph(graph_id)

    assert graph.graph_id == graph_id
    assert [node.node_id for node in graph.ready_frontier()] == ["scope"]
    assert receipt["context_manifest"]["graph_id"] == graph_id
    assert receipt["route"]["worker_registry"] == "LOCAL_PROFILE_REGISTRY_V1"


def test_agentic_harness_allows_one_active_lease_per_conflict_domain(
    tmp_path: Path,
) -> None:
    harness = _harness(tmp_path)
    receipt = harness.admit_and_compile(
        "Inspect repository docs in parallel",
        acceptance=["inspection result is recorded"],
        evidence_required=["agentic_task_graph_compiled"],
        model_hints={"allow_parallel": True, "domain": "RESEARCH"},
    )
    graph_id = receipt["graph"]["graph_id"]

    first = harness.acquire_ready_leases(
        graph_id, worker_id="readonly-inspector", limit=2
    )
    second = harness.acquire_ready_leases(
        graph_id, worker_id="planner-shadow", limit=2
    )

    assert len(first) == 1
    assert first[0].conflict_domain == "readonly"
    assert second == []


def test_agentic_harness_failure_blocks_dependents(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    receipt = harness.admit_and_compile(
        "Inspect repository docs in parallel",
        acceptance=["inspection result is recorded"],
        evidence_required=["agentic_task_graph_compiled"],
        model_hints={"allow_parallel": True, "domain": "RESEARCH"},
    )
    graph_id = receipt["graph"]["graph_id"]
    lease = harness.acquire_ready_leases(graph_id, worker_id="readonly-inspector")[0]

    harness.fail_node(
        graph_id,
        lease.node_id,
        lease_id=lease.lease_id,
        error="fixture failure",
    )
    failure_rows = harness.db.query_all("SELECT * FROM agentic_failure_attributions")
    assert len(failure_rows) == 1
    assert failure_rows[0]["failure_class"] == "LOCAL"
    graph = harness.load_graph(graph_id)

    assert graph.nodes["scope"].status is TaskNodeStatus.FAILED
    assert graph.nodes["gather"].status is TaskNodeStatus.BLOCKED
    assert graph.nodes["synthesize"].status is TaskNodeStatus.BLOCKED


def test_high_risk_node_waits_for_approval_without_lease(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    receipt = harness.admit_and_compile(
        "Publish release to an external system",
        acceptance=["owner approval is present"],
        evidence_required=["approval receipt"],
    )
    graph_id = receipt["graph"]["graph_id"]

    leases = harness.acquire_ready_leases(
        graph_id, worker_id="owner-gated-executor-shadow", limit=3
    )
    graph = harness.load_graph(graph_id)

    assert leases[0].node_id == "plan"
    harness.complete_node(
        graph_id,
        leases[0].node_id,
        lease_id=leases[0].lease_id,
        result={"ok": True},
    )
    waiting = harness.acquire_ready_leases(
        graph_id, worker_id="owner-gated-executor-shadow", limit=3
    )
    graph = harness.load_graph(graph_id)
    assert waiting == []
    assert graph.nodes["execute"].status is TaskNodeStatus.WAITING_APPROVAL


def test_living_system_exposes_agentic_tasks_without_second_authority(
    tmp_path: Path,
) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    receipt = runtime.agentic.admit_and_compile(
        "Inspect repository docs",
        acceptance=["inspection result is recorded"],
        evidence_required=["agentic_task_graph_compiled"],
    )

    status = runtime.status()
    panel = next(
        item
        for item in OwnerConsoleProductProjection().project(status)["panels"]
        if item["panel_id"] == "agentic_tasks"
    )

    assert status["agentic_task_receipts"][0]["graph_id"] == receipt["graph"]["graph_id"]
    assert panel["status"]["canonical_runtime"] == "LivingSystem"
    assert panel["status"]["second_authority_created"] is False
    assert panel["status"]["context_manifest_v1"]["receipt_count"] == 1
    assert panel["status"]["worker_registry_v1"]["receipt_count"] >= 3


def test_context_manifest_selects_scoped_canonical_records(tmp_path: Path) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    memory_id = runtime.memories.add(
        MemoryItem(
            memory_type="project_note",
            content={
                "project_ids": ["wls"],
                "summary": "repository context survives restart",
            },
            importance=0.8,
            confidence=0.9,
            source_ids=["test"],
            tags=["agentic_context"],
        )
    )

    receipt = runtime.agentic.admit_and_compile(
        "Inspect repository docs",
        acceptance=["inspection result is recorded"],
        evidence_required=["agentic_task_graph_compiled"],
    )
    records = receipt["context_manifest"]["records"]

    assert receipt["context_manifest"]["selection_policy"]["name"] == "CONTEXT_MANIFEST_V1"
    assert any(item["record_id"] == memory_id for item in records)
    assert runtime.status()["agentic_context_manifest_receipts"][0]["graph_id"] == receipt["graph"]["graph_id"]


def test_worker_registry_rejects_unknown_and_overrisk_workers(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    receipt = harness.admit_and_compile(
        "Publish release to an external system",
        acceptance=["owner approval is present"],
        evidence_required=["approval receipt"],
    )
    graph_id = receipt["graph"]["graph_id"]

    with pytest.raises(PermissionError, match="unknown or inactive worker"):
        harness.acquire_ready_leases(graph_id, worker_id="not-registered")

    low_risk_lease = harness.acquire_ready_leases(
        graph_id, worker_id="readonly-inspector", limit=1
    )[0]
    harness.complete_node(
        graph_id,
        low_risk_lease.node_id,
        lease_id=low_risk_lease.lease_id,
        result={"ok": True},
    )
    with pytest.raises(PermissionError, match="max risk"):
        harness.acquire_ready_leases(
            graph_id,
            worker_id="readonly-inspector",
            owner_authorized_node_ids={"execute"},
        )


def test_living_system_binds_leased_agentic_node_to_canonical_action(
    tmp_path: Path,
) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    receipt = runtime.agentic.admit_and_compile(
        "Inspect repository docs",
        acceptance=["inspection result is recorded"],
        evidence_required=["agentic_task_graph_compiled"],
    )
    graph_id = receipt["graph"]["graph_id"]
    lease = runtime.agentic.acquire_ready_leases(
        graph_id, worker_id="readonly-inspector"
    )[0]

    binding = runtime.bind_agentic_node_to_action(
        graph_id,
        lease.node_id,
        lease_id=lease.lease_id,
        reason="test policy-bound mapping",
    )

    assert binding["action_status"] == "PLANNED"
    assert binding["policy_decision"]["allowed"] is True
    assert binding["direct_tool_execution"] is False
    action = runtime.db.query_one(
        "SELECT * FROM actions WHERE action_id=?", (binding["action_id"],)
    )
    assert action is not None
    assert action["tool"] == "noop"
    assert action["status"] == "PLANNED"
    assert runtime.agentic.load_graph(graph_id).nodes[lease.node_id].status is TaskNodeStatus.LEASED
    panel = next(
        item
        for item in OwnerConsoleProductProjection().project(runtime.status())["panels"]
        if item["panel_id"] == "agentic_tasks"
    )
    assert panel["status"]["policy_bound_actions"]["receipt_count"] == 1


def test_agentic_node_action_binding_preserves_owner_gate_for_high_risk(
    tmp_path: Path,
) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    receipt = runtime.agentic.admit_and_compile(
        "Publish release to an external system",
        acceptance=["owner approval is present"],
        evidence_required=["approval receipt"],
    )
    graph_id = receipt["graph"]["graph_id"]
    plan_lease = runtime.agentic.acquire_ready_leases(
        graph_id, worker_id="owner-gated-executor-shadow"
    )[0]
    runtime.agentic.complete_node(
        graph_id,
        plan_lease.node_id,
        lease_id=plan_lease.lease_id,
        result={"ok": True},
    )
    execute_lease = runtime.agentic.acquire_ready_leases(
        graph_id,
        worker_id="owner-gated-executor-shadow",
        owner_authorized_node_ids={"execute"},
    )[0]

    binding = runtime.bind_agentic_node_to_action(
        graph_id, execute_lease.node_id, lease_id=execute_lease.lease_id
    )

    assert binding["action_status"] == "WAITING_APPROVAL"
    assert binding["policy_decision"]["requires_approval"] is True
    action = runtime.db.query_one(
        "SELECT status FROM actions WHERE action_id=?", (binding["action_id"],)
    )
    assert action["status"] == "WAITING_APPROVAL"


def test_living_system_executes_bound_readonly_agentic_node_action(
    tmp_path: Path,
) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    receipt = runtime.agentic.admit_and_compile(
        "Inspect repository docs",
        acceptance=["inspection result is recorded"],
        evidence_required=["agentic_task_graph_compiled"],
    )
    graph_id = receipt["graph"]["graph_id"]
    lease = runtime.agentic.acquire_ready_leases(
        graph_id, worker_id="readonly-inspector"
    )[0]
    binding = runtime.bind_agentic_node_to_action(
        graph_id,
        lease.node_id,
        lease_id=lease.lease_id,
    )

    execution = runtime.execute_bound_agentic_node_action(binding["binding_id"])

    assert execution["status"] == "SUCCEEDED"
    assert execution["execution_scope"] == "READ/no-side-effect shadow node action"
    graph = runtime.agentic.load_graph(graph_id)
    assert graph.nodes[lease.node_id].status is TaskNodeStatus.SUCCEEDED
    binding_row = runtime.db.query_one(
        "SELECT status FROM agentic_node_action_bindings WHERE binding_id=?",
        (binding["binding_id"],),
    )
    assert binding_row["status"] == "SUCCEEDED"


def test_bound_agentic_node_action_execution_rejects_owner_gated_binding(
    tmp_path: Path,
) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    receipt = runtime.agentic.admit_and_compile(
        "Publish release to an external system",
        acceptance=["owner approval is present"],
        evidence_required=["approval receipt"],
    )
    graph_id = receipt["graph"]["graph_id"]
    plan_lease = runtime.agentic.acquire_ready_leases(
        graph_id, worker_id="owner-gated-executor-shadow"
    )[0]
    runtime.agentic.complete_node(
        graph_id,
        plan_lease.node_id,
        lease_id=plan_lease.lease_id,
        result={"ok": True},
    )
    execute_lease = runtime.agentic.acquire_ready_leases(
        graph_id,
        worker_id="owner-gated-executor-shadow",
        owner_authorized_node_ids={"execute"},
    )[0]
    binding = runtime.bind_agentic_node_to_action(
        graph_id,
        execute_lease.node_id,
        lease_id=execute_lease.lease_id,
    )

    with pytest.raises(PermissionError, match="not executable"):
        runtime.execute_bound_agentic_node_action(binding["binding_id"])

    graph = runtime.agentic.load_graph(graph_id)
    assert graph.nodes[execute_lease.node_id].status is TaskNodeStatus.LEASED


def test_agentic_harness_epoch_audit_records_safety_invariants(
    tmp_path: Path,
) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    read_receipt = runtime.agentic.admit_and_compile(
        "Inspect repository docs",
        acceptance=["inspection result is recorded"],
        evidence_required=["agentic_task_graph_compiled"],
    )
    read_graph_id = read_receipt["graph"]["graph_id"]
    read_lease = runtime.agentic.acquire_ready_leases(
        read_graph_id, worker_id="readonly-inspector"
    )[0]
    read_binding = runtime.bind_agentic_node_to_action(
        read_graph_id,
        read_lease.node_id,
        lease_id=read_lease.lease_id,
    )
    runtime.execute_bound_agentic_node_action(read_binding["binding_id"])

    risky_receipt = runtime.agentic.admit_and_compile(
        "Publish release to an external system",
        acceptance=["owner approval is present"],
        evidence_required=["approval receipt"],
    )
    risky_graph_id = risky_receipt["graph"]["graph_id"]
    plan_lease = runtime.agentic.acquire_ready_leases(
        risky_graph_id, worker_id="owner-gated-executor-shadow"
    )[0]
    runtime.agentic.complete_node(
        risky_graph_id,
        plan_lease.node_id,
        lease_id=plan_lease.lease_id,
        result={"ok": True},
    )
    execute_lease = runtime.agentic.acquire_ready_leases(
        risky_graph_id,
        worker_id="owner-gated-executor-shadow",
        owner_authorized_node_ids={"execute"},
    )[0]
    runtime.bind_agentic_node_to_action(
        risky_graph_id,
        execute_lease.node_id,
        lease_id=execute_lease.lease_id,
    )

    audit = runtime.record_agentic_harness_epoch_audit(
        reason="unit test agentic harness epoch audit"
    )

    assert audit["status"] == "PASS"
    assert audit["receipt_counts"]["task_graph"] == 2
    assert audit["receipt_counts"]["node_action_binding"] == 2
    assert all(audit["invariants"].values())
    panel = next(
        item
        for item in OwnerConsoleProductProjection().project(runtime.status())["panels"]
        if item["panel_id"] == "agentic_tasks"
    )
    assert panel["status"]["harness_epoch_audit"]["receipt_count"] == 1


def test_failure_attribution_classifies_policy_and_environment(
    tmp_path: Path,
) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    receipt = runtime.agentic.admit_and_compile(
        "Inspect repository docs in parallel",
        acceptance=["inspection result is recorded"],
        evidence_required=["agentic_task_graph_compiled"],
        model_hints={"allow_parallel": True, "domain": "RESEARCH"},
    )
    graph_id = receipt["graph"]["graph_id"]
    lease = runtime.agentic.acquire_ready_leases(
        graph_id, worker_id="readonly-inspector"
    )[0]

    payload = runtime.agentic.fail_node(
        graph_id,
        lease.node_id,
        lease_id=lease.lease_id,
        error="PermissionError: policy approval missing",
    )

    assert payload["failure_attribution"]["failure_class"] == "POLICY"
    receipts = runtime.status()["agentic_failure_attribution_receipts"]
    assert receipts[0]["failure_class"] == "POLICY"
