from __future__ import annotations

from pathlib import Path

import pytest

from wls.agentic_harness import AgenticHarness
from wls.config import default_config
from wls.evidence import EvidenceLedger
from wls.db import Database
from wls.runtime import LivingSystem
from wls.schemas import RiskLevel, TaskNodeStatus
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

    first = harness.acquire_ready_leases(graph_id, worker_id="worker-a", limit=2)
    second = harness.acquire_ready_leases(graph_id, worker_id="worker-b", limit=2)

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
    lease = harness.acquire_ready_leases(graph_id, worker_id="worker-a")[0]

    harness.fail_node(
        graph_id,
        lease.node_id,
        lease_id=lease.lease_id,
        error="fixture failure",
    )
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

    leases = harness.acquire_ready_leases(graph_id, worker_id="worker-a", limit=3)
    graph = harness.load_graph(graph_id)

    assert leases[0].node_id == "plan"
    harness.complete_node(
        graph_id,
        leases[0].node_id,
        lease_id=leases[0].lease_id,
        result={"ok": True},
    )
    waiting = harness.acquire_ready_leases(graph_id, worker_id="worker-a", limit=3)
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
