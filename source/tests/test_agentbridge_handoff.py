"""WLS canonical graph authority refuses unverified AgentBridge handoff claims."""

from __future__ import annotations

from pathlib import Path

import pytest
from wls.agentic_mailbox import AgenticFileMailbox, ResultEnvelope
from wls.config import default_config
from wls.runtime import LivingSystem
from wls.schemas import TaskNodeStatus


def exported_node(tmp_path: Path):
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
    mailbox_root = tmp_path / "mailbox"
    exported = runtime.agentic.export_node_task_envelope(
        graph_id,
        lease.node_id,
        lease_id=lease.lease_id,
        mailbox_root=mailbox_root,
        recipient="agentbridge",
    )
    task = AgenticFileMailbox(mailbox_root).read_task(exported["message_id"])
    return runtime, task, mailbox_root


def candidate(task, *, message_id="ab-test", status="SUCCEEDED", **kwargs):
    payload = {
        "agentbridge_task_id": "WLS-TEST",
        "agentbridge_run_id": "RUN-REAL",
        "agentbridge_attempt_id": "ATT-REAL",
        "agentbridge_state": "COMPLETED",
        "verified_checks": [
            {"check_id": "WLS_FILE_1", "status": "PASS", "verifier_id": "file"}
        ],
        "checked_scope": "agentbridge_declared_acceptance_only",
        "lease_fencing_token": task.payload["lease_fencing_token"],
    }
    payload.update(kwargs.pop("payload", {}))
    return ResultEnvelope.create(
        message_id=message_id,
        in_reply_to=kwargs.pop("in_reply_to", task.message_id),
        graph_id=kwargs.pop("graph_id", task.graph_id),
        node_id=kwargs.pop("node_id", task.node_id),
        lease_id=kwargs.pop("lease_id", task.lease_id),
        sender="SZ-AgentBridge",
        recipient=kwargs.pop("recipient", "LivingSystem.AgenticHarness"),
        status=status,
        payload=payload,
    )


def test_wls_accepts_bound_verifiable_bridge_receipt(tmp_path):
    runtime, task, root = exported_node(tmp_path)
    result = candidate(task)
    mailbox = AgenticFileMailbox(root)
    mailbox.write_result(result)
    receipt = runtime.agentic.import_node_result_envelope(
        mailbox_root=root,
        message_id=result.message_id,
        acceptance_checks=[{
            "check_id": "bridge_verification_receipt",
            "type": "json_required_keys",
            "config": {"keys": ["verified_checks", "agentbridge_run_id"]},
        }],
    )
    assert receipt["receipt_type"] == "AGENTIC_RESULT_ENVELOPE_IMPORTED"
    assert receipt["completion"]["acceptance_report"]["passed"] is True
    assert runtime.agentic.load_graph(task.graph_id).nodes[task.node_id].status is (
        TaskNodeStatus.SUCCEEDED
    )


@pytest.mark.parametrize(("mutation", "reason"), [
    ({"in_reply_to": "msg-does-not-exist"}, "bridge_unknown_original_task"),
    ({"recipient": "other"}, "bridge_wrong_recipient"),
    ({"graph_id": "different-graph"}, "bridge_original_task_mismatch"),
    ({"payload": {"lease_fencing_token": None}}, "bridge_missing_or_invalid_fencing_token"),
    ({"payload": {"agentbridge_state": "READY"}}, "bridge_missing_verified_completion"),
    ({"payload": {"verified_checks": []}}, "bridge_missing_verified_completion"),
    ({"payload": {"verified_checks": [{"status": "FAIL"}]}}, "bridge_missing_verified_completion"),
    ({"payload": {"agentbridge_attempt_id": None}}, "bridge_missing_verified_completion"),
])
def test_wls_quarantines_unbound_or_forged_bridge_success(
    tmp_path, mutation, reason,
):
    runtime, task, root = exported_node(tmp_path)
    result = candidate(task, **mutation)
    mailbox = AgenticFileMailbox(root)
    mailbox.write_result(result)
    receipt = runtime.agentic.import_node_result_envelope(
        mailbox_root=root,
        message_id=result.message_id,
    )
    assert receipt["receipt_type"] == "AGENTIC_RESULT_ENVELOPE_QUARANTINED"
    assert receipt["reason"] == reason
    assert receipt["completion_attempted"] is False
    assert not (root / "results" / f"{result.message_id}.json").exists()
    assert (root / "rejected" / f"{result.message_id}.json").exists()
    assert runtime.agentic.load_graph(task.graph_id).nodes[task.node_id].status is (
        TaskNodeStatus.LEASED
    )


def test_wls_imports_failed_bridge_execution_as_failure_not_success(tmp_path):
    runtime, task, root = exported_node(tmp_path)
    result = candidate(
        task, status="FAILED", payload={
            "agentbridge_state": "RECOVERY_REQUIRED",
            "verified_checks": [],
            "error": "real worker failed",
        }
    )
    mailbox = AgenticFileMailbox(root)
    mailbox.write_result(result)
    receipt = runtime.agentic.import_node_result_envelope(
        mailbox_root=root,
        message_id=result.message_id,
    )
    assert receipt["receipt_type"] == "AGENTIC_RESULT_ENVELOPE_IMPORTED"
    assert runtime.agentic.load_graph(task.graph_id).nodes[task.node_id].status is (
        TaskNodeStatus.FAILED
    )
